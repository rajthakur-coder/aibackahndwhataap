import json

import requests
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import AsyncSessionLocal
from app.models.ecommerce import (
    ContactStoreMapping,
    EcommerceConnection,
    ShopifyCatalogCollection,
    ShopifyCatalogDefaultCategory,
)
from app.modules.ecommerce.shared.core_service import (
    _normalize_product,
    bootstrap_shopify_connection,
    fetch_all_products,
    fetch_shopify_collections,
    fetch_shopify_collects,
    find_shopify_connection_by_domain,
    mark_shopify_webhook_event,
    record_shopify_webhook_event,
    set_shopify_webhook_request_id,
    verify_shopify_hmac,
)
from app.modules.ecommerce.shared.serializers import serialize_ecommerce_connection
from app.modules.ecommerce.orders.order_service import upsert_contact_store_mapping
from app.shared.redis import get_redis


def connection_or_404(db: Session, connection_id: int, tenant_id: str | None = None) -> EcommerceConnection:
    connection = db.execute(
        select(EcommerceConnection).where(EcommerceConnection.id == connection_id)
    ).scalars().first()
    if not connection or (tenant_id is not None and connection.tenant_id != tenant_id):
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")
    return connection


def db_bool(value: bool) -> str:
    return "true" if value else "false"


def is_db_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_CATALOG_CATEGORIES = [
    {
        "category_key": "all",
        "title": "All products",
        "description": "Browse the full catalog",
        "sort_order": 0,
    },
    {
        "category_key": "best_sellers",
        "title": "Best sellers",
        "description": "Popular products",
        "sort_order": 1,
    },
]


async def clear_shopify_catalog_cache(connection_id: int) -> None:
    try:
        redis = await get_redis()
        patterns = [
            f"shopify:collections:*:{connection_id}",
            f"shopify:collection-index:*:{connection_id}",
            f"shopify:products:all:*:{connection_id}",
            f"shopify:top-selling:*:{connection_id}:*",
            f"shopify:fbt:*:{connection_id}",
            f"shopify:categories:*:{connection_id}:*",
            f"shopify:category:*:{connection_id}:*",
            f"shopify:query:*:{connection_id}:*",
            f"shopify:cross-sell:*:{connection_id}:*",
        ]
        for pattern in patterns:
            batch = []
            async for key in redis.scan_iter(match=pattern, count=100):
                batch.append(key)
                if len(batch) >= 100:
                    await redis.delete(*batch)
                    batch = []
            if batch:
                await redis.delete(*batch)
    except Exception:
        return


def shopify_collection_rows(connection: EcommerceConnection) -> list[dict]:
    collections = fetch_shopify_collections(connection, 250)
    collects = fetch_shopify_collects(connection, 5000)
    products = fetch_all_products(connection, 5000)
    in_stock_product_ids = {
        str(normalized.get("shopify_product_id") or normalized.get("external_id") or "")
        for normalized in (_normalize_product(connection, product) for product in products)
        if normalized.get("status") == "active" and normalized.get("in_stock")
    }
    counts: dict[str, int] = {}
    for collect in collects:
        product_id = str(collect.get("product_id") or "")
        if product_id not in in_stock_product_ids:
            continue
        collection_id = str(collect.get("collection_id") or "")
        if collection_id:
            counts[collection_id] = counts.get(collection_id, 0) + 1
    rows = []
    for collection in collections:
        collection_id = str(collection.get("id") or "")
        if not collection_id:
            continue
        product_count = counts.get(collection_id, 0)
        if product_count <= 0:
            continue
        rows.append(
            {
                "shopify_collection_id": collection_id,
                "title": collection.get("title") or collection.get("handle") or collection_id,
                "handle": collection.get("handle"),
                "product_count": product_count,
            }
        )
    return sorted(rows, key=lambda row: (-int(row["product_count"] or 0), str(row["title"]).lower()))


