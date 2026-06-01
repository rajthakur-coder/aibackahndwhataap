from app.modules.ecommerce.catalog.catalog_cache_service import (
    find_cached_catalog_categories,
    find_cached_catalog_products,
    find_cached_category_products,
    find_cached_cross_sell_products,
    find_cached_default_catalog_categories,
    find_cached_order_status,
    find_cached_product_image,
    find_cached_product_recommendations,
    find_cached_top_selling_products,
    is_catalog_request,
    is_image_request,
)

find_cached_shopify_catalog_categories = find_cached_catalog_categories
find_cached_shopify_catalog_products = find_cached_catalog_products
find_cached_shopify_category_products = find_cached_category_products
find_cached_shopify_cross_sell_products = find_cached_cross_sell_products
find_cached_shopify_default_catalog_categories = find_cached_default_catalog_categories
find_cached_shopify_order_status = find_cached_order_status
find_cached_shopify_product_image = find_cached_product_image
find_cached_shopify_product_recommendations = find_cached_product_recommendations
find_cached_shopify_top_selling_products = find_cached_top_selling_products

__all__ = [
    "find_cached_catalog_categories",
    "find_cached_catalog_products",
    "find_cached_category_products",
    "find_cached_cross_sell_products",
    "find_cached_default_catalog_categories",
    "find_cached_order_status",
    "find_cached_product_image",
    "find_cached_product_recommendations",
    "find_cached_top_selling_products",
    "find_cached_shopify_catalog_categories",
    "find_cached_shopify_catalog_products",
    "find_cached_shopify_category_products",
    "find_cached_shopify_cross_sell_products",
    "find_cached_shopify_default_catalog_categories",
    "find_cached_shopify_order_status",
    "find_cached_shopify_product_image",
    "find_cached_shopify_product_recommendations",
    "find_cached_shopify_top_selling_products",
    "is_catalog_request",
    "is_image_request",
]
