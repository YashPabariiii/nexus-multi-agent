import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import memory
from app.agents.common import parse_llm_json
from app.core.metrics import knowledge_base_entries_total
from app.llm.router import get_llm
from app.models.brief import ResearchBrief
from app.models.knowledge import KnowledgeBaseEntry
from app.models.report import Report

CHUNK_WORDS = 800
CHUNK_OVERLAP_WORDS = 150
EMBEDDING_MODEL = "chromadb-default (onnx all-MiniLM-L6-v2)"

COMPARE_SYSTEM_PROMPT = """Compare two research reports on related topics.
Return ONLY valid JSON:
{"common_findings": [str], "contradictions": [str], "new_developments": [str], "confidence_delta": float}"""


def _chunk_text(text: str, chunk_words: int = CHUNK_WORDS, overlap_words: int = CHUNK_OVERLAP_WORDS) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max(chunk_words - overlap_words, 1)
    for start in range(0, len(words), step):
        chunk = " ".join(words[start : start + chunk_words])
        if chunk:
            chunks.append(chunk)
        if start + chunk_words >= len(words):
            break
    return chunks


async def index_brief(db: AsyncSession, brief_id: str, tenant_id: str) -> int:
    report = await db.scalar(select(Report).where(Report.brief_id == uuid.UUID(brief_id)))
    brief = await db.get(ResearchBrief, uuid.UUID(brief_id))
    if report is None or brief is None:
        return 0

    section_text = "\n\n".join(f"{s.get('title', '')}\n{s.get('content', '')}" for s in report.sections)
    full_text = f"{report.title}\n\n{report.executive_summary}\n\n{section_text}"
    chunks = _chunk_text(full_text)
    if not chunks:
        return 0

    chroma_ids = memory.write_semantic(
        tenant_id=tenant_id,
        brief_id=brief_id,
        content_chunks=chunks,
        topic=brief.topic,
        domain=brief.domain,
        confidence=report.overall_confidence,
    )

    for chunk, chroma_id in zip(chunks, chroma_ids):
        db.add(
            KnowledgeBaseEntry(
                tenant_id=uuid.UUID(tenant_id),
                brief_id=uuid.UUID(brief_id),
                topic=brief.topic,
                domain=brief.domain,
                content_chunk=chunk,
                embedding_model=EMBEDDING_MODEL,
                chroma_id=chroma_id,
            )
        )
    await db.commit()
    knowledge_base_entries_total.labels(tenant_id=tenant_id).inc(len(chroma_ids))
    return len(chroma_ids)


def get_related_briefs(tenant_id: str, topic: str, limit: int = 3) -> list[dict]:
    hits = memory.search_semantic(tenant_id, topic, top_k=10)

    by_brief = defaultdict(list)
    for hit in hits:
        by_brief[hit["brief_id"]].append(hit)

    ranked = sorted(by_brief.items(), key=lambda kv: max(h["similarity"] for h in kv[1]), reverse=True)

    results = []
    for brief_id, brief_hits in ranked[:limit]:
        best = max(brief_hits, key=lambda h: h["similarity"])
        results.append(
            {
                "brief_id": brief_id,
                "topic": best.get("topic"),
                "date": best.get("created_at"),
                "similarity": best.get("similarity"),
                "key_findings_preview": best.get("content", "")[:300],
            }
        )
    return results


async def compare_briefs(db: AsyncSession, brief_id_a: str, brief_id_b: str) -> dict:
    report_a = await db.scalar(select(Report).where(Report.brief_id == uuid.UUID(brief_id_a)))
    report_b = await db.scalar(select(Report).where(Report.brief_id == uuid.UUID(brief_id_b)))
    if report_a is None or report_b is None:
        return {"error": "one or both briefs have no report yet"}

    llm = get_llm("synthesis")
    user_prompt = (
        f"Report A ({report_a.title}):\n{report_a.executive_summary}\n\n"
        f"Report B ({report_b.title}):\n{report_b.executive_summary}"
    )
    result = await llm.call(
        messages=[{"role": "system", "content": COMPARE_SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )
    parsed = parse_llm_json(result["content"])
    if not isinstance(parsed, dict):
        parsed = {"common_findings": [], "contradictions": [], "new_developments": [], "confidence_delta": 0.0}
    return parsed
