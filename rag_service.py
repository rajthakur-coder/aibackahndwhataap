import hashlib
import json
import math
import re
from collections import Counter

from sqlalchemy.orm import Session

from models import KnowledgeChunk, KnowledgeDocument, ScrapedChunk, ScrapedData
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
