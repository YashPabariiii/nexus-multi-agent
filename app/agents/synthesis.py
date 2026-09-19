from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import parse_llm_json, record_trace
from app.agents.state import ResearchState
from app.llm.router import get_llm

AGENT_TYPE = "synthesis"

SYSTEM_PROMPT = """You are a research synthesizer. Given verified claims from multiple agents, structure them
into coherent report sections. Resolve any remaining contradictions by noting both perspectives.
Maintain the citation chain back to source claims.
Return ONLY valid JSON:
{"sections": [{"title": str, "key_points": [str], "claims_used": [str], "confidence": float}],
 "contradictions_noted": [str], "gaps_identified": [str]}"""


async def synthesize(state: ResearchState, db: AsyncSession) -> ResearchState:
    llm = get_llm(AGENT_TYPE)

    claims_text = "\n".join(
        f"[{i}] ({c.get('final_confidence', c.get('confidence', 0)):.2f}) {c.get('claim')} — {c.get('source_title', c.get('source_url', ''))}"
        for i, c in enumerate(state["verified_claims"])
    )
    disputed_text = "\n".join(f"- {c.get('claim')}: {c.get('dispute_reason')}" for c in state["disputed_claims"])

    user_prompt = (
        f"Topic: {state['topic']}\nDomain: {state['domain']}\nAudience: {state['audience']}\n"
        f"Research plan: {state['supervisor_plan']}\n\nVerified claims:\n{claims_text}\n\nDisputed claims:\n{disputed_text}"
    )

    result = await llm.call(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )
    synthesis_output = parse_llm_json(result["content"])
    if not isinstance(synthesis_output, dict) or not synthesis_output.get("sections"):
        synthesis_output = {
            "sections": [
                {
                    "title": "Findings",
                    "key_points": [c.get("claim") for c in state["verified_claims"][:10]],
                    "claims_used": [],
                    "confidence": 0.5,
                }
            ],
            "contradictions_noted": [],
            "gaps_identified": ["synthesis LLM output unparseable — fallback structure used"],
        }

    await record_trace(
        db,
        brief_id=state["brief_id"],
        agent_type=AGENT_TYPE,
        llm_provider=llm.provider,
        llm_model=llm.model,
        input_summary=f"{len(state['verified_claims'])} verified, {len(state['disputed_claims'])} disputed",
        output_summary=f"{len(synthesis_output.get('sections', []))} sections synthesized",
        reasoning_trace=result["content"],
        prompt_tokens=result.get("prompt_tokens"),
        completion_tokens=result.get("completion_tokens"),
        latency_ms=result.get("latency_ms"),
    )

    return {"synthesis_output": synthesis_output, "current_stage": "synthesizing"}
