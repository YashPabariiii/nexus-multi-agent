from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import parse_llm_json, record_trace
from app.core.metrics import debate_resolution_total, debate_rounds_total
from app.llm.router import get_llm
from app.tools.search_tool import web_search as web_search_tool

DEBATE_CONFIDENCE_THRESHOLD = 0.4
ORIGINAL_CONFIDENCE_THRESHOLD = 0.7

DEFENSE_SYSTEM_PROMPT = """A fact-checker disputed a claim you produced. Defend it with additional evidence
if it holds up, or retract it if you can no longer support it. Be honest — do not defend an indefensible claim.
Return ONLY valid JSON: {"defense": str, "new_sources": [{"url": str, "title": str}], "retract": bool}"""

VERDICT_SYSTEM_PROMPT = """You are the fact-checker rendering a final verdict after a debate round.
Given the original dispute, the agent's defense, and any new evidence, decide the outcome.
Return ONLY valid JSON: {"upheld": bool, "partial": bool, "final_confidence": float, "reasoning": str}"""


def should_debate(fact_checker_confidence: float, original_confidence: float) -> bool:
    return fact_checker_confidence < DEBATE_CONFIDENCE_THRESHOLD and original_confidence > ORIGINAL_CONFIDENCE_THRESHOLD


async def run_debate_round(
    db: AsyncSession,
    claim: dict,
    fact_checker_dispute_reason: str,
    original_agent_type: str,
    brief_id: str,
    domain: str,
) -> dict:
    defense_llm = get_llm(original_agent_type)

    defense_prompt = (
        f"Your claim: {claim.get('claim')}\nYour original confidence: {claim.get('confidence')}\n"
        f"Fact-checker dispute reason: {fact_checker_dispute_reason}"
    )
    defense_result = await defense_llm.call(
        messages=[{"role": "system", "content": DEFENSE_SYSTEM_PROMPT}, {"role": "user", "content": defense_prompt}],
        model=defense_llm.model,
    )
    defense = parse_llm_json(defense_result["content"])
    if not isinstance(defense, dict):
        defense = {"defense": "", "new_sources": [], "retract": True}

    new_evidence = []
    if not defense.get("retract"):
        new_evidence = web_search_tool.invoke(
            {"query": f"evidence supporting: {claim.get('claim', '')}", "max_results": 3, "domain": domain}
        )

    await record_trace(
        db,
        brief_id=brief_id,
        agent_type=f"{original_agent_type}_debate_defense",
        llm_provider=defense_llm.provider,
        llm_model=defense_llm.model,
        input_summary=defense_prompt[:500],
        output_summary=f"retract={defense.get('retract')} defense={defense.get('defense', '')[:200]}",
        tool_calls=[{"tool": "web_search", "purpose": "debate_supporting_evidence", "result_count": len(new_evidence)}],
        reasoning_trace=defense_result["content"],
        prompt_tokens=defense_result.get("prompt_tokens"),
        completion_tokens=defense_result.get("completion_tokens"),
        latency_ms=defense_result.get("latency_ms"),
    )

    if defense.get("retract"):
        final_confidence = 0.1
        verdict = {"upheld": False, "partial": False, "final_confidence": final_confidence, "reasoning": "agent retracted the claim"}
    else:
        verdict_llm = get_llm("fact_checker")
        evidence_context = "\n".join(f"- {e['title']} ({e['url']}): {e['content'][:400]}" for e in new_evidence)
        verdict_prompt = (
            f"Original dispute: {fact_checker_dispute_reason}\nAgent defense: {defense.get('defense')}\n"
            f"New evidence:\n{evidence_context}"
        )
        verdict_result = await verdict_llm.call(
            messages=[{"role": "system", "content": VERDICT_SYSTEM_PROMPT}, {"role": "user", "content": verdict_prompt}],
            model=verdict_llm.model,
        )
        verdict = parse_llm_json(verdict_result["content"])
        if not isinstance(verdict, dict) or "final_confidence" not in verdict:
            verdict = {"upheld": False, "partial": True, "final_confidence": 0.3, "reasoning": "unparseable debate verdict"}

        if verdict.get("upheld"):
            final_confidence = claim.get("confidence", 0.5)
        elif verdict.get("partial"):
            final_confidence = (claim.get("confidence", 0.5) + verdict.get("final_confidence", 0.3)) / 2
        else:
            final_confidence = verdict.get("final_confidence", 0.3)

        await record_trace(
            db,
            brief_id=brief_id,
            agent_type="fact_checker_debate_verdict",
            llm_provider=verdict_llm.provider,
            llm_model=verdict_llm.model,
            input_summary=verdict_prompt[:500],
            output_summary=f"upheld={verdict.get('upheld')} final_confidence={final_confidence:.2f}",
            reasoning_trace=verdict_result["content"],
            confidence_scores={"final_confidence": final_confidence},
            prompt_tokens=verdict_result.get("prompt_tokens"),
            completion_tokens=verdict_result.get("completion_tokens"),
            latency_ms=verdict_result.get("latency_ms"),
        )

    debate_rounds_total.inc()
    resolution = "retracted" if defense.get("retract") else ("upheld" if verdict.get("upheld") else "partial")
    debate_resolution_total.labels(resolution=resolution).inc()

    return {
        **claim,
        "verified": final_confidence >= 0.5,
        "disputed": final_confidence < 0.5,
        "dispute_reason": None if final_confidence >= 0.5 else verdict.get("reasoning", fact_checker_dispute_reason),
        "final_confidence": round(final_confidence, 3),
        "debated": True,
    }
