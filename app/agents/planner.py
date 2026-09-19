from app.agents import memory
from app.agents.common import parse_llm_json, record_trace
from app.llm.router import get_llm

MAX_REPLANS = 2

COMPLEXITY_SYSTEM_PROMPT = """You are a research planning assistant. Given a topic, scope, and domain,
assess how complex the research will be.
Return ONLY valid JSON:
{"complexity": "low|medium|high", "recommended_depth": "quick|standard|deep",
 "key_research_questions": [5 questions], "potential_data_sources": [str],
 "estimated_minutes": int}"""

REPLAN_SYSTEM_PROMPT = """You are a research supervisor issuing targeted follow-up tasks to close research gaps.
Given the gaps identified by the fact-checker and the current research plan, generate a short list of
follow-up tasks for specific agents (web_search, domain_knowledge, data_analyst) that would resolve the gaps.
Return ONLY valid JSON:
{"tasks": [{"agent": str, "task": str, "priority": "high|medium|low", "questions": [str]}]}"""


async def assess_brief_complexity(topic: str, scope: str, domain: str) -> dict:
    llm = get_llm("supervisor")
    user_prompt = f"Topic: {topic}\nScope: {scope}\nDomain: {domain}"
    result = await llm.call(
        messages=[{"role": "system", "content": COMPLEXITY_SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )
    parsed = parse_llm_json(result["content"])
    if not isinstance(parsed, dict) or "recommended_depth" not in parsed:
        parsed = {
            "complexity": "medium",
            "recommended_depth": "standard",
            "key_research_questions": [topic],
            "potential_data_sources": [],
            "estimated_minutes": 8,
        }
    return parsed


async def dynamic_replan(state, gaps: list[str], db=None) -> dict:
    """Called when fact_checker finds major gaps. Bounded by MAX_REPLANS via the caller's
    iteration_count check (see supervisor.check_completion)."""
    llm = get_llm("supervisor")
    user_prompt = (
        f"Topic: {state['topic']}\nOriginal plan: {state.get('supervisor_plan')}\n"
        f"Gaps identified (disputed/low-confidence claims):\n" + "\n".join(f"- {g}" for g in gaps)
    )
    result = await llm.call(
        messages=[{"role": "system", "content": REPLAN_SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )
    parsed = parse_llm_json(result["content"])
    if not isinstance(parsed, dict) or not parsed.get("tasks"):
        parsed = {"tasks": [{"agent": "web_search", "task": f"Re-investigate: {state['topic']}", "priority": "high", "questions": gaps[:3]}]}

    if db is not None:
        await record_trace(
            db,
            brief_id=state["brief_id"],
            agent_type="supervisor",
            llm_provider=llm.provider,
            llm_model=llm.model,
            input_summary=f"replan for {len(gaps)} gaps",
            output_summary=f"{len(parsed['tasks'])} follow-up tasks issued",
            reasoning_trace=result["content"],
            prompt_tokens=result.get("prompt_tokens"),
            completion_tokens=result.get("completion_tokens"),
            latency_ms=result.get("latency_ms"),
        )

    return parsed


async def generate_research_plan_context(tenant_id: str, topic: str) -> str:
    """Checks the tenant's knowledge base before planning. Returns a context string to prime
    the domain_knowledge agent, or "" if nothing relevant was found."""
    hits = memory.search_semantic(tenant_id, topic, top_k=3)
    if not hits:
        return ""

    best = hits[0]
    created_at = best.get("created_at", "an earlier date")
    return (
        f"We researched '{best.get('topic', topic)}' on {created_at} (similarity={best.get('similarity')}). "
        "Focus this research on what has changed since, rather than re-deriving known findings."
    )
