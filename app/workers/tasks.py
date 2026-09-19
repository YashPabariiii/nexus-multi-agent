import asyncio
import json
import os
import uuid
from datetime import UTC, datetime

import redis.asyncio as redis
from langgraph.types import Command
from sqlalchemy import select, update

from app.agents.common import publish_event
from app.agents.graph import compile_graph
from app.agents.state import initial_state
from app.config.settings import get_settings
from app.core import tracing
from app.core.logging import logger
from app.core.metrics import (
    brief_duration_seconds,
    briefs_completed_total,
    briefs_failed_total,
    confidence_score_histogram,
    reports_emailed_total,
    reports_generated_total,
)
from app.db.session import AsyncSessionLocal
from app.models.brief import ResearchBrief
from app.models.report import Report
from app.services import email_service
from app.services.knowledge_service import get_related_briefs, index_brief
from app.services.report_generator import generate_report_pdf, generate_trace_pdf
from app.workers.celery_app import celery_app

settings = get_settings()

RESUME_WAIT_TIMEOUT_SECONDS = 30 * 60


def trace_pdf_path(report_pdf_path: str) -> str:
    return os.path.join(os.path.dirname(report_pdf_path), "trace.pdf")


def _write_pdf_files(pdf_path: str, pdf_bytes: bytes, trace_bytes: bytes) -> None:
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    with open(trace_pdf_path(pdf_path), "wb") as f:
        f.write(trace_bytes)


async def _generate_and_deliver_report(brief_id: str, to_email: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        brief = await db.get(ResearchBrief, uuid.UUID(brief_id))
        report = await db.scalar(select(Report).where(Report.brief_id == uuid.UUID(brief_id)))
        if brief is None or report is None:
            logger.error("generate_report_no_report", brief_id=brief_id)
            return

        related = get_related_briefs(str(brief.tenant_id), brief.topic, limit=3)
        pdf_bytes = await generate_report_pdf(db, brief_id, related_briefs=related)
        trace_bytes = await generate_trace_pdf(db, brief_id)

        pdf_path = os.path.join(settings.REPORTS_DIR, brief_id, "report.pdf")
        await asyncio.to_thread(_write_pdf_files, pdf_path, pdf_bytes, trace_bytes)

        report.pdf_path = pdf_path
        await db.commit()
        reports_generated_total.labels(tenant_id=str(brief.tenant_id)).inc()
        confidence_score_histogram.observe(report.overall_confidence)

        metadata = {
            "topic": brief.topic,
            "overall_confidence": report.overall_confidence,
            "executive_summary": report.executive_summary,
            "agents_used": ["supervisor", "web_search", "domain_knowledge", "data_analyst", "fact_checker", "synthesis", "writer"],
        }

        await email_service.send_brief_ready(to_email or brief.user_email, metadata, pdf_bytes, trace_bytes)
        report.emailed = True
        await db.commit()
        reports_emailed_total.inc()

    await publish_event(brief_id, "report_ready", "supervisor", "Report PDF generated and emailed")


async def _load_brief(brief_id: str) -> ResearchBrief | None:
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(ResearchBrief).where(ResearchBrief.id == uuid.UUID(brief_id)))


