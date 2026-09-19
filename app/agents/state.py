import operator
from typing import Annotated, TypedDict


class ResearchState(TypedDict):
    brief_id: str
    tenant_id: str
    topic: str
    scope: str
    domain: str
    depth: str
    audience: str
    supervisor_plan: dict
    research_tasks: list[dict]
    # written by parallel research nodes (web_search/domain_knowledge/data_analyst) — needs a merge reducer
    web_search_results: Annotated[list[dict], operator.add]
    domain_knowledge_results: Annotated[list[dict], operator.add]
    data_analyst_results: Annotated[list[dict], operator.add]
    all_claims: Annotated[list[dict], operator.add]
    verified_claims: list[dict]
    disputed_claims: list[dict]
    synthesis_output: dict
    draft_report: dict
    final_report: dict
    human_feedback: dict | None
    agent_traces: Annotated[list[dict], operator.add]
    current_stage: str
    errors: Annotated[list[str], operator.add]
    iteration_count: int


def initial_state(brief_id: str, tenant_id: str, topic: str, scope: str, domain: str, depth: str, audience: str) -> ResearchState:
    return ResearchState(
        brief_id=brief_id,
        tenant_id=tenant_id,
        topic=topic,
        scope=scope,
        domain=domain,
        depth=depth,
        audience=audience,
        supervisor_plan={},
        research_tasks=[],
        web_search_results=[],
        domain_knowledge_results=[],
        data_analyst_results=[],
        all_claims=[],
        verified_claims=[],
        disputed_claims=[],
        synthesis_output={},
        draft_report={},
        final_report={},
        human_feedback=None,
        agent_traces=[],
        current_stage="queued",
        errors=[],
        iteration_count=0,
    )
