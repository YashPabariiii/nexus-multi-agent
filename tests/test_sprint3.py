"""Sprint 3: dynamic planning, memory, debate protocol, confidence propagation, knowledge write-back.
Requires live Postgres/Redis/Chroma (see RUNBOOK.md) — no real LLM API keys needed (mocked).
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents import memory
from app.core.auth import generate_api_key, hash_api_key
from app.db.session import AsyncSessionLocal
from app.models.brief import ResearchBrief
from app.models.report import Report
from app.models.tenant import Tenant
from app.services import confidence_service, knowledge_service

# ---------------------------------------------------------------------------
# confidence_service — pure functions, no infra needed
# ---------------------------------------------------------------------------


def test_claim_confidence_trusted_source_boost():
    claim = {"confidence": 0.6, "source_url": "https://sec.gov/filing"}
    score = confidence_service.claim_confidence(claim, domain="finance")
    assert score == pytest.approx(0.7)


def test_claim_confidence_dispute_and_retraction_penalties():
    claim = {"confidence": 0.8}
    disputed = confidence_service.claim_confidence(claim, disputed=True)
    retracted = confidence_service.claim_confidence(claim, retracted=True)
    assert disputed == pytest.approx(0.5)
    assert retracted == pytest.approx(0.3)


def test_claim_confidence_clamped_to_0_1():
    assert confidence_service.claim_confidence({"confidence": 0.95, "source_url": "https://sec.gov"}, domain="finance", corroboration_count=3) == 1.0
    assert confidence_service.claim_confidence({"confidence": 0.1}, retracted=True) == 0.0


def test_section_and_report_confidence_weighting():
    claims = [{"confidence": 0.9, "importance": 2.0}, {"confidence": 0.3, "importance": 1.0}]
    section_score = confidence_service.section_confidence(claims)
    assert section_score == pytest.approx((0.9 * 2 + 0.3 * 1) / 3, rel=1e-3)

    sections = [{"content": "a " * 100, "confidence": 0.9}, {"content": "b " * 300, "confidence": 0.3}]
    report_score = confidence_service.report_confidence(sections)
    assert 0.3 < report_score < 0.9  # weighted toward the longer, lower-confidence section


def test_confidence_label_thresholds():
    assert confidence_service.confidence_label(0.85) == "HIGH"
    assert confidence_service.confidence_label(0.65) == "MEDIUM"
    assert confidence_service.confidence_label(0.45) == "LOW"
    assert confidence_service.confidence_label(0.1) == "UNVERIFIED"


# ---------------------------------------------------------------------------
# memory.py — episodic (Redis) + semantic (Chroma), against live local services
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_episodic_memory_roundtrip():
    brief_id = str(uuid.uuid4())
    await memory.write_episodic(brief_id, "web_search", "found 3 sources on topic X")
    value = await memory.get_episodic(brief_id, "web_search")
    assert value == "found 3 sources on topic X"

    all_mem = await memory.get_all_episodic(brief_id)
    assert all_mem == {"web_search": "found 3 sources on topic X"}


def test_semantic_memory_write_and_search():
    tenant_id = str(uuid.uuid4())
    brief_id = str(uuid.uuid4())
    ids = memory.write_semantic(
        tenant_id=tenant_id,
        brief_id=brief_id,
        content_chunks=["AI chip demand grew sharply in 2025 due to datacenter buildouts."],
        topic="AI Chip Market",
        domain="tech",
        confidence=0.8,
    )
    assert len(ids) == 1

    hits = memory.search_semantic(tenant_id, "AI chip demand growth", top_k=3)
    assert hits
    assert hits[0]["brief_id"] == brief_id
    assert hits[0]["topic"] == "AI Chip Market"


# ---------------------------------------------------------------------------
# debate.py — trigger condition + full round persists agent_traces
# ---------------------------------------------------------------------------


def test_should_debate_trigger_condition():
    from app.agents.debate import should_debate

    assert should_debate(fact_checker_confidence=0.2, original_confidence=0.8) is True
    assert should_debate(fact_checker_confidence=0.5, original_confidence=0.8) is False
    assert should_debate(fact_checker_confidence=0.2, original_confidence=0.5) is False


@pytest.mark.asyncio
async def test_debate_round_retraction_persists_traces():
    from sqlalchemy import select

    from app.agents.debate import run_debate_round
    from app.models.agent_trace import AgentTrace

    async def fake_llm_call(messages, model=None, temperature=0.1, max_tokens=4096):
        return {"content": json.dumps({"defense": "cannot substantiate further", "new_sources": [], "retract": True}), "prompt_tokens": 5, "completion_tokens": 5, "latency_ms": 1}

    _tenant_id, brief_id = await _seed_tenant_brief_report()
    async with AsyncSessionLocal() as db:
        with patch("app.llm.groq_client.call", new=AsyncMock(side_effect=fake_llm_call)):
            result = await run_debate_round(
                db,
                claim={"claim": "Revenue tripled", "confidence": 0.8, "source_url": "https://example.com", "agent_id": "web_search"},
                fact_checker_dispute_reason="no corroborating source found",
                original_agent_type="web_search",
                brief_id=brief_id,
                domain="tech",
            )

        assert result["final_confidence"] == 0.1
        assert result["disputed"] is True

        traces = list(await db.scalars(select(AgentTrace).where(AgentTrace.brief_id == uuid.UUID(brief_id))))
        assert any(t.agent_type == "web_search_debate_defense" for t in traces)


# ---------------------------------------------------------------------------
# knowledge_service.py — index_brief + get_related_briefs against live Chroma/Postgres
# ---------------------------------------------------------------------------


async def _seed_tenant_brief_report():
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="KB Tenant", email=f"kb-{uuid.uuid4().hex[:8]}@test.local", api_key_hash=hash_api_key(generate_api_key()), plan="pro")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

        brief = ResearchBrief(
            tenant_id=tenant.id, topic="Quantum Computing Startups", scope="s", domain="tech",
            depth="standard", audience="analyst", status="complete", consent_store=True, user_email="x@test.local",
        )
        db.add(brief)
        await db.commit()
        await db.refresh(brief)

        report = Report(
            brief_id=brief.id, tenant_id=tenant.id, title="Quantum Computing Startups — Research Brief",
            executive_summary="Quantum computing funding doubled in 2025.",
            sections=[{"title": "Funding", "content": "Quantum computing funding doubled in 2025 " * 50, "citations": [], "confidence": 0.8}],
            overall_confidence=0.8, word_count=400,
        )
        db.add(report)
        await db.commit()

    return str(tenant.id), str(brief.id)


@pytest.mark.asyncio
async def test_index_brief_and_get_related_briefs():
    tenant_id, brief_id = await _seed_tenant_brief_report()

    async with AsyncSessionLocal() as db:
        entries_count = await knowledge_service.index_brief(db, brief_id, tenant_id)

    assert entries_count >= 1

    related = knowledge_service.get_related_briefs(tenant_id, "quantum computing funding trends", limit=3)
    assert related
    assert related[0]["brief_id"] == brief_id


# ---------------------------------------------------------------------------
# planner.py — assess_brief_complexity + knowledge-base priming context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assess_brief_complexity_parses_llm_json():
    from app.agents.planner import assess_brief_complexity

    async def fake_llm_call(messages, model=None, temperature=0.1, max_tokens=4096):
        return {
            "content": json.dumps(
                {
                    "complexity": "high",
                    "recommended_depth": "deep",
                    "key_research_questions": ["q1", "q2", "q3", "q4", "q5"],
                    "potential_data_sources": ["sec.gov"],
                    "estimated_minutes": 15,
                }
            ),
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "latency_ms": 1,
        }

    with patch("app.llm.groq_client.call", new=AsyncMock(side_effect=fake_llm_call)):
        result = await assess_brief_complexity("AI Chip Market", "global players", "tech")

    assert result["complexity"] == "high"
    assert result["recommended_depth"] == "deep"
    assert len(result["key_research_questions"]) == 5


@pytest.mark.asyncio
async def test_generate_research_plan_context_uses_prior_knowledge():
    from app.agents.planner import generate_research_plan_context

    tenant_id, brief_id = await _seed_tenant_brief_report()
    async with AsyncSessionLocal() as db:
        await knowledge_service.index_brief(db, brief_id, tenant_id)

    context = await generate_research_plan_context(tenant_id, "quantum computing startup funding")
    assert "Quantum Computing Startups" in context or "quantum" in context.lower()

    empty_context = await generate_research_plan_context(str(uuid.uuid4()), "completely unrelated topic xyz")
    assert empty_context == ""
