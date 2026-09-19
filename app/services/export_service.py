import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_trace import Claim
from app.models.brief import ResearchBrief
from app.models.report import Report


async def build_report_json(db: AsyncSession, brief_id: str) -> dict:
    bid = uuid.UUID(brief_id)
    brief = await db.get(ResearchBrief, bid)
    report = await db.scalar(select(Report).where(Report.brief_id == bid))
    claims = list(await db.scalars(select(Claim).where(Claim.brief_id == bid)))

    if brief is None or report is None:
        return {}

    return {
        "brief_id": str(brief.id),
        "topic": brief.topic,
        "domain": brief.domain,
        "depth": brief.depth,
        "audience": brief.audience,
        "status": brief.status,
        "title": report.title,
        "executive_summary": report.executive_summary,
        "sections": report.sections,
        "overall_confidence": report.overall_confidence,
        "word_count": report.word_count,
        "claims": [
            {
                "claim": c.claim_text,
                "source_url": c.source_url,
                "source_title": c.source_title,
                "confidence": c.confidence,
                "verified": c.verified,
                "disputed_by_agent": c.disputed_by_agent,
                "dispute_reason": c.dispute_reason,
                "final_confidence": c.final_confidence,
            }
            for c in claims
        ],
        "created_at": brief.created_at.isoformat() if brief.created_at else None,
        "completed_at": brief.completed_at.isoformat() if brief.completed_at else None,
    }


async def build_report_preview(db: AsyncSession, brief_id: str) -> dict:
    bid = uuid.UUID(brief_id)
    report = await db.scalar(select(Report).where(Report.brief_id == bid))
    if report is None:
        return {}

    return {
        "title": report.title,
        "executive_summary": report.executive_summary,
        "overall_confidence": report.overall_confidence,
        "sections": [{"title": s.get("title"), "confidence": s.get("confidence")} for s in report.sections],
    }
