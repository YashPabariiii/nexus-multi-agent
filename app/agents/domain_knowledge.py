import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.common import parse_llm_json, record_trace
from app.agents.state import ResearchState
from app.llm.router import get_llm
from app.tools.chroma_tool import search_knowledge_base

AGENT_TYPE = "domain_knowledge"

SYSTEM_PROMPT = """You are a domain knowledge agent. You are given excerpts of the tenant's past research.
Synthesize the relevant findings into claims, citing the originating past brief.
Return ONLY valid JSON: {"claims": [{"claim": str, "source_url": str, "source_title": str, "confidence": float}]}
If there is no relevant past research, return {"claims": []}."""


async def _research_task(task: dict, tenant_id: str, llm) -> tuple[dict, list[dict], dict]:
    questions = task.get("questions") or [task.get("task", "")]
    query = " ".join(questions)[:400]

    kb_results = search_knowledge_base.invoke({"query": query, "tenant_id": tenant_id, "top_k": 5})

    if not kb_results:
        return {"content": "", "prompt_tokens": None, "completion_tokens": None, "latency_ms": 0}, [], {
            "tool": "search_knowledge_base",
            "query": query,
            "result_count": 0,
        }

    context = "\n".join(f"- [{r.get('topic')} / brief {r.get('brief_id')}]: {r['content'][:500]}" for r in kb_results)
    user_prompt = f"Task: {task.get('task')}\nQuestions: {questions}\n\nPast research excerpts:\n{context}"

    result = await llm.call(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        model=llm.model,
    )
    parsed = parse_llm_json(result["content"])
    claims = parsed.get("claims", []) if isinstance(parsed, dict) else []
    for c in claims:
        c["agent_id"] = AGENT_TYPE

    return result, claims, {"tool": "search_knowledge_base", "query": query, "result_count": len(kb_results)}


async def research(state: ResearchState, db: AsyncSession) -> ResearchState:
    llm = get_llm(AGENT_TYPE)
    tasks = [t for t in state["research_tasks"] if t.get("agent") == AGENT_TYPE]
    if not tasks:
        return {}

    results = await asyncio.gather(*[_research_task(t, state["tenant_id"], llm) for t in tasks])

    all_claims = []
    domain_results = []
    for (llm_result, claims, tool_call), task in zip(results, tasks):
        all_claims.extend(claims)
        domain_results.append({"task": task.get("task"), "claims": claims})

        await record_trace(
            db,
            brief_id=state["brief_id"],
            agent_type=AGENT_TYPE,
            llm_provider=llm.provider,
            llm_model=llm.model,
            input_summary=task.get("task", ""),
            output_summary=f"{len(claims)} claims from knowledge base",
            tool_calls=[tool_call],
            reasoning_trace=llm_result["content"],
            claims=claims,
            prompt_tokens=llm_result.get("prompt_tokens"),
            completion_tokens=llm_result.get("completion_tokens"),
            latency_ms=llm_result.get("latency_ms"),
        )

    return {"domain_knowledge_results": domain_results, "all_claims": all_claims}
