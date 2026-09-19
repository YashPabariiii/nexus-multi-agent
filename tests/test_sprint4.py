"""Sprint 4: PDF report generation, email delivery, downloads, knowledge comparison.
Requires live Postgres/Redis/Chroma (see RUNBOOK.md). LLM/email are mocked.
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader
from sqlalchemy import select

from app.core.auth import generate_api_key, hash_api_key
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.agent_trace import AgentTrace, Claim
from app.models.brief import ResearchBrief
from app.models.report import Report
from app.models.tenant import Tenant
from app.services import export_service, report_generator
from app.workers import tasks as tasks_module


async def _seed_full_brief():
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="Report Tenant", email=f"rt-{uuid.uuid4().hex[:8]}@test.local", api_key_hash=hash_api_key(generate_api_key()), plan="pro")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

        brief = ResearchBrief(
            tenant_id=tenant.id, topic="AI Chip Market", scope="global players", domain="tech",
            depth="standard", audience="analyst", status="complete", consent_store=True, user_email="reader@example.com",
        )
        db.add(brief)
        await db.commit()
        await db.refresh(brief)

        report = Report(
            brief_id=brief.id, tenant_id=tenant.id, title="AI Chip Market — Research Brief",
            executive_summary="- Demand grew 40%\n- Two new entrants\n- Supply chain risk remains",
            sections=[
                {"title": "Market Growth", "content": "Demand grew sharply " * 30, "citations": [{"title": "Source A", "url": "https://example.com/a"}], "confidence": 0.82},
                {"title": "Risks", "content": "Supply chain risk remains elevated " * 20, "citations": [], "confidence": 0.55},
            ],
            overall_confidence=0.7, word_count=350,
        )
        db.add(report)

        db.add(Claim(
            brief_id=brief.id, agent_id="web_search", claim_text="Demand grew 40% year over year",
            source_url="https://example.com/a", source_title="Source A", confidence=0.8, verified=True, final_confidence=0.85,
        ))
        db.add(Claim(
            brief_id=brief.id, agent_id="data_analyst", claim_text="Supplier X will double capacity",
            source_url="https://example.com/b", source_title="Source B", confidence=0.7, verified=False,
            disputed_by_agent="fact_checker", dispute_reason="no corroborating source", final_confidence=0.3,
        ))

        db.add(AgentTrace(
            brief_id=brief.id, agent_id="web_search-1", agent_type="web_search", llm_provider="groq", llm_model="llama-3.1-8b-instant",
            input_summary="find demand data", output_summary="1 claim extracted", claims=[{"claim": "Demand grew 40%", "confidence": 0.8}],
            prompt_tokens=100, completion_tokens=50, latency_ms=800, status="complete",
        ))
        db.add(AgentTrace(
            brief_id=brief.id, agent_id="fact_checker-1", agent_type="fact_checker", llm_provider="groq", llm_model="llama-3.3-70b-versatile",
            input_summary="verify claim", output_summary="disputed", confidence_scores={"final_confidence": 0.3},
            prompt_tokens=200, completion_tokens=80, latency_ms=1200, status="complete",
        ))
        db.add(AgentTrace(
            brief_id=brief.id, agent_id="data_analyst_debate_verdict-1", agent_type="data_analyst_debate_verdict",
            llm_provider="groq", llm_model="llama-3.3-70b-versatile", input_summary="debate verdict",
            output_summary="upheld=False final_confidence=0.30", confidence_scores={"final_confidence": 0.3},
            prompt_tokens=90, completion_tokens=40, latency_ms=600, status="complete",
        ))
        await db.commit()

    return str(tenant.id), str(brief.id)


async def _seed_second_brief_same_tenant(tenant_id: str) -> str:
    async with AsyncSessionLocal() as db:
        brief = ResearchBrief(
            tenant_id=uuid.UUID(tenant_id), topic="AI Chip Market Q2 Update", scope="global players", domain="tech",
            depth="standard", audience="analyst", status="complete", consent_store=True, user_email="reader@example.com",
        )
        db.add(brief)
        await db.commit()
        await db.refresh(brief)

        report = Report(
            brief_id=brief.id, tenant_id=uuid.UUID(tenant_id), title="AI Chip Market Q2 Update — Research Brief",
            executive_summary="- Demand grew further\n- One new entrant exited",
            sections=[{"title": "Market Growth", "content": "Demand continued to grow " * 20, "citations": [], "confidence": 0.75}],
            overall_confidence=0.75, word_count=200,
        )
        db.add(report)
        await db.commit()

    return str(brief.id)


# ---------------------------------------------------------------------------
# report_generator.py — real PDF bytes, real page counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_report_pdf_has_at_least_five_pages():
    _tenant_id, brief_id = await _seed_full_brief()

    async with AsyncSessionLocal() as db:
        pdf_bytes = await report_generator.generate_report_pdf(db, brief_id)

    assert pdf_bytes[:4] == b"%PDF"
    reader = PdfReader(__import__("io").BytesIO(pdf_bytes))
    assert len(reader.pages) >= 5

    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert "NEXUS" in text
    assert "Executive Summary" in text
    assert "Market Growth" in text
    assert "Agent Intelligence Log" in text
    assert "Methodology" in text


@pytest.mark.asyncio
async def test_generate_trace_pdf_shows_full_reasoning_log():
    _tenant_id, brief_id = await _seed_full_brief()

    async with AsyncSessionLocal() as db:
        pdf_bytes = await report_generator.generate_trace_pdf(db, brief_id)

    reader = PdfReader(__import__("io").BytesIO(pdf_bytes))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert "web_search" in text
    assert "fact_checker" in text
    assert "Timeline" in text


# ---------------------------------------------------------------------------
# export_service.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_service_json_and_preview():
    _tenant_id, brief_id = await _seed_full_brief()

    async with AsyncSessionLocal() as db:
        full = await export_service.build_report_json(db, brief_id)
        preview = await export_service.build_report_preview(db, brief_id)

    assert full["topic"] == "AI Chip Market"
    assert len(full["claims"]) == 2
    assert preview["title"] == full["title"]
    assert "content" not in json.dumps(preview["sections"])  # preview omits full section content


# ---------------------------------------------------------------------------
# full pipeline: _generate_and_deliver_report writes files + attempts email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_deliver_report_writes_pdfs_and_sends_email(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_module.settings, "REPORTS_DIR", str(tmp_path))

    _tenant_id, brief_id = await _seed_full_brief()

    sent = {}

    async def fake_send_brief_ready(to, metadata, pdf_bytes, trace_bytes):
        sent["to"] = to
        sent["confidence"] = metadata["overall_confidence"]
        sent["pdf_len"] = len(pdf_bytes)
        sent["trace_len"] = len(trace_bytes)

    with patch("app.workers.tasks.email_service.send_brief_ready", new=AsyncMock(side_effect=fake_send_brief_ready)):
        await tasks_module._generate_and_deliver_report(brief_id)

    assert sent["to"] == "reader@example.com"
    assert sent["pdf_len"] > 1000
    assert sent["trace_len"] > 100

    async with AsyncSessionLocal() as db:
        report = await db.scalar(select(Report).where(Report.brief_id == uuid.UUID(brief_id)))
        assert report.pdf_path and report.pdf_path.endswith("report.pdf")
        assert report.emailed is True

    import os

    assert os.path.exists(report.pdf_path)  # noqa: ASYNC240 (test assertion, not production code)
    assert os.path.exists(tasks_module.trace_pdf_path(report.pdf_path))  # noqa: ASYNC240


@pytest.mark.asyncio
async def test_low_confidence_triggers_alert_subject():
    from app.services.email_service import LOW_CONFIDENCE_THRESHOLD

    captured = {}

    async def fake_send(msg, to):
        captured["subject"] = msg["Subject"]

    with patch("app.services.email_service._send", new=AsyncMock(side_effect=fake_send)):
        from app.services.email_service import send_brief_ready

        await send_brief_ready(
            "reader@example.com",
            {"topic": "Risky Topic", "overall_confidence": LOW_CONFIDENCE_THRESHOLD - 0.1, "executive_summary": "x", "agents_used": []},
            b"%PDF-1.4 fake",
            b"%PDF-1.4 fake",
        )

    assert "Low Confidence Warning" in captured["subject"]


@pytest.mark.asyncio
async def test_email_send_skips_gracefully_without_smtp_configured():
    """settings.MAIL_SERVER is empty in this dev/test environment — must not raise."""
    from app.services.email_service import send_brief_ready

    await send_brief_ready(
        "reader@example.com",
        {"topic": "T", "overall_confidence": 0.9, "executive_summary": "x", "agents_used": []},
        b"%PDF-1.4 fake",
        b"%PDF-1.4 fake",
    )  # no exception == pass


# ---------------------------------------------------------------------------
# routes/reports.py — download endpoints + compare, via a real ASGI client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_and_compare_routes(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks_module.settings, "REPORTS_DIR", str(tmp_path))

    tenant_id, brief_id = await _seed_full_brief()

    async with AsyncSessionLocal() as db:
        tenant = await db.get(Tenant, uuid.UUID(tenant_id))

    with patch("app.workers.tasks.email_service.send_brief_ready", new=AsyncMock()):
        await tasks_module._generate_and_deliver_report(brief_id)

    from app.core.auth import create_access_token

    token, _ = create_access_token(tenant.id, tenant.plan)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/v1/briefs/{brief_id}/report/download/pdf", headers=headers)
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"

        r = await client.get(f"/v1/briefs/{brief_id}/report/download/trace", headers=headers)
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"

        r = await client.get(f"/v1/briefs/{brief_id}/report/download/json", headers=headers)
        assert r.status_code == 200
        assert r.json()["topic"] == "AI Chip Market"

        r = await client.get(f"/v1/briefs/{brief_id}/report/preview", headers=headers)
        assert r.status_code == 200
        assert "sections" in r.json()

        brief_id_b = await _seed_second_brief_same_tenant(tenant_id)

        async def fake_llm_call(messages, model=None, temperature=0.1, max_tokens=4096):
            return {
                "content": json.dumps(
                    {"common_findings": ["demand growth"], "contradictions": [], "new_developments": ["new entrant"], "confidence_delta": 0.1}
                ),
                "prompt_tokens": 5,
                "completion_tokens": 5,
                "latency_ms": 1,
            }

        with patch("app.llm.gemini_client.call", new=AsyncMock(side_effect=fake_llm_call)):
            r = await client.post(
                "/v1/briefs/compare",
                headers=headers,
                json={"brief_id_a": brief_id, "brief_id_b": brief_id_b},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["common_findings"] == ["demand growth"]
        assert body["confidence_delta"] == 0.1
