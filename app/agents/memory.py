import uuid
from datetime import UTC, datetime

import chromadb
import redis.asyncio as redis

from app.config.settings import get_settings
from app.core.metrics import knowledge_queries_total

settings = get_settings()

EPISODIC_TTL_SECONDS = 24 * 3600


def _redis_client() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL)


async def write_episodic(brief_id: str, agent_type: str, content: str) -> None:
    client = _redis_client()
    try:
        key = f"brief:{brief_id}:memory"
        await client.hset(key, agent_type, content)
        await client.expire(key, EPISODIC_TTL_SECONDS)
    finally:
        await client.aclose()


async def get_episodic(brief_id: str, agent_type: str) -> str:
    client = _redis_client()
    try:
        value = await client.hget(f"brief:{brief_id}:memory", agent_type)
        return value.decode() if value else ""
    finally:
        await client.aclose()


async def get_all_episodic(brief_id: str) -> dict:
    client = _redis_client()
    try:
        raw = await client.hgetall(f"brief:{brief_id}:memory")
        return {k.decode(): v.decode() for k, v in raw.items()}
    finally:
        await client.aclose()


def _kb_collection(tenant_id: str):
    """chromadb's default embedding function is ONNX all-MiniLM-L6-v2 — same model family
    the spec calls for, without adding a heavyweight sentence-transformers/torch dependency."""
    client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
    return client.get_or_create_collection(f"kb_{tenant_id}")


def write_semantic(
    tenant_id: str,
    brief_id: str,
    content_chunks: list[str],
    topic: str,
    domain: str,
    confidence: float = 0.5,
) -> list[str]:
    if not content_chunks:
        return []
    collection = _kb_collection(tenant_id)
    ids = [f"{brief_id}-{uuid.uuid4().hex[:8]}" for _ in content_chunks]
    metadatas = [
        {
            "brief_id": brief_id,
            "topic": topic,
            "domain": domain,
            "confidence": confidence,
            "created_at": datetime.now(UTC).isoformat(),
        }
        for _ in content_chunks
    ]
    collection.add(ids=ids, documents=content_chunks, metadatas=metadatas)
    return ids


def search_semantic(tenant_id: str, query: str, top_k: int = 5) -> list[dict]:
    knowledge_queries_total.labels(tenant_id=tenant_id).inc()
    collection = _kb_collection(tenant_id)
    if collection.count() == 0:
        return []

    results = collection.query(query_texts=[query], n_results=min(top_k, collection.count()))
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    ids = results.get("ids", [[]])[0]

    return [
        {"chroma_id": cid, "content": doc, **meta, "similarity": round(1 - dist, 4)}
        for cid, doc, meta, dist in zip(ids, documents, metadatas, distances)
    ]
