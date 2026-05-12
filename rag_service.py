import hashlib
import json
import math
import re
from collections import Counter

from sqlalchemy.orm import Session

from ecommerce_service import product_knowledge_text
from models import EcommerceProduct, KnowledgeChunk, KnowledgeDocument, ScrapedChunk, ScrapedData
from pinecone_service import query_context, upsert_chunks


CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
MAX_CHUNKS_PER_DOCUMENT = 80
MAX_RETRIEVED_CHUNKS = 5
MAX_CONTEXT_CHARS = 6000
EMBEDDING_DIMENSIONS = 64

TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "ka",
    "ke",
    "ki",
    "ko",
    "me",
    "of",
    "on",
    "or",
    "please",
    "the",
    "to",
    "what",
    "when",
    "where",
    "with",
    "you",
}
IMAGE_REQUEST_TERMS = {
    "image",
    "images",
    "photo",
    "photos",
    "pic",
    "pics",
    "picture",
    "pictures",
    "img",
    "tasveer",
    "tasvir",
    "dikha",
    "dika",
    "dikhai",
    "dikhaye",
    "dikhana",
    "dikhao",
    "bhejo",
}
CATALOG_REQUEST_TERMS = {
    "catalog",
    "catalogue",
    "products",
    "product",
    "collection",
    "collections",
    "items",
    "list",
    "menu",
    "offering",
    "offerings",
    "range",
    "service",
    "services",
    "experience",
    "experiences",
}
REQUEST_ACTION_TERMS = {
    "bhejo",
    "catalog",
    "catalogue",
    "de",
    "dekhna",
    "dikha",
    "dika",
    "dikhai",
    "dikhana",
    "dikhao",
    "do",
    "send",
    "show",
}
IMAGE_URL_RE = re.compile(r"https?://[^\s,]+")


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOP_WORDS
    ]


