import asyncio
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import parse_llm_json, record_trace
from app.agents.state import ResearchState
from app.llm.router import get_llm
from app.tools.calculator_tool import calculate
from app.tools.search_tool import web_search as web_search_tool

AGENT_TYPE = "data_analyst"

SYSTEM_PROMPT = """You are a quantitative data analyst agent. Given search results and computed metrics,
interpret the data into structured claims. Prefer numeric, verifiable statements.
Return ONLY valid JSON: {"claims": [{"claim": str, "source_url": str, "source_title": str, "confidence": float}],
"metrics_computed": [{"expression": str, "result": float, "label": str}]}"""

_NUMBER_PAIR = re.compile(r"(-?\d+(?:\.\d+)?)")


def _attempt_growth_calculations(search_results: list[dict]) -> list[dict]:
    """ponytail: naive numeric-pair growth-rate scan over search snippets; a real data pipeline would
    extract structured figures per domain instead of regexing prose."""
    computed = []
    for r in search_results:
        numbers = _NUMBER_PAIR.findall(r.get("content", ""))
        if len(numbers) >= 2:
            try:
                old, new = float(numbers[0]), float(numbers[1])
                if old != 0:
                    growth = calculate.invoke({"expression": f"(({new}-{old})/{old})*100"})
                    computed.append({"expression": f"growth({old}->{new})", "result": growth, "label": r.get("title", "")})
            except (ValueError, ZeroDivisionError):
                continue
    return computed[:5]


async def _research_task(task: dict, domain: str, depth: str, llm) -> tuple[dict, list[dict], dict, list[dict]]:
    questions = task.get("questions") or [task.get("task", "")]
    query = " ".join(questions)[:400]

    search_results = web_search_tool.invoke({"query": query, "max_results": 5, "domain": domain, "depth": depth})
    computed_metrics = _attempt_growth_calculations(search_results)

    context = "\n".join(f"- {r['title']} ({r['url']}): {r['content'][:500]}" for r in search_results)
    metrics_context = "\n".join(f"- {m['label']}: {m['expression']} = {m['result']:.2f}" for m in computed_metrics)
    user_prompt = f"Domain: {domain}\nTask: {task.get('task')}\n\nSearch results:\n{context}\n\nComputed metrics:\n{metrics_context}"

    result = await llm.call(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )
    parsed = parse_llm_json(result["content"])
    claims = parsed.get("claims", []) if isinstance(parsed, dict) else []
    for c in claims:
        c["agent_id"] = AGENT_TYPE

    tool_calls = [
        {"tool": "web_search", "query": query, "result_count": len(search_results)},
        {"tool": "calculate", "computed_count": len(computed_metrics)},
    ]
    return result, claims, tool_calls, computed_metrics


async def research(state: ResearchState, db: AsyncSession) -> ResearchState:
    if state["depth"] == "quick":
        return {}

    llm = get_llm(AGENT_TYPE)
    tasks = [t for t in state["research_tasks"] if t.get("agent") == AGENT_TYPE]
    if not tasks:
        return {}

    results = await asyncio.gather(*[_research_task(t, state["domain"], state["depth"], llm) for t in tasks])

    all_claims = []
    analyst_results = []
    for (llm_result, claims, tool_calls, computed_metrics), task in zip(results, tasks):
        all_claims.extend(claims)
        analyst_results.append({"task": task.get("task"), "claims": claims, "metrics": computed_metrics})

        await record_trace(
            db,
            brief_id=state["brief_id"],
            agent_type=AGENT_TYPE,
            llm_provider=llm.provider,
            llm_model=llm.model,
            input_summary=task.get("task", ""),
            output_summary=f"{len(claims)} claims, {len(computed_metrics)} metrics computed",
            tool_calls=tool_calls,
            reasoning_trace=llm_result["content"],
            claims=claims,
            prompt_tokens=llm_result.get("prompt_tokens"),
            completion_tokens=llm_result.get("completion_tokens"),
            latency_ms=llm_result.get("latency_ms"),
        )

    return {"data_analyst_results": analyst_results, "all_claims": all_claims}
