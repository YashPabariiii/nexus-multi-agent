import chromadb
from langchain_core.tools import tool

from app.config.settings import get_settings
from app.core.tracing import traced_tool

settings = get_settings()
COLLECTION_NAME = "knowledge_base"


def _collection():
    client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
    return client.get_or_create_collection(COLLECTION_NAME)


@tool
@traced_tool
def search_knowledge_base(query: str, tenant_id: str, top_k: int = 5) -> list[dict]:
    """Search the tenant's private knowledge base of past research for relevant prior findings."""
    collection = _collection()
    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        where={"tenant_id": tenant_id},
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    return [
        {
            "content": doc,
            "topic": meta.get("topic"),
            "brief_id": meta.get("brief_id"),
            "similarity": round(1 - dist, 4),
        }
        for doc, meta, dist in zip(documents, metadatas, distances)
    ]


def add_knowledge_entry(chroma_id: str, content_chunk: str, tenant_id: str, brief_id: str, topic: str) -> None:
    collection = _collection()
    collection.add(
        ids=[chroma_id],
        documents=[content_chunk],
        metadatas=[{"tenant_id": tenant_id, "brief_id": brief_id, "topic": topic}],
    )