def _embedding_for_text(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign

    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        return vector

    return [value / length for value in vector]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(left[index] * right[index] for index in range(len(left)))


def _load_embedding(value: str | None) -> list[float]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def chunk_text(content: str) -> list[str]:
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    if not content:
        return []

    chunks = []
    start = 0
    while start < len(content) and len(chunks) < MAX_CHUNKS_PER_DOCUMENT:
        end = min(start + CHUNK_SIZE, len(content))
        if end < len(content):
            split_at = max(content.rfind("\n", start, end), content.rfind(" ", start, end))
            if split_at > start + (CHUNK_SIZE // 2):
                end = split_at

        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(content):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)

    return chunks


def save_scraped_chunks(db: Session, scraped_data: ScrapedData) -> dict:
    db.query(ScrapedChunk).filter(
        ScrapedChunk.scraped_data_id == scraped_data.id,
    ).delete()

    chunks = chunk_text(scraped_data.content)
    for index, chunk in enumerate(chunks):
        db.add(
            ScrapedChunk(
                scraped_data_id=scraped_data.id,
                url=scraped_data.url,
                chunk_index=index,
                content=chunk,
            )
        )

    db.commit()
    pinecone_upserted = _upsert_scraped_chunks_to_pinecone(scraped_data, chunks)
    return {
        "chunks": len(chunks),
        "pinecone_upserted": pinecone_upserted,
    }


def save_knowledge_document(
    db: Session,
    title: str,
    content: str,
    source: str | None = None,
) -> KnowledgeDocument:
    document = KnowledgeDocument(
        title=title.strip() or "Untitled document",
        source=source,
        content=content.strip(),
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    save_knowledge_chunks(db, document)
    return document


def save_knowledge_chunks(db: Session, document: KnowledgeDocument) -> dict:
    db.query(KnowledgeChunk).filter(
        KnowledgeChunk.document_id == document.id,
    ).delete()

    chunks = chunk_text(document.content)
    for index, chunk in enumerate(chunks):
        db.add(
            KnowledgeChunk(
                document_id=document.id,
                title=document.title,
                source=document.source,
                chunk_index=index,
                content=chunk,
                embedding=json.dumps(_embedding_for_text(chunk)),
            )
        )

    db.commit()
    pinecone_upserted = _upsert_knowledge_chunks_to_pinecone(document, chunks)
    return {
        "chunks": len(chunks),
        "pinecone_upserted": pinecone_upserted,
    }


def _upsert_scraped_chunks_to_pinecone(scraped_data: ScrapedData, chunks: list[str]) -> int:
    vectors = [
        {
            "id": f"scraped-{scraped_data.id}-{index}",
            "document_id": scraped_data.id,
            "source_type": "website",
            "source": scraped_data.url,
            "title": scraped_data.url,
            "chunk_index": index,
            "content": chunk,
        }
        for index, chunk in enumerate(chunks)
    ]

    try:
        return upsert_chunks(vectors)
    except Exception as exc:
        print("PINECONE UPSERT ERROR:", exc)
        return 0


def _upsert_knowledge_chunks_to_pinecone(document: KnowledgeDocument, chunks: list[str]) -> int:
    vectors = [
        {
            "id": f"knowledge-{document.id}-{index}",
            "document_id": document.id,
            "source_type": "document",
            "source": document.source,
            "title": document.title,
            "chunk_index": index,
            "content": chunk,
        }
        for index, chunk in enumerate(chunks)
    ]

    try:
        return upsert_chunks(vectors)
    except Exception as exc:
        print("PINECONE UPSERT ERROR:", exc)
        return 0


def _score_chunk(query_terms: Counter, chunk: ScrapedChunk) -> float:
    chunk_terms = Counter(_tokens(chunk.content))
    if not chunk_terms:
        return 0.0

    score = 0.0
    for term, query_count in query_terms.items():
        term_count = chunk_terms.get(term, 0)
        if term_count:
            score += (1 + math.log(term_count)) * query_count

    if score == 0:
        return 0.0

    return score / math.sqrt(sum(count * count for count in chunk_terms.values()))


def _score_knowledge_chunk(
    query_terms: Counter,
    query_embedding: list[float],
    chunk: KnowledgeChunk,
) -> float:
    lexical_score = _score_chunk(query_terms, chunk)
    vector_score = _cosine_similarity(query_embedding, _load_embedding(chunk.embedding))
    return lexical_score + max(vector_score, 0.0)


def is_image_request(query: str) -> bool:
    return bool(set(_tokens(query)) & IMAGE_REQUEST_TERMS)


def is_catalog_request(query: str) -> bool:
    terms = set(_tokens(query))
    return bool(terms & CATALOG_REQUEST_TERMS and terms & REQUEST_ACTION_TERMS)


def _product_image_urls(product: EcommerceProduct) -> list[str]:
    try:
        image_urls = json.loads(product.image_urls or "[]")
    except json.JSONDecodeError:
        return []
    return [url for url in image_urls if isinstance(url, str) and url.startswith("http")]


def _product_caption(product: EcommerceProduct) -> str:
    caption_parts = [product.title]
    if product.price_min:
        price = product.price_min
        if product.price_max and product.price_max != product.price_min:
            price = f"{product.price_min} - {product.price_max}"
        caption_parts.append(f"Price: {price}")
    if product.product_url:
        caption_parts.append(product.product_url)
    return "\n".join(caption_parts)


def _scraped_image_urls(content: str) -> list[str]:
    match = re.search(r"^Images:\s*(.+)$", content or "", flags=re.MULTILINE)
    if not match:
        return []

    urls = []
    for raw_url in IMAGE_URL_RE.findall(match.group(1)):
        url = raw_url.rstrip(").]")
        if url.startswith("http"):
            urls.append(url)
    return list(dict.fromkeys(urls))


def _scraped_title(content: str, fallback: str) -> str:
    match = re.search(r"^Page title:\s*(.+)$", content or "", flags=re.MULTILINE)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return fallback


def find_relevant_catalog_products(db: Session, query: str, limit: int = 5) -> list[dict]:
    if not is_catalog_request(query):
        return []

    query_terms = Counter(_tokens(query))
    products = db.query(EcommerceProduct).order_by(EcommerceProduct.updated_at.desc()).limit(400).all()
    scored = []
    for product in products:
        score = _score_chunk(
            query_terms,
            ScrapedChunk(
                scraped_data_id=0,
                url=product.product_url or product.title,
                chunk_index=0,
                content=product_knowledge_text(product),
            ),
        )
        scored.append((score, product))

    sorted_products = [
        product
        for _score, product in sorted(scored, key=lambda item: item[0], reverse=True)
    ][: max(1, min(limit, 10))]

    return [
        {
            "title": product.title,
            "product_url": product.product_url,
            "price_min": product.price_min,
            "price_max": product.price_max,
            "image_url": (_product_image_urls(product) or [None])[0],
            "caption": _product_caption(product),
        }
        for product in sorted_products
    ]


def find_relevant_product_image(db: Session, query: str) -> dict | None:
    if not is_image_request(query):
        return None

    query_terms = Counter(_tokens(query))
    if not query_terms:
        return None

    products = db.query(EcommerceProduct).order_by(EcommerceProduct.updated_at.desc()).limit(400).all()
    best_product = None
    best_score = 0.0
    for product in products:
        score = _score_chunk(
            query_terms,
            ScrapedChunk(
                scraped_data_id=0,
                url=product.product_url or product.title,
                chunk_index=0,
                content=product_knowledge_text(product),
            ),
        )
        if score > best_score:
            best_score = score
            best_product = product

    if not best_product or best_score <= 0:
        best_product = next(
            (product for product in products if _product_image_urls(product)),
            None,
        )
        if not best_product:
            return None

    image_url = (_product_image_urls(best_product) or [None])[0]
    if not image_url:
        return None

    return {
        "title": best_product.title,
        "image_url": image_url,
        "caption": _product_caption(best_product),
    }


def find_relevant_website_images(db: Session, query: str, limit: int = 3) -> list[dict]:
    if not is_image_request(query):
        return []

    query_terms = Counter(_tokens(query))
    rows = db.query(ScrapedData).order_by(ScrapedData.created_at.desc()).limit(100).all()
    scored = []
    for row in rows:
        image_urls = _scraped_image_urls(row.content)
        if not image_urls:
            continue
        score = _score_chunk(
            query_terms,
            ScrapedChunk(
                scraped_data_id=row.id,
                url=row.url,
                chunk_index=0,
                content=row.content,
            ),
        )
        scored.append((score, row, image_urls))

    if not scored:
        return []

    selected = [
        (row, image_urls)
        for score, row, image_urls in sorted(scored, key=lambda item: item[0], reverse=True)
        if score > 0
    ]
    if not selected:
        selected = [(row, image_urls) for _score, row, image_urls in scored]

    images = []
    seen_urls = set()
    for row, image_urls in selected:
        title = _scraped_title(row.content, row.url)
        for image_url in image_urls:
            if image_url in seen_urls:
                continue
            seen_urls.add(image_url)
            images.append(
                {
                    "title": title,
                    "page_url": row.url,
                    "image_url": image_url,
                    "caption": f"{title}\n{row.url}",
                }
            )
            if len(images) >= max(1, min(limit, 5)):
                return images

    return images


def retrieve_relevant_context(db: Session, query: str) -> str:
    query_terms = Counter(_tokens(query))
    if not query_terms:
        return ""

    try:
        pinecone_context = query_context(
            query,
            top_k=MAX_RETRIEVED_CHUNKS,
            max_chars=MAX_CONTEXT_CHARS,
        )
        if pinecone_context:
            return pinecone_context
    except Exception as exc:
        print("PINECONE QUERY ERROR:", exc)

    query_embedding = _embedding_for_text(query)
    knowledge_chunks = (
        db.query(KnowledgeChunk)
        .order_by(KnowledgeChunk.created_at.desc())
        .limit(400)
        .all()
    )
    chunks = db.query(ScrapedChunk).order_by(ScrapedChunk.created_at.desc()).limit(400).all()
    products = db.query(EcommerceProduct).order_by(EcommerceProduct.updated_at.desc()).limit(400).all()

    if not chunks:
        chunks = []
        rows = db.query(ScrapedData).order_by(ScrapedData.created_at.desc()).limit(20).all()
        for row in rows:
            for index, content in enumerate(chunk_text(row.content)):
                chunks.append(
                    ScrapedChunk(
                        scraped_data_id=row.id,
                        url=row.url,
                        chunk_index=index,
                        content=content,
                    )
                )

    scored = [
        (_score_chunk(query_terms, chunk), chunk)
        for chunk in chunks
    ]
    product_chunks = [
        ScrapedChunk(
            scraped_data_id=0,
            url=product.product_url or product.title,
            chunk_index=0,
            content=product_knowledge_text(product),
        )
        for product in products
    ]
    scored.extend((_score_chunk(query_terms, chunk), chunk) for chunk in product_chunks)
    scored.extend(
        (_score_knowledge_chunk(query_terms, query_embedding, chunk), chunk)
        for chunk in knowledge_chunks
    )

    best_chunks = [
        chunk
        for score, chunk in sorted(scored, key=lambda item: item[0], reverse=True)
        if score > 0
    ][:MAX_RETRIEVED_CHUNKS]

    sections = [
        f"Source: {getattr(chunk, 'url', None) or getattr(chunk, 'source', None) or getattr(chunk, 'title', 'Knowledge base')}\n{chunk.content}"
        for chunk in best_chunks
    ]
    return "\n\n".join(sections)[:MAX_CONTEXT_CHARS]
