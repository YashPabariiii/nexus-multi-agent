import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import parse_llm_json, record_trace
from app.agents.state import ResearchState
from app.llm.router import get_llm
from app.tools.search_tool import web_search as web_search_tool

AGENT_TYPE = "web_search"

SYSTEM_PROMPT = """You are a web research agent. Search for information and extract factual claims with
their sources. For each claim include the source URL and your confidence 0-1.
Return ONLY valid JSON: {"claims": [{"claim": str, "source_url": str, "source_title": str, "confidence": float}]}"""


async def _research_task(task: dict, domain: str, depth: str, llm) -> tuple[dict, list[dict], dict]:
    questions = task.get("questions") or [task.get("task", "")]
    query = " ".join(questions)[:400]

    search_results = web_search_tool.invoke({"query": query, "max_results": 5, "domain": domain, "depth": depth})

    context = "\n".join(f"- {r['title']} ({r['url']}): {r['content'][:500]}" for r in search_results)
    user_prompt = f"Task: {task.get('task')}\nQuestions: {questions}\n\nSearch results:\n{context}"

    result = await llm.call(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )
    parsed = parse_llm_json(result["content"])
    claims = parsed.get("claims", []) if isinstance(parsed, dict) else []
    for c in claims:
        c["agent_id"] = AGENT_TYPE

    tool_call_record = {"tool": "web_search", "query": query, "result_count": len(search_results)}
    return result, claims, tool_call_record


async def research(state: ResearchState, db: AsyncSession) -> ResearchState:
    llm = get_llm(AGENT_TYPE)
    tasks = [t for t in state["research_tasks"] if t.get("agent") == AGENT_TYPE] or [
        {"task": state["topic"], "questions": [state["topic"]]}
    ]

    results = await asyncio.gather(*[_research_task(t, state["domain"], state["depth"], llm) for t in tasks])

    all_claims = []
    web_results = []
    for (llm_result, claims, tool_call), task in zip(results, tasks):
        all_claims.extend(claims)
        web_results.append({"task": task.get("task"), "claims": claims})

        await record_trace(
            db,
            brief_id=state["brief_id"],
            agent_type=AGENT_TYPE,
            llm_provider=llm.provider,
            llm_model=llm.model,
            input_summary=task.get("task", ""),
            output_summary=f"{len(claims)} claims extracted",
            tool_calls=[tool_call],
            reasoning_trace=llm_result["content"],
            claims=claims,
            prompt_tokens=llm_result.get("prompt_tokens"),
            completion_tokens=llm_result.get("completion_tokens"),
            latency_ms=llm_result.get("latency_ms"),
        )

    return {"web_search_results": web_results, "all_claims": all_claims}
