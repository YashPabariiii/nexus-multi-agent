import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import record_trace
from app.agents.state import ResearchState
from app.llm.router import get_llm

AGENT_TYPE = "writer"

AUDIENCE_PROMPTS = {
    "executive": "Concise, strategic, 3-5 bullets per section, no jargon, ROI focus.",
    "analyst": "Detailed, data-driven, include metrics, compare alternatives.",
    "technical": "Deep technical detail, include methodology, architecture notes.",
}


def _system_prompt(audience: str) -> str:
    style = AUDIENCE_PROMPTS.get(audience, AUDIENCE_PROMPTS["analyst"])
    return (
        f"You are a research report writer. Style: {style}\n"
        "Write full prose for the given section using ONLY the supplied key points and claims.\n"
        "Embed citations inline as [Source Title, URL]. Do not invent sources.\n"
        "Return plain prose text only, no JSON, no markdown headers."
    )


async def _write_section(section: dict, claims_by_index: list[dict], audience: str, llm) -> tuple[dict, dict]:
    citations = [
        {"title": c.get("source_title"), "url": c.get("source_url")}
        for c in claims_by_index
        if c.get("source_url")
    ]
    key_points = "\n".join(f"- {p}" for p in section.get("key_points", []))
    user_prompt = f"Section title: {section['title']}\nKey points:\n{key_points}\nAvailable citations: {citations}"

    result = await llm.call(
        messages=[{"role": "system", "content": _system_prompt(audience)}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )

    written = {
        "title": section["title"],
        "content": result["content"].strip(),
        "citations": citations,
        "confidence": section.get("confidence", 0.5),
    }
    return result, written


async def write(state: ResearchState, db: AsyncSession) -> ResearchState:
    llm = get_llm(AGENT_TYPE)
    sections = state["synthesis_output"].get("sections", [])
    verified_claims = state["verified_claims"]

    results = await asyncio.gather(*[_write_section(s, verified_claims, state["audience"], llm) for s in sections])

    written_sections = []
    for llm_result, written in results:
        written_sections.append(written)
        await record_trace(
            db,
            brief_id=state["brief_id"],
            agent_type=AGENT_TYPE,
            llm_provider=llm.provider,
            llm_model=llm.model,
            input_summary=written["title"],
            output_summary=written["content"][:300],
            reasoning_trace=llm_result["content"],
            prompt_tokens=llm_result.get("prompt_tokens"),
            completion_tokens=llm_result.get("completion_tokens"),
            latency_ms=llm_result.get("latency_ms"),
        )

    exec_summary_prompt = (
        "Write a 3-bullet executive summary of this research, based on these section summaries:\n"
        + "\n".join(f"- {s['title']}: {s['content'][:200]}" for s in written_sections)
    )
    exec_result = await llm.call(
        messages=[
            {"role": "system", "content": "Write exactly 3 concise bullet points. Plain text, one bullet per line, prefixed with '- '."},
            {"role": "user", "content": exec_summary_prompt},
        ],
        model=llm.model,
    )

    confidences = [s["confidence"] for s in written_sections if s.get("confidence") is not None]
    overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    word_count = sum(len(s["content"].split()) for s in written_sections)

    draft_report = {
        "title": f"{state['topic']} — Research Brief",
        "executive_summary": exec_result["content"].strip(),
        "sections": written_sections,
        "overall_confidence": round(overall_confidence, 3),
        "word_count": word_count,
    }

    await record_trace(
        db,
        brief_id=state["brief_id"],
        agent_type=AGENT_TYPE,
        llm_provider=llm.provider,
        llm_model=llm.model,
        input_summary="executive_summary",
        output_summary=draft_report["executive_summary"][:300],
        reasoning_trace=exec_result["content"],
        prompt_tokens=exec_result.get("prompt_tokens"),
        completion_tokens=exec_result.get("completion_tokens"),
        latency_ms=exec_result.get("latency_ms"),
    )

    return {"draft_report": draft_report, "current_stage": "awaiting_review"}