async def _set_status(brief_id: str, status: str, error_message: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        values = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        if status == "complete":
            values["completed_at"] = datetime.now(UTC)
        await db.execute(update(ResearchBrief).where(ResearchBrief.id == uuid.UUID(brief_id)).values(**values))
        await db.commit()


async def _wait_for_resume(brief_id: str) -> dict:
    client = redis.from_url(settings.REDIS_URL)
    pubsub = client.pubsub()
    channel = f"brief:{brief_id}:resume"
    await pubsub.subscribe(channel)
    try:
        deadline = asyncio.get_event_loop().time() + RESUME_WAIT_TIMEOUT_SECONDS
        while asyncio.get_event_loop().time() < deadline:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if message and message.get("type") == "message":
                return json.loads(message["data"])
        return {"action": "approve", "reason": "timeout"}
    finally:
        await pubsub.unsubscribe(channel)
        await client.aclose()


async def _persist_report(brief: ResearchBrief, final_report: dict) -> None:
    async with AsyncSessionLocal() as db:
        report = Report(
            brief_id=brief.id,
            tenant_id=brief.tenant_id,
            title=final_report.get("title", brief.topic),
            executive_summary=final_report.get("executive_summary", ""),
            sections=final_report.get("sections", []),
            overall_confidence=final_report.get("overall_confidence", 0.0),
            word_count=final_report.get("word_count", 0),
        )
        db.add(report)
        await db.commit()


async def _execute_graph(brief: ResearchBrief, brief_id: str, tenant_id: str) -> dict:
    """Run the LangGraph pipeline (including human-review interrupt/resume cycles); returns the final report."""
    from app.agents.planner import assess_brief_complexity

    complexity = await assess_brief_complexity(brief.topic, brief.scope, brief.domain)
    logger.info("brief_complexity_assessed", brief_id=brief_id, **complexity)

    graph = compile_graph(brief_id, brief.depth)
    config = {"configurable": {"thread_id": brief_id}}

    state = initial_state(
        brief_id=brief_id,
        tenant_id=tenant_id,
        topic=brief.topic,
        scope=brief.scope,
        domain=brief.domain,
        depth=brief.depth,
        audience=brief.audience,
    )

    await graph.ainvoke(state, config=config)

    while True:
        snapshot = await graph.aget_state(config)
        if not snapshot.next:
            break

        # graph is paused at an interrupt (human_review)
        await _set_status(brief_id, "awaiting_review")
        preview_url = f"{settings.APP_BASE_URL}/v1/briefs/{brief_id}"
        await email_service.send_human_review_notification(brief.user_email, brief_id, brief.topic, preview_url)
        feedback = await _wait_for_resume(brief_id)
        await publish_event(brief_id, "human_review_resumed", "human_review", f"Resumed with action={feedback.get('action')}")
        await graph.ainvoke(Command(resume=feedback), config=config)

    final_snapshot = await graph.aget_state(config)
    return final_snapshot.values.get("final_report") or final_snapshot.values.get("draft_report", {})


async def _run_graph(brief_id: str, tenant_id: str) -> None:
    brief = await _load_brief(brief_id)
    if brief is None:
        logger.error("brief_not_found", brief_id=brief_id)
        return

    with (
        tracing.trace_attributes(
            trace_name="research_brief",
            session_id=brief_id,
            tags=[brief.depth, brief.domain],
            metadata={"run_id": brief_id, "tenant_id": tenant_id},
        ),
        tracing.observation("research_brief", as_type="agent", input={"topic": brief.topic, "scope": brief.scope}) as root,
    ):
        final_report = await _execute_graph(brief, brief_id, tenant_id)
        root.update(output={"title": final_report.get("title"), "overall_confidence": final_report.get("overall_confidence")})

    await _persist_report(brief, final_report)
    await _set_status(brief_id, "complete")

    briefs_completed_total.labels(domain=brief.domain, depth=brief.depth).inc()
    refreshed = await _load_brief(brief_id)
    if refreshed and refreshed.completed_at and refreshed.created_at:
        brief_duration_seconds.labels(depth=brief.depth).observe((refreshed.completed_at - refreshed.created_at).total_seconds())

    if brief.consent_store:
        async with AsyncSessionLocal() as db:
            entries_count = await index_brief(db, brief_id, tenant_id)
            logger.info("brief_indexed", brief_id=brief_id, entries_count=entries_count)

    await _generate_and_deliver_report(brief_id)

    await publish_event(brief_id, "brief_complete", "supervisor", "Research brief complete")


@celery_app.task(name="generate_report")
def generate_report(brief_id: str, to_email: str | None = None) -> None:
    """Standalone trigger (e.g. from the resend-email route) — regenerates both PDFs and re-sends the email."""
    asyncio.run(_generate_and_deliver_report(brief_id, to_email=to_email))


@celery_app.task(name="run_research_brief", bind=True)
def run_research_brief(self, brief_id: str, tenant_id: str) -> None:
    async def _runner():
        try:
            await _set_status(brief_id, "planning")
            await _run_graph(brief_id, tenant_id)
        except Exception as exc:
            logger.error("brief_failed", brief_id=brief_id, error=str(exc))
            await _set_status(brief_id, "failed", error_message=str(exc)[:2000])
            briefs_failed_total.labels(reason=type(exc).__name__).inc()
            await publish_event(brief_id, "brief_failed", "supervisor", str(exc)[:280])
            raise
        finally:
            tracing.flush()

    asyncio.run(_runner())
