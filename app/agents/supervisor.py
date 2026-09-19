from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import parse_llm_json, record_trace
from app.agents.planner import generate_research_plan_context
from app.agents.state import ResearchState
from app.llm.router import get_llm
from app.models.brief import ResearchBrief

AGENT_TYPE = "supervisor"

SYSTEM_PROMPT = """You are a research supervisor managing a team of specialist AI agents.
Given a research brief, create a structured research plan specifying:
1. Which agents to activate (from: web_search, domain_knowledge, data_analyst)
2. Specific tasks per agent
3. Key questions each agent must answer
4. Dependencies between tasks
Return ONLY valid JSON in this shape:
{"agents_needed": [str], "tasks": [{"agent": str, "task": str, "priority": "high|medium|low", "questions": [str]}], "estimated_complexity": "low|medium|high", "depth_justification": str}"""

MAX_ITERATIONS = 2


async def plan(state: ResearchState, db: AsyncSession) -> ResearchState:
    llm = get_llm(AGENT_TYPE)

    # Check the tenant's knowledge base before planning — primes the run with prior findings.
    kb_context = await generate_research_plan_context(state["tenant_id"], state["topic"])

    user_prompt = (
        f"Topic: {state['topic']}\nScope: {state['scope']}\nDomain: {state['domain']}\n"
        f"Depth: {state['depth']}\nAudience: {state['audience']}"
    )
    if kb_context:
        user_prompt += f"\n\nPrior knowledge base context:\n{kb_context}"
    if state["depth"] == "quick":
        user_prompt += "\nConstraint: quick depth — do NOT include data_analyst in agents_needed."

    result = await llm.call(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )
    plan_json = parse_llm_json(result["content"])
    if not isinstance(plan_json, dict) or not plan_json.get("tasks"):
        plan_json = {
            "agents_needed": ["web_search"] if state["depth"] == "quick" else ["web_search", "domain_knowledge", "data_analyst"],
            "tasks": [{"agent": "web_search", "task": f"Research {state['topic']}", "priority": "high", "questions": [state["topic"]]}],
            "estimated_complexity": "medium",
            "depth_justification": "fallback plan (LLM output unparseable)",
        }

    await db.execute(
        update(ResearchBrief)
        .where(ResearchBrief.id == state["brief_id"])
        .values(supervisor_plan=plan_json, status="researching")
    )
    await db.commit()

    await record_trace(
        db,
        brief_id=state["brief_id"],
        agent_type=AGENT_TYPE,
        llm_provider=llm.provider,
        llm_model=llm.model,
        input_summary=user_prompt,
        output_summary=f"Plan: {plan_json.get('agents_needed')} tasks={len(plan_json.get('tasks', []))}",
        reasoning_trace=result["content"],
        prompt_tokens=result.get("prompt_tokens"),
        completion_tokens=result.get("completion_tokens"),
        latency_ms=result.get("latency_ms"),
    )

    return {
        "supervisor_plan": plan_json,
        "research_tasks": plan_json.get("tasks", []),
        "current_stage": "researching",
    }


def check_completion(state: ResearchState) -> str:
    """Conditional edge: decide whether to synthesize or loop back for more research."""
    if state["iteration_count"] >= MAX_ITERATIONS:
        return "synthesize"

    unverified_ratio = 0.0
    if state["all_claims"]:
        unverified = [c for c in state["all_claims"] if c.get("confidence", 1.0) < 0.4]
        unverified_ratio = len(unverified) / len(state["all_claims"])

    if unverified_ratio > 0.5:
        return "research_more"
    return "synthesize"