def serialize_contact_store_mapping(row: ContactStoreMapping) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "phone": row.phone,
        "normalized_phone": row.normalized_phone,
        "connection_id": row.connection_id,
        "source": row.source,
        "status": row.status,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


async def bootstrap_shopify_connection_background(connection_id: int) -> None:
    async with AsyncSessionLocal() as db:
        def sync_op(sync_db: Session):
            connection = connection_or_404(sync_db, connection_id)
            connection.status = "syncing"
            sync_db.commit()
            bootstrap_shopify_connection(sync_db, connection)

        await db.run_sync(sync_op)


def shopify_webhook_context(
    db: Session,
    raw_body: bytes,
    headers,
    request_id: str | None = None,
) -> tuple[EcommerceConnection, dict, object]:
    if not verify_shopify_hmac(raw_body, headers.get("X-Shopify-Hmac-Sha256")):
        raise HTTPException(status_code=401, detail="Invalid Shopify webhook signature")

    shop_domain = headers.get("X-Shopify-Shop-Domain")
    topic = headers.get("X-Shopify-Topic") or "unknown"
    if not shop_domain:
        raise HTTPException(status_code=400, detail="X-Shopify-Shop-Domain header is required")

    connection = find_shopify_connection_by_domain(db, shop_domain)
    if not connection:
        raise HTTPException(status_code=404, detail="Shopify ecommerce connection not found")

    event, already_processed = record_shopify_webhook_event(
        db,
        connection,
        shop_domain,
        topic,
        headers.get("X-Shopify-Webhook-Id"),
        raw_body,
    )
    if already_processed:
        return connection, {"_duplicate": True}, event
    set_shopify_webhook_request_id(db, event, request_id)

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        mark_shopify_webhook_event(db, event, "failed", str(exc))
        raise HTTPException(status_code=400, detail="Invalid Shopify webhook JSON") from exc
    return connection, body, event


def upsert_manual_contact_store_mapping(
    db: Session,
    connection_id: int,
    body: dict,
    tenant_id: str | None = None,
) -> dict:
    connection = connection_or_404(db, connection_id, tenant_id)
    mapping = upsert_contact_store_mapping(
        db,
        connection,
        str(body.get("phone") or body.get("customer_phone_number") or ""),
        source=str(body.get("source") or "manual"),
    )
    if not mapping:
        raise HTTPException(status_code=400, detail="phone is required")
    db.commit()
    db.refresh(mapping)
    return {
        "status": "success",
        "connection_id": connection.id,
        "mapping": serialize_contact_store_mapping(mapping),
    }


def shopify_catalog_collections_payload(db: Session, connection_id: int, tenant_id: str | None = None) -> dict:
    connection = connection_or_404(db, connection_id, tenant_id)
    if connection.platform != "shopify":
        raise HTTPException(status_code=400, detail="Collections are only available for Shopify")

    default_categories = shopify_default_catalog_categories_payload(db, connection)
    has_default_preferences = _has_saved_default_catalog_preferences(db, connection.id)
    saved_rows = db.execute(
        select(ShopifyCatalogCollection).where(
            ShopifyCatalogCollection.connection_id == connection.id
        )
    ).scalars().all()
    saved_by_id = {str(row.shopify_collection_id): row for row in saved_rows}

    try:
        collections = shopify_collection_rows(connection)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    has_saved_preferences = bool(saved_rows)
    data = []
    for index, collection in enumerate(collections):
        saved = saved_by_id.get(collection["shopify_collection_id"])
        visible = is_db_true(saved.visible) if saved else not has_saved_preferences
        sort_order = saved.sort_order if saved else index
        if not has_default_preferences:
            sort_order = int(sort_order or 0) + len(DEFAULT_CATALOG_CATEGORIES)
        data.append(
            {
                **collection,
                "visible": visible,
                "sort_order": sort_order,
            }
        )
    return {
        "status": "success",
        "connection_id": connection.id,
        "default_categories": default_categories,
        "collections": data,
    }


