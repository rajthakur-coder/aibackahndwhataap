import hashlib
import json
import math
import os
import re
from collections import Counter

import requests
from sqlalchemy.orm import Session

from app.services.ecommerce import product_knowledge_text
from app.models.ecommerce import EcommerceProduct
from app.models.entities import (
    FAQ,
    KnowledgeChunk,
    KnowledgeDocument,
    Policy,
    ScrapedChunk,
    ScrapedData,
    Service,
    StructuredProduct,
)
from app.services.intelligence import detect_query_intent
from app.services.pinecone import query_context, query_matches, upsert_chunks
from app.services.product_search import product_search_text, score_search_text, search_terms


CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
MAX_CHUNKS_PER_DOCUMENT = 80
MAX_RETRIEVED_CHUNKS = 5
MAX_RERANK_CANDIDATES = 20
MAX_CONTEXT_CHARS = 6000
EMBEDDING_DIMENSIONS = 64
VECTOR_WEIGHT = 0.7
BM25_WEIGHT = 0.3

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
    "footwear",
    "joota",
    "joote",
    "juta",
    "jute",
    "kapda",
    "kapde",
    "mobile",
    "phone",
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
    "shoe",
    "shoes",
    "tshirt",
    "experience",
    "experiences",
}
REQUEST_ACTION_TERMS = {
    "bhejo",
    "catalog",
    "catalogue",
    "chahiye",
    "chaiye",
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
SECTION_RE = re.compile(r"(?m)^Section:\s*(.+)$")
PAGE_METADATA_RE = re.compile(r"(?m)^Page metadata:\s*(\{.+\})$")

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - optional dependency fallback
    BM25Okapi = None


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


def _metadata_from_content(content: str) -> dict:
    match = PAGE_METADATA_RE.search(content or "")
    if not match:
        return {}
    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _metadata_lines(content: str) -> list[str]:
    lines = []
    for line in (content or "").splitlines():
        if not line.strip():
            break
        if line.startswith(("Page URL:", "Page title:", "Meta description:", "Images:", "Page metadata:")):
            lines.append(line)
    return lines


def _section_blocks(content: str) -> list[str]:
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    if not content:
        return []

    matches = list(SECTION_RE.finditer(content))
    if not matches:
        return [content]

    blocks = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        block = content[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def _split_large_block(block: str) -> list[str]:
    if len(block) <= CHUNK_SIZE:
        return [block]

    chunks = []
    start = 0
    while start < len(block):
        end = min(start + CHUNK_SIZE, len(block))
        if end < len(block):
            sentence_split = max(block.rfind(". ", start, end), block.rfind("? ", start, end), block.rfind("! ", start, end))
            split_at = max(sentence_split, block.rfind("\n", start, end), block.rfind(" ", start, end))
            if split_at > start + (CHUNK_SIZE // 2):
                end = split_at + 1

        chunk = block[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(block):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)

    return chunks


def chunk_text(content: str) -> list[str]:
    metadata_prefix = "\n".join(_metadata_lines(content))
    chunks = []
    for block in _section_blocks(content):
        for chunk in _split_large_block(block):
            if metadata_prefix and not chunk.startswith("Page URL:"):
                chunk = f"{metadata_prefix}\n\n{chunk}"
            chunks.append(chunk)
            if len(chunks) >= MAX_CHUNKS_PER_DOCUMENT:
                return chunks
    return chunks


def _chunk_heading(chunk: str) -> str:
    match = SECTION_RE.search(chunk or "")
    return match.group(1).strip() if match else ""


def _chunk_metadata(base_metadata: dict, chunk: str) -> dict:
    metadata = dict(base_metadata)
    heading = _chunk_heading(chunk)
    if heading:
        metadata["section"] = heading
    return {key: value for key, value in metadata.items() if value not in ("", None, [])}


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
    base_metadata = _metadata_from_content(scraped_data.content)
    vectors = [
        {
            "id": f"scraped-{scraped_data.id}-{index}",
            "document_id": scraped_data.id,
            "source_type": "website",
            "source": scraped_data.url,
            "title": base_metadata.get("title") or scraped_data.url,
            "chunk_index": index,
            "content": chunk,
            "metadata": _chunk_metadata(base_metadata, chunk),
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
            "metadata": {
                "page_type": "knowledge",
                "section": _chunk_heading(chunk),
            },
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


def _normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    high = max(scores)
    low = min(scores)
    if high == low:
        return [1.0 if high > 0 else 0.0 for _score in scores]
    return [(score - low) / (high - low) for score in scores]


def _bm25_scores(query: str, chunks: list) -> list[float]:
    tokenized_chunks = [_tokens(chunk.content) for chunk in chunks]
    query_tokens = _tokens(query)
    if not query_tokens or not tokenized_chunks:
        return [0.0 for _chunk in chunks]

    if BM25Okapi is not None:
        return list(BM25Okapi(tokenized_chunks).get_scores(query_tokens))

    query_terms = Counter(query_tokens)
    return [_score_chunk(query_terms, chunk) for chunk in chunks]


def _hybrid_rank(query: str, chunks: list, query_embedding: list[float]) -> list[tuple[float, object]]:
    vector_scores = []
    for chunk in chunks:
        if isinstance(chunk, KnowledgeChunk):
            vector_scores.append(_cosine_similarity(query_embedding, _load_embedding(chunk.embedding)))
        else:
            vector_scores.append(_score_chunk(Counter(_tokens(query)), chunk))

    bm25_scores = _bm25_scores(query, chunks)
    normalized_vector_scores = _normalize_scores([max(score, 0.0) for score in vector_scores])
    normalized_bm25_scores = _normalize_scores([max(score, 0.0) for score in bm25_scores])

    ranked = []
    for index, chunk in enumerate(chunks):
        score = (
            normalized_vector_scores[index] * VECTOR_WEIGHT
            + normalized_bm25_scores[index] * BM25_WEIGHT
        )
        ranked.append((score, chunk))
    return sorted(ranked, key=lambda item: item[0], reverse=True)


def _chunk_source(chunk) -> str:
    return (
        getattr(chunk, "url", None)
        or getattr(chunk, "source", None)
        or getattr(chunk, "title", None)
        or "Knowledge base"
    )


def _format_context_section(chunk) -> str:
    return f"Source: {_chunk_source(chunk)}\n{chunk.content}"


def _dedupe_chunks(chunks: list) -> list:
    deduped = []
    seen = set()
    for chunk in chunks:
        key = re.sub(r"\s+", " ", (chunk.content or "").strip().lower())[:700]
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


def _llm_rerank_chunks(query: str, chunks: list, limit: int = MAX_RETRIEVED_CHUNKS) -> list:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key or not chunks:
        return chunks[:limit]

    candidates = [
        {
            "id": index,
            "source": _chunk_source(chunk),
            "content": chunk.content[:1200],
        }
        for index, chunk in enumerate(chunks[:MAX_RERANK_CANDIDATES])
    ]
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("APP_URL", ""),
                "X-Title": os.getenv("APP_NAME", "AI WhatsApp Automation"),
            },
            json={
                "model": os.getenv("RERANK_MODEL", os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Pick the chunk ids that best answer the user query. "
                            "Return only JSON like {\"ids\":[0,2,4]}."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"query": query, "candidates": candidates, "limit": limit},
                            ensure_ascii=True,
                        ),
                    },
                ],
                "temperature": 0,
                "max_tokens": 120,
            },
            timeout=20,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        payload = json.loads(match.group(0) if match else content)
        ids = payload.get("ids", [])
    except Exception as exc:
        print("RERANK ERROR:", exc)
        return chunks[:limit]

    selected = []
    seen = set()
    for chunk_id in ids:
        if isinstance(chunk_id, int) and 0 <= chunk_id < len(chunks) and chunk_id not in seen:
            selected.append(chunks[chunk_id])
            seen.add(chunk_id)
        if len(selected) >= limit:
            return selected

    for index, chunk in enumerate(chunks):
        if index not in seen:
            selected.append(chunk)
        if len(selected) >= limit:
            break
    return selected


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

    query_terms = search_terms(query)
    products = db.query(EcommerceProduct).order_by(EcommerceProduct.updated_at.desc()).limit(400).all()
    scored = []
    for product in products:
        score = score_search_text(query_terms, product_search_text(product))
        scored.append((score, product))

    sorted_products = [
        product
        for _score, product in sorted(scored, key=lambda item: item[0], reverse=True)
        if _score > 0
    ][: max(1, min(limit, 10))]
    if not sorted_products:
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

    query_terms = search_terms(query)
    if not query_terms:
        return None

    products = db.query(EcommerceProduct).order_by(EcommerceProduct.updated_at.desc()).limit(400).all()
    best_product = None
    best_score = 0.0
    for product in products:
        score = score_search_text(query_terms, product_search_text(product))
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

    structured_context = _retrieve_structured_context(db, query)
    if structured_context:
        return structured_context[:MAX_CONTEXT_CHARS]

    pinecone_context = ""
    pinecone_chunks = []
    try:
        for index, match in enumerate(query_matches(query, top_k=MAX_RERANK_CANDIDATES)):
            pinecone_chunks.append(
                ScrapedChunk(
                    scraped_data_id=0,
                    url=match["source"],
                    chunk_index=index,
                    content=match["content"],
                )
            )
        pinecone_context = query_context(
            query,
            top_k=MAX_RERANK_CANDIDATES,
            max_chars=MAX_CONTEXT_CHARS,
        )
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

    product_chunks = [
        ScrapedChunk(
            scraped_data_id=0,
            url=product.product_url or product.title,
            chunk_index=0,
            content=product_knowledge_text(product),
        )
        for product in products
    ]
    all_chunks = _dedupe_chunks(pinecone_chunks + chunks + product_chunks + knowledge_chunks)
    ranked_chunks = [
        chunk
        for score, chunk in _hybrid_rank(query, all_chunks, query_embedding)
        if score > 0
    ][:MAX_RERANK_CANDIDATES]

    best_chunks = ranked_chunks[:MAX_RETRIEVED_CHUNKS]

    sections = [_format_context_section(chunk) for chunk in best_chunks]
    local_context = "\n\n".join(sections)
    if local_context:
        return local_context[:MAX_CONTEXT_CHARS]
    return pinecone_context[:MAX_CONTEXT_CHARS]


def _retrieve_structured_context(db: Session, query: str) -> str:
    intent = detect_query_intent(query)
    query_terms = Counter(_tokens(query))
    sections = []

    if intent.name == "policy_question":
        policies_query = db.query(Policy)
        if intent.policy_type:
            policies_query = policies_query.filter(Policy.policy_type == intent.policy_type)
        policies = policies_query.order_by(Policy.created_at.desc()).limit(30).all()
        ranked = _rank_structured_rows(query_terms, policies, lambda row: f"{row.policy_type} {row.title or ''} {row.content}")
        for row in ranked[:MAX_RETRIEVED_CHUNKS]:
            sections.append(
                f"Structured policy: {row.policy_type}\n"
                f"Source: {row.source_url}\n"
                f"{row.title or row.policy_type.title()}\n{row.content}"
            )

    elif intent.name == "price_question":
        products = db.query(StructuredProduct).order_by(StructuredProduct.created_at.desc()).limit(50).all()
        services = db.query(Service).order_by(Service.created_at.desc()).limit(50).all()
        ranked_products = _rank_structured_rows(
            query_terms,
            products,
            lambda row: f"{row.title} {row.description or ''} {row.category or ''} {row.brand or ''} {row.price or ''}",
        )
        ranked_services = _rank_structured_rows(
            query_terms,
            services,
            lambda row: f"{row.name} {row.description or ''} {row.category or ''} {row.price or ''}",
        )
        for row in ranked_products[:3]:
            sections.append(
                f"Structured product pricing\nSource: {row.source_url}\n"
                f"Product: {row.title}\nBrand: {row.brand or 'unknown'}\nPrice: {row.price or 'not listed'}\n"
                f"{row.description or ''}"
            )
        for row in ranked_services[:3]:
            sections.append(
                f"Structured service pricing\nSource: {row.source_url}\n"
                f"Service: {row.name}\nPrice: {row.price or 'not listed'}\n{row.description or ''}"
            )

    elif intent.name == "faq_question":
        faqs = db.query(FAQ).order_by(FAQ.created_at.desc()).limit(50).all()
        ranked = _rank_structured_rows(query_terms, faqs, lambda row: f"{row.question} {row.answer} {row.category or ''}")
        for row in ranked[:MAX_RETRIEVED_CHUNKS]:
            sections.append(
                f"Structured FAQ\nSource: {row.source_url}\nQ: {row.question}\nA: {row.answer}"
            )

    return "\n\n".join(sections)


def _rank_structured_rows(query_terms: Counter, rows: list, text_for_row) -> list:
    scored = []
    for row in rows:
        text = text_for_row(row)
        row_terms = Counter(_tokens(text))
        score = sum(row_terms.get(term, 0) * count for term, count in query_terms.items())
        if score > 0:
            scored.append((score, row))
    if not scored:
        return rows[:MAX_RETRIEVED_CHUNKS]
    return [row for _score, row in sorted(scored, key=lambda item: item[0], reverse=True)]

