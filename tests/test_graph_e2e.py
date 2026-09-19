"""Integration test for the full 7-agent LangGraph pipeline.

Mocks the LLM and search/tool boundaries (no API keys required) but exercises the
real graph, real Postgres persistence (traces/claims/report), and the human_review
interrupt/resume cycle. Requires DATABASE_URL/REDIS_URL pointing at live services
(see scripts/sprint2.sh).
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.auth import generate_api_key, hash_api_key
from app.db.session import AsyncSessionLocal
from app.models.agent_trace import AgentTrace, Claim
from app.models.brief import ResearchBrief
from app.models.report import Report
from app.models.tenant import Tenant

FAKE_SEARCH_RESULTS = [
    {"title": "Test Source", "url": "https://example.com/a", "content": "Revenue grew from 100 to 150 million this year.", "score": 0.9}
]

PLAN_JSON = json.dumps(
    {
        "agents_needed": ["web_search", "domain_knowledge", "data_analyst"],
        "tasks": [
            {"agent": "web_search", "task": "Find facts", "priority": "high", "questions": ["What happened?"]},
            {"agent": "domain_knowledge", "task": "Past research", "priority": "medium", "questions": ["Prior findings?"]},
            {"agent": "data_analyst", "task": "Compute growth", "priority": "medium", "questions": ["Growth rate?"]},
        ],
        "estimated_complexity": "medium",
        "depth_justification": "test",
    }
)

CLAIMS_JSON = json.dumps(
    {"claims": [{"claim": "Revenue grew 50%", "source_url": "https://example.com/a", "source_title": "Test Source", "confidence": 0.8}]}
)

SYNTH_JSON = json.dumps(
    {
        "sections": [{"title": "Revenue Growth", "key_points": ["Revenue grew 50%"], "claims_used": ["0"], "confidence": 0.85}],
        "contradictions_noted": [],
        "gaps_identified": [],
    }
)


def _verdict_json(final_confidence: float, disputed: bool) -> str:
    return json.dumps(
        {
            "verified": not disputed,
            "disputed": disputed,
            "dispute_reason": "insufficient corroboration" if disputed else None,
            "final_confidence": final_confidence,
            "supporting_sources": ["https://example.com/a"],
            "contradicting_sources": [],
        }
    )


def _make_fake_llm_call(verdict_json: str):
    async def fake_llm_call(messages, model=None, temperature=0.1, max_tokens=4096):
        system = messages[0]["content"].lower()
        if "research supervisor" in system:
            content = PLAN_JSON
        elif "web research agent" in system or "domain knowledge agent" in system or "quantitative data analyst" in system:
            content = CLAIMS_JSON
        elif "fact-checker" in system:
            content = verdict_json
        elif "synthesizer" in system:
            content = SYNTH_JSON
        elif "3 concise bullet" in system:
            content = "- Point one\n- Point two\n- Point three"
        else:
            content = "Written section body citing [Test Source, https://example.com/a]."
        return {"content": content, "prompt_tokens": 10, "completion_tokens": 20, "latency_ms": 5}

    return fake_llm_call


async def _seed_tenant_and_brief(depth: str = "standard") -> tuple[str, str]:
    async with AsyncSessionLocal() as db:
        tenant = Tenant(
            name="Test Tenant",
            email=f"test-{uuid.uuid4().hex[:8]}@test.local",
            api_key_hash=hash_api_key(generate_api_key()),
            plan="pro",
        )
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

        brief = ResearchBrief(
            tenant_id=tenant.id,
            topic="AI Chip Market",
            scope="test scope",
            domain="tech",
            depth=depth,
            audience="analyst",
            status="queued",
            consent_store=False,
            user_email="x@test.local",
        )
        db.add(brief)
        await db.commit()
        await db.refresh(brief)

    return str(brief.id), str(tenant.id)


async def _run_pipeline(brief_id: str, tenant_id: str, verdict_json: str):
    fake_llm_call = _make_fake_llm_call(verdict_json)

    with (
        patch("app.llm.groq_client.call", new=AsyncMock(side_effect=fake_llm_call)),
        patch("app.llm.gemini_client.call", new=AsyncMock(side_effect=fake_llm_call)),
        patch("app.tools.search_tool.web_search.func", return_value=FAKE_SEARCH_RESULTS),
        patch("app.tools.chroma_tool.search_knowledge_base.func", return_value=[]),
    ):
        from app.workers import tasks as tasks_module

        async def fake_wait_for_resume(_brief_id):
            return {"action": "approve"}

        with patch.object(tasks_module, "_wait_for_resume", new=fake_wait_for_resume):
            await tasks_module._run_graph(brief_id, tenant_id)


@pytest.mark.asyncio
async def test_graph_end_to_end_verified_claims():
    brief_id, tenant_id = await _seed_tenant_and_brief(depth="standard")
    await _run_pipeline(brief_id, tenant_id, _verdict_json(0.9, disputed=False))

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        brief = await db.get(ResearchBrief, uuid.UUID(brief_id))
        traces = list(await db.scalars(select(AgentTrace).where(AgentTrace.brief_id == uuid.UUID(brief_id))))
        claims = list(await db.scalars(select(Claim).where(Claim.brief_id == uuid.UUID(brief_id))))
        report = await db.scalar(select(Report).where(Report.brief_id == uuid.UUID(brief_id)))

    expected_agents = {"supervisor", "web_search", "domain_knowledge", "data_analyst", "fact_checker", "synthesis", "writer"}
    assert expected_agents.issubset({t.agent_type for t in traces})
    assert brief.status == "complete"
    assert report is not None
    assert all(c.disputed_by_agent is None for c in claims)


@pytest.mark.asyncio
async def test_graph_end_to_end_disputed_claims():
    brief_id, tenant_id = await _seed_tenant_and_brief(depth="standard")
    await _run_pipeline(brief_id, tenant_id, _verdict_json(0.2, disputed=True))

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        claims = list(await db.scalars(select(Claim).where(Claim.brief_id == uuid.UUID(brief_id))))

    assert claims, "expected claims to be persisted"
    assert all(c.disputed_by_agent == "fact_checker" for c in claims)
    assert all(c.final_confidence < 0.5 for c in claims)


@pytest.mark.asyncio
async def test_graph_quick_depth_skips_data_analyst_and_human_review():
    brief_id, tenant_id = await _seed_tenant_and_brief(depth="quick")
    await _run_pipeline(brief_id, tenant_id, _verdict_json(0.9, disputed=False))

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        brief = await db.get(ResearchBrief, uuid.UUID(brief_id))
        traces = list(await db.scalars(select(AgentTrace).where(AgentTrace.brief_id == uuid.UUID(brief_id))))

    assert brief.status == "complete"
    assert "data_analyst" not in {t.agent_type for t in traces}