def update_shopify_catalog_collections_payload(
    db: Session,
    connection_id: int,
    data,
    tenant_id: str | None = None,
) -> dict:
    connection = connection_or_404(db, connection_id, tenant_id)
    if connection.platform != "shopify":
        raise HTTPException(status_code=400, detail="Collections are only available for Shopify")

    try:
        live_collections = shopify_collection_rows(connection)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    live_by_id = {row["shopify_collection_id"]: row for row in live_collections}
    selected_by_id = {
        str(row.shopify_collection_id): row
        for row in data.collections
        if str(row.shopify_collection_id) in live_by_id
    }

    db.execute(
        delete(ShopifyCatalogDefaultCategory).where(
            ShopifyCatalogDefaultCategory.connection_id == connection.id
        )
    )
    default_by_key = {row["category_key"]: row for row in DEFAULT_CATALOG_CATEGORIES}
    selected_defaults = {
        str(row.category_key): row
        for row in data.default_categories
        if str(row.category_key) in default_by_key
    }
    if not selected_defaults and not data.default_categories:
        selected_defaults = {
            row["category_key"]: type(
                "DefaultSelection",
                (),
                {
                    "category_key": row["category_key"],
                    "visible": True,
                    "sort_order": row["sort_order"],
                },
            )()
            for row in DEFAULT_CATALOG_CATEGORIES
        }
    for fallback_index, (category_key, selection) in enumerate(selected_defaults.items()):
        default = default_by_key[category_key]
        db.add(
            ShopifyCatalogDefaultCategory(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                category_key=category_key,
                title=default["title"],
                description=default["description"],
                visible=db_bool(selection.visible),
                sort_order=selection.sort_order if selection.sort_order is not None else fallback_index,
            )
        )

    db.execute(
        delete(ShopifyCatalogCollection).where(
            ShopifyCatalogCollection.connection_id == connection.id
        )
    )
    for fallback_index, (collection_id, selection) in enumerate(selected_by_id.items()):
        live = live_by_id[collection_id]
        db.add(
            ShopifyCatalogCollection(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                shopify_collection_id=collection_id,
                title=live["title"],
                handle=live.get("handle"),
                product_count=live.get("product_count") or 0,
                visible=db_bool(selection.visible),
                sort_order=selection.sort_order if selection.sort_order is not None else fallback_index,
            )
        )
    db.commit()
    return {
        "status": "success",
        "connection_id": connection.id,
        "default_saved": len(selected_defaults),
        "saved": len(selected_by_id),
    }


def _has_saved_default_catalog_preferences(db: Session, connection_id: int) -> bool:
    return bool(
        db.execute(
            select(ShopifyCatalogDefaultCategory.id).where(
                ShopifyCatalogDefaultCategory.connection_id == connection_id
            )
        ).first()
    )


def shopify_default_catalog_categories_payload(
    db: Session,
    connection: EcommerceConnection,
) -> list[dict]:
    saved_rows = db.execute(
        select(ShopifyCatalogDefaultCategory).where(
            ShopifyCatalogDefaultCategory.connection_id == connection.id
        )
    ).scalars().all()
    saved_by_key = {str(row.category_key): row for row in saved_rows}
    has_saved_preferences = bool(saved_rows)
    rows = []
    for index, default in enumerate(DEFAULT_CATALOG_CATEGORIES):
        saved = saved_by_key.get(default["category_key"])
        rows.append(
            {
                "category_key": default["category_key"],
                "title": default["title"],
                "description": default["description"],
                "visible": is_db_true(saved.visible) if saved else True,
                "sort_order": saved.sort_order if saved else index,
            }
        )
    if has_saved_preferences:
        rows.sort(key=lambda row: (int(row.get("sort_order") or 0), row["title"].lower()))
    return rows
