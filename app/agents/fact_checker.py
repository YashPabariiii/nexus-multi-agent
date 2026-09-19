import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import parse_llm_json, publish_event, record_trace
from app.agents.debate import run_debate_round, should_debate
from app.agents.state import ResearchState
from app.core.metrics import claims_disputed_total, claims_verified_total
from app.llm.router import get_llm
from app.models.agent_trace import Claim
from app.tools.search_tool import web_search as web_search_tool

AGENT_TYPE = "fact_checker"

SYSTEM_PROMPT = """You are a rigorous fact-checker. Review each claim and:
1. Verify it has a credible source
2. Check for logical consistency
3. Search for contradicting evidence
4. Assign final_confidence 0-1
If a claim is unsupported, set disputed=true and lower confidence significantly.
Be adversarial — your job is to find flaws.
Return ONLY valid JSON:
{"verified": bool, "disputed": bool, "dispute_reason": str or null, "final_confidence": float,
 "supporting_sources": [str], "contradicting_sources": [str]}"""

VERIFIED_THRESHOLD = 0.5


async def _check_claim(claim: dict, domain: str, llm) -> tuple[dict, dict]:
    contradicting = web_search_tool.invoke({"query": f"evidence against: {claim.get('claim', '')}", "max_results": 3, "domain": domain})

    context = "\n".join(f"- {r['title']} ({r['url']}): {r['content'][:400]}" for r in contradicting)
    user_prompt = (
        f"Claim: {claim.get('claim')}\nOriginal source: {claim.get('source_url')}\n"
        f"Original confidence: {claim.get('confidence')}\n\nPotential contradicting evidence:\n{context}"
    )

    result = await llm.call(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )
    verdict = parse_llm_json(result["content"])
    if not isinstance(verdict, dict) or "final_confidence" not in verdict:
        verdict = {
            "verified": bool(claim.get("source_url")),
            "disputed": not bool(claim.get("source_url")),
            "dispute_reason": "unparseable fact-check output" if not claim.get("source_url") else None,
            "final_confidence": claim.get("confidence", 0.3) if claim.get("source_url") else 0.2,
            "supporting_sources": [],
            "contradicting_sources": [],
        }
    return result, verdict


async def check(state: ResearchState, db: AsyncSession) -> ResearchState:
    llm = get_llm(AGENT_TYPE)
    claims = state["all_claims"]
    if not claims:
        return {"iteration_count": state.get("iteration_count", 0) + 1}

    checks = await asyncio.gather(*[_check_claim(c, state["domain"], llm) for c in claims])

    verified_claims, disputed_claims = [], []
    for claim, (llm_result, verdict) in zip(claims, checks):
        enriched = {
            **claim,
            "verified": verdict.get("verified", False),
            "disputed": verdict.get("disputed", False),
            "dispute_reason": verdict.get("dispute_reason"),
            "final_confidence": verdict.get("final_confidence", 0.0),
        }

        if should_debate(enriched["final_confidence"], claim.get("confidence", 0.0)):
            enriched = await run_debate_round(
                db,
                claim=claim,
                fact_checker_dispute_reason=verdict.get("dispute_reason") or "confidence too low",
                original_agent_type=claim.get("agent_id", "web_search"),
                brief_id=state["brief_id"],
                domain=state["domain"],
            )
            await publish_event(
                brief_id=state["brief_id"],
                event_type="debate_resolved",
                agent_type="fact_checker",
                message=f"Debate on '{claim.get('claim', '')[:100]}' -> final_confidence={enriched['final_confidence']:.2f}",
                confidence=enriched["final_confidence"],
            )

        db.add(
            Claim(
                brief_id=uuid.UUID(state["brief_id"]),
                agent_id=claim.get("agent_id", "unknown"),
                claim_text=claim.get("claim", "")[:4000],
                source_url=claim.get("source_url"),
                source_title=claim.get("source_title"),
                confidence=claim.get("confidence", 0.0),
                verified=enriched.get("verified"),
                disputed_by_agent=AGENT_TYPE if enriched.get("disputed") else None,
                dispute_reason=enriched.get("dispute_reason"),
                final_confidence=enriched.get("final_confidence"),
            )
        )

        if enriched["final_confidence"] >= VERIFIED_THRESHOLD:
            verified_claims.append(enriched)
            claims_verified_total.inc()
        else:
            disputed_claims.append(enriched)
            claims_disputed_total.inc()
            await publish_event(
                brief_id=state["brief_id"],
                event_type="claim_disputed",
                agent_type=AGENT_TYPE,
                message=f"Disputed: {claim.get('claim', '')[:150]}",
                confidence=enriched["final_confidence"],
            )

        await record_trace(
            db,
            brief_id=state["brief_id"],
            agent_type=AGENT_TYPE,
            llm_provider=llm.provider,
            llm_model=llm.model,
            input_summary=claim.get("claim", "")[:500],
            output_summary=f"verified={verdict.get('verified')} confidence={verdict.get('final_confidence')}",
            tool_calls=[{"tool": "web_search", "purpose": "contradiction_search"}],
            reasoning_trace=llm_result["content"],
            claims=[enriched],
            confidence_scores={"final_confidence": verdict.get("final_confidence", 0.0)},
            prompt_tokens=llm_result.get("prompt_tokens"),
            completion_tokens=llm_result.get("completion_tokens"),
            latency_ms=llm_result.get("latency_ms"),
        )

    await db.commit()

    return {
        "verified_claims": verified_claims,
        "disputed_claims": disputed_claims,
        "iteration_count": state.get("iteration_count", 0) + 1,
    }
