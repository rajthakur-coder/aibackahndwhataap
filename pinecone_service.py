import os
import time
from typing import Any

import requests
from openai import OpenAI


DEFAULT_NAMESPACE = "default"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_GEMINI_EMBEDDING_MODEL = "text-embedding-004"
DEFAULT_OPENAI_EMBEDDING_DIMENSION = 1536
DEFAULT_GEMINI_EMBEDDING_DIMENSION = 768


def _embedding_provider() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()


def _embedding_model() -> str:
    if _embedding_provider() == "gemini":
        return os.getenv("GEMINI_EMBEDDING_MODEL", DEFAULT_GEMINI_EMBEDDING_MODEL)
    return os.getenv("OPENAI_EMBEDDING_MODEL", DEFAULT_OPENAI_EMBEDDING_MODEL)


def _embedding_dimension() -> int:
    fallback = (
        DEFAULT_GEMINI_EMBEDDING_DIMENSION
        if _embedding_provider() == "gemini"
        else DEFAULT_OPENAI_EMBEDDING_DIMENSION
    )
    return int(os.getenv("PINECONE_DIMENSION", fallback))


def is_pinecone_configured() -> bool:
    embedding_key = (
        os.getenv("GEMINI_API_KEY")
        if _embedding_provider() == "gemini"
        else os.getenv("OPENAI_API_KEY")
    )
    return bool(
        os.getenv("PINECONE_API_KEY")
        and os.getenv("PINECONE_INDEX_NAME")
        and embedding_key
    )


def _pinecone_client():
    try:
        from pinecone.grpc import PineconeGRPC as Pinecone
    except ImportError:
        from pinecone import Pinecone

    return Pinecone(api_key=os.getenv("PINECONE_API_KEY"))


def _index_names(client: Any) -> list[str]:
    indexes = client.list_indexes()
    if hasattr(indexes, "names"):
        return list(indexes.names())
    return [
        index.get("name") if isinstance(index, dict) else getattr(index, "name", None)
        for index in indexes
    ]


def ensure_index() -> bool:
    if not is_pinecone_configured():
        return False

    from pinecone import ServerlessSpec

    client = _pinecone_client()
    index_name = os.getenv("PINECONE_INDEX_NAME")
    if index_name in _index_names(client):
        return True

    client.create_index(
        name=index_name,
        dimension=_embedding_dimension(),
        metric=os.getenv("PINECONE_METRIC", "cosine"),
        spec=ServerlessSpec(
            cloud=os.getenv("PINECONE_CLOUD", "aws"),
            region=os.getenv("PINECONE_REGION", "us-east-1"),
        ),
        deletion_protection=os.getenv("PINECONE_DELETION_PROTECTION", "disabled"),
    )

    timeout_at = time.time() + int(os.getenv("PINECONE_READY_TIMEOUT", "60"))
    while time.time() < timeout_at:
        description = client.describe_index(index_name)
        status = getattr(description, "status", None)
        ready = status.get("ready") if isinstance(status, dict) else getattr(status, "ready", False)
        if ready:
            return True
        time.sleep(2)

    return True


def _index():
    client = _pinecone_client()
    return client.Index(os.getenv("PINECONE_INDEX_NAME"))


def _namespace() -> str:
    return os.getenv("PINECONE_NAMESPACE", DEFAULT_NAMESPACE)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    if _embedding_provider() == "gemini":
        return _embed_texts_with_gemini(texts)

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.embeddings.create(
        model=_embedding_model(),
        input=texts,
    )
    return [item.embedding for item in response.data]


def _embed_texts_with_gemini(texts: list[str]) -> list[list[float]]:
    model = _embedding_model()
    model_path = model if model.startswith("models/") else f"models/{model}"
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/{model_path}:batchEmbedContents",
        params={"key": os.getenv("GEMINI_API_KEY")},
        json={
            "requests": [
                {
                    "model": model_path,
                    "content": {"parts": [{"text": text}]},
                }
                for text in texts
            ]
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return [item["values"] for item in data.get("embeddings", [])]


def upsert_chunks(chunks: list[dict]) -> int:
    if not chunks or not ensure_index():
        return 0

    vectors = []
    embeddings = embed_texts([chunk["content"] for chunk in chunks])
    for chunk, embedding in zip(chunks, embeddings):
        metadata = {
            "source_type": chunk["source_type"],
            "source": chunk.get("source") or "",
            "title": chunk.get("title") or "",
            "content": chunk["content"],
            "chunk_index": chunk["chunk_index"],
            "document_id": str(chunk["document_id"]),
        }
        vectors.append(
            {
                "id": chunk["id"],
                "values": embedding,
                "metadata": metadata,
            }
        )

    _index().upsert(vectors=vectors, namespace=_namespace())
    return len(vectors)


def query_context(query: str, top_k: int = 5, max_chars: int = 6000) -> str:
    if not query.strip() or not ensure_index():
        return ""

    embedding = embed_texts([query])[0]
    result = _index().query(
        vector=embedding,
        top_k=top_k,
        namespace=_namespace(),
        include_metadata=True,
    )

    matches = result.get("matches", []) if isinstance(result, dict) else getattr(result, "matches", [])
    sections = []
    for match in matches:
        metadata = match.get("metadata", {}) if isinstance(match, dict) else getattr(match, "metadata", {})
        content = metadata.get("content")
        if not content:
            continue
        source = metadata.get("source") or metadata.get("title") or "Knowledge base"
        sections.append(f"Source: {source}\n{content}")

    return "\n\n".join(sections)[:max_chars]


def status() -> dict:
    configured = is_pinecone_configured()
    if not configured:
        return {
            "configured": False,
            "ready": False,
            "reason": (
                "Set PINECONE_API_KEY, PINECONE_INDEX_NAME, and GEMINI_API_KEY"
                if _embedding_provider() == "gemini"
                else "Set PINECONE_API_KEY, PINECONE_INDEX_NAME, and OPENAI_API_KEY"
            ),
        }

    try:
        ensure_index()
        return {
            "configured": True,
            "ready": True,
            "index": os.getenv("PINECONE_INDEX_NAME"),
            "namespace": _namespace(),
            "embedding_provider": _embedding_provider(),
            "embedding_model": _embedding_model(),
        }
    except Exception as exc:
        return {
            "configured": True,
            "ready": False,
            "error": str(exc),
        }
