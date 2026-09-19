from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents import data_analyst, domain_knowledge, fact_checker, planner, supervisor, synthesis, web_search, writer
from app.agents.common import publish_event
from app.agents.state import ResearchState
from app.core import tracing
from app.db.session import AsyncSessionLocal


def _with_db(agent_fn, name: str):
    """Wrap an agent's (state, db) coroutine so LangGraph nodes only need (state), and
    open a Langfuse `agent` span named after the node so LLM/tool calls nest under it."""

    async def node(state: ResearchState) -> ResearchState:
        with tracing.observation(name, as_type="agent", input={"topic": state["topic"], "stage": state.get("current_stage")}) as span:
            async with AsyncSessionLocal() as db:
                out = await agent_fn(state, db)
            span.update(output={k: (f"{len(v)} items" if isinstance(v, list) else v) for k, v in out.items()})
            return out

    return node


async def _replan(state: ResearchState, db) -> ResearchState:
    """Dynamic re-planning: fact_checker found major gaps, so the supervisor issues
    targeted follow-up tasks instead of blindly re-running the full original plan."""
    gaps = [c.get("claim", "") for c in state.get("disputed_claims", [])] or [state["topic"]]
    replanned = await planner.dynamic_replan(state, gaps, db=db)
    return {"research_tasks": replanned["tasks"]}


async def _human_review(state: ResearchState) -> ResearchState:
    await publish_event(
        brief_id=state["brief_id"],
        event_type="human_review",
        agent_type="human_review",
        message="Draft report awaiting human review",
        confidence=state["draft_report"].get("overall_confidence"),
    )
    feedback = interrupt({"status": "awaiting_review", "draft_report": state["draft_report"]})
    return {"human_feedback": feedback}


def _review_decision(state: ResearchState) -> str:
    feedback = state.get("human_feedback") or {}
    if feedback.get("action") == "revise":
        return "synthesize"
    return "complete"


async def _complete(state: ResearchState) -> ResearchState:
    return {"final_report": state["draft_report"], "current_stage": "complete"}


def build_graph(depth: str):
    graph = StateGraph(ResearchState)

    graph.add_node("supervisor_plan", _with_db(supervisor.plan, "supervisor_plan"))
    graph.add_node("web_search", _with_db(web_search.research, "web_search"))
    graph.add_node("domain_knowledge", _with_db(domain_knowledge.research, "domain_knowledge"))
    graph.add_node("data_analyst", _with_db(data_analyst.research, "data_analyst"))
    graph.add_node("fact_check", _with_db(fact_checker.check, "fact_check"))
    graph.add_node("replan", _with_db(_replan, "replan"))
    graph.add_node("synthesize", _with_db(synthesis.synthesize, "synthesize"))
    graph.add_node("write", _with_db(writer.write, "write"))
    graph.add_node("complete", _complete)

    graph.add_edge(START, "supervisor_plan")

    research_nodes = ["web_search", "domain_knowledge"] if depth == "quick" else ["web_search", "domain_knowledge", "data_analyst"]
    for node_name in research_nodes:
        graph.add_edge("supervisor_plan", node_name)
        graph.add_edge(node_name, "fact_check")

    graph.add_conditional_edges(
        "fact_check",
        supervisor.check_completion,
        {"synthesize": "synthesize", "research_more": "replan"},
    )
    graph.add_edge("replan", "web_search")
    graph.add_edge("synthesize", "write")

    if depth == "quick":
        graph.add_edge("write", "complete")
    else:
        graph.add_node("human_review", _human_review)
        graph.add_edge("write", "human_review")
        graph.add_conditional_edges(
            "human_review",
            _review_decision,
            {"synthesize": "synthesize", "complete": "complete"},
        )

    graph.add_edge("complete", END)

    return graph.compile(checkpointer=MemorySaver())


def compile_graph(brief_id: str, depth: str):
    return build_graph(depth)
