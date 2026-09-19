"""Agent eval suite — scores every golden research question on four metrics:

  task_success        fraction of expected key facts present in the final brief
                      (token-coverage heuristic first; an LLM judge only tie-breaks the misses)
  tool_call_accuracy  expected tools called with a non-empty query, read from agent_traces.tool_calls
  cost_usd            prompt/completion tokens per agent_trace x provider list price
  latency_ms          wall-clock of the quick-depth graph run (p50/p95 reported over the set)

Runs the real pipeline (live Groq/Gemini/Tavily) at depth=quick against local Postgres/Redis.
With LANGFUSE_* keys set, the golden set is upserted as Langfuse dataset "nexus-golden" and the run
is recorded as a dataset run, so two prompt/model versions can be compared side by side in the UI.

  .venv/bin/python -m evals.run_evals --limit 5 --run-name baseline
  .venv/bin/python -m pytest -m evals evals/            # same via pytest; skipped without keys

Results: evals/results/<run_name>.json + evals/results/latest.md (paste the table into the README).
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from langfuse import Evaluation, Langfuse
from sqlalchemy import select

from app.agents.graph import compile_graph
from app.agents.state import initial_state
from app.config.settings import get_settings
from app.core import tracing
from app.core.auth import generate_api_key, hash_api_key
from app.db.session import AsyncSessionLocal
from app.llm.router import get_llm
from app.models.agent_trace import AgentTrace
from app.models.brief import ResearchBrief
from app.models.tenant import Tenant

settings = get_settings()

GOLDEN_PATH = Path(__file__).with_name("golden.jsonl")
RESULTS_DIR = Path(__file__).with_name("results")
DATASET_NAME = "nexus-golden"

# USD per 1M tokens (input, output). Public list prices, Sept 2026 snapshot — update when providers change.
PRICES_PER_M = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "gemini-1.5-flash": (0.075, 0.30),
}

JUDGE_SYSTEM_PROMPT = (
    "You grade research briefs. Answer YES if the brief states the given fact (paraphrase allowed, "
    "numbers and dates must agree), otherwise NO. Reply with exactly one word: YES or NO."
)

_STOPWORDS = frozenset("a an the of in on at to by for and or is are was were be been with from as that this it its into over under than".split())
_TOKEN = re.compile(r"[a-z0-9][a-z0-9.%-]*")


# ----------------------------------------------------------------------------- scoring (pure)


def content_tokens(text: str) -> set[str]:
    return {t.strip(".-") for t in _TOKEN.findall(text.lower())} - _STOPWORDS - {""}


def heuristic_match(fact: str, text: str, threshold: float = 0.8) -> bool:
    """True when >= `threshold` of the fact's content tokens appear anywhere in the brief."""
    want, have = content_tokens(fact), content_tokens(text)
    return bool(want) and len(want & have) / len(want) >= threshold


def report_text(report) -> str:
    """Flatten every string in the final report (title, summary, section bodies) into one blob."""
    if isinstance(report, str):
        return report
    if isinstance(report, dict):
        return "\n".join(report_text(v) for v in report.values())
    if isinstance(report, list):
        return "\n".join(report_text(v) for v in report)
    return ""


def cost_usd(traces: list[dict]) -> float:
    total = 0.0
    for t in traces:
        price_in, price_out = PRICES_PER_M.get(t.get("llm_model"), (0.0, 0.0))
        total += (t.get("prompt_tokens") or 0) * price_in / 1e6 + (t.get("completion_tokens") or 0) * price_out / 1e6
    return round(total, 6)


def tool_call_accuracy(expected_tools: list[str], traces: list[dict]) -> float:
    """Share of expected tools that were called with a non-empty query. A recorded call whose
    `query` is blank counts as not called (a tool without a query field, e.g. calculate, is fine)."""
    called = set()
    for t in traces:
        for call in t.get("tool_calls") or []:
            query = call.get("query")
            if query is None or str(query).strip():
                called.add(call.get("tool"))
    if not expected_tools:
        return 1.0
    return sum(tool in called for tool in expected_tools) / len(expected_tools)


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; p in [0, 100]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(p / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


# ----------------------------------------------------------------------------- live pieces


async def llm_judge(fact: str, text: str) -> bool:
    llm = get_llm("fact_checker")
    result = await llm.call(
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": f"Fact: {fact}\n\nBrief:\n{text[:12000]}"},
        ],
        model=llm.model,
        temperature=0.0,
        max_tokens=5,
    )
    return result["content"].strip().upper().startswith("YES")


async def task_success(expected_facts: list[str], text: str) -> tuple[float, dict[str, str]]:
    verdicts: dict[str, str] = {}
    for fact in expected_facts:
        if heuristic_match(fact, text):
            verdicts[fact] = "heuristic"
        else:
            verdicts[fact] = "judge" if await llm_judge(fact, text) else "miss"
    hits = sum(v != "miss" for v in verdicts.values())
    return (hits / len(expected_facts) if expected_facts else 1.0), verdicts


async def _seed_brief(topic: str, scope: str, domain: str) -> tuple[str, str]:
    async with AsyncSessionLocal() as db:
        tenant = Tenant(
            name="evals",
            email=f"evals-{uuid.uuid4().hex[:8]}@nexus.local",
            api_key_hash=hash_api_key(generate_api_key()),
            plan="pro",
        )
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)
        brief = ResearchBrief(
            tenant_id=tenant.id,
            topic=topic,
            scope=scope,
            domain=domain,
            depth="quick",
            audience="analyst",
            status="queued",
            consent_store=False,
            user_email="evals@nexus.local",
        )
        db.add(brief)
        await db.commit()
        await db.refresh(brief)
        return str(brief.id), str(tenant.id)


async def run_brief(golden_id: str, topic: str, scope: str, domain: str) -> dict:
    """Run the quick-depth graph for one golden question; return text + traces + cost + latency."""
    brief_id, tenant_id = await _seed_brief(topic, scope, domain)
    graph = compile_graph(brief_id, "quick")
    state = initial_state(brief_id, tenant_id, topic, scope, domain, "quick", "analyst")

    start = time.perf_counter()
    with (
        tracing.trace_attributes(trace_name="eval_run", session_id=brief_id, tags=["eval", golden_id], metadata={"golden_id": golden_id}),
        tracing.observation(f"eval:{golden_id}", as_type="agent", input={"topic": topic, "scope": scope}) as root,
    ):
        final = await graph.ainvoke(state, config={"configurable": {"thread_id": brief_id}})
        trace_id = root.trace_id
    latency_ms = int((time.perf_counter() - start) * 1000)

    async with AsyncSessionLocal() as db:
        rows = await db.scalars(select(AgentTrace).where(AgentTrace.brief_id == uuid.UUID(brief_id)))
        traces = [
            {
                "agent_type": r.agent_type,
                "llm_model": r.llm_model,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "latency_ms": r.latency_ms,
                "tool_calls": r.tool_calls,
            }
            for r in rows
        ]

    report = final.get("final_report") or final.get("draft_report") or {}
    return {
        "brief_id": brief_id,
        "trace_id": trace_id,
        "text": report_text(report),
        "traces": traces,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd(traces),
    }


# ----------------------------------------------------------------------------- langfuse experiment glue


def _field(item, name: str):
    return item[name] if isinstance(item, dict) else getattr(item, name)


async def _task(*, item, **_) -> dict:
    inp, meta = _field(item, "input"), _field(item, "metadata") or {}
    return await run_brief(meta["golden_id"], inp["topic"], inp["scope"], inp["domain"])


async def _eval_task_success(*, output, expected_output, **_) -> Evaluation:
    score, verdicts = await task_success(expected_output["facts"], output["text"])
    return Evaluation(name="task_success", value=score, comment=json.dumps(verdicts))


def _eval_tool_accuracy(*, output, expected_output, **_) -> Evaluation:
    return Evaluation(name="tool_call_accuracy", value=tool_call_accuracy(expected_output["tools"], output["traces"]))


def _eval_cost(*, output, **_) -> Evaluation:
    return Evaluation(name="cost_usd", value=output["cost_usd"])


def _eval_latency(*, output, **_) -> Evaluation:
    return Evaluation(name="latency_ms", value=output["latency_ms"])


EVALUATORS = [_eval_task_success, _eval_tool_accuracy, _eval_cost, _eval_latency]


def load_golden(limit: int | None = None) -> list[dict]:
    items = [json.loads(line) for line in GOLDEN_PATH.read_text().splitlines() if line.strip()]
    return items[:limit] if limit else items


def _local_items(golden: list[dict]) -> list[dict]:
    return [
        {
            "input": {"topic": g["topic"], "scope": g["scope"], "domain": g["domain"]},
            "expected_output": {"facts": g["expected_facts"], "tools": g["expected_tools"]},
            "metadata": {"golden_id": g["id"], "domain": g["domain"]},
        }
        for g in golden
    ]


def _dataset_items(lf: Langfuse, golden: list[dict]):
    """Upsert the golden set into Langfuse (items keyed by golden id) and return the dataset items."""
    try:
        lf.create_dataset(name=DATASET_NAME, description="Nexus golden research questions")
    except Exception:  # already exists
        pass
    for local, g in zip(_local_items(golden), golden):
        lf.create_dataset_item(dataset_name=DATASET_NAME, id=f"{DATASET_NAME}-{g['id']}", **local)
    wanted = {f"{DATASET_NAME}-{g['id']}" for g in golden}
    return [i for i in lf.get_dataset(DATASET_NAME).items if i.id in wanted]


def summarize(result) -> dict:
    rows = []
    for ir in result.item_results:
        scores = {e.name: e.value for e in ir.evaluations}
        rows.append(
            {
                "id": (_field(ir.item, "metadata") or {}).get("golden_id"),
                "topic": _field(ir.item, "input")["topic"],
                "brief_id": ir.output["brief_id"],
                "trace_url": tracing.trace_url(ir.trace_id),
                **scores,
                "fact_verdicts": next((e.comment for e in ir.evaluations if e.name == "task_success"), None),
            }
        )
    latencies = [r["latency_ms"] for r in rows if "latency_ms" in r]
    n = len(rows) or 1
    return {
        "run_name": result.run_name,
        "dataset_run_url": result.dataset_run_url,
        "items": rows,
        "aggregate": {
            "n": len(rows),
            "task_success_mean": round(sum(r.get("task_success", 0) for r in rows) / n, 3),
            "tool_call_accuracy_mean": round(sum(r.get("tool_call_accuracy", 0) for r in rows) / n, 3),
            "cost_usd_total": round(sum(r.get("cost_usd", 0) for r in rows), 4),
            "cost_usd_mean": round(sum(r.get("cost_usd", 0) for r in rows) / n, 5),
            "latency_ms_p50": percentile(latencies, 50),
            "latency_ms_p95": percentile(latencies, 95),
        },
    }


def to_markdown(summary: dict) -> str:
    agg = summary["aggregate"]
    lines = [
        f"### Eval run `{summary['run_name']}` — {agg['n']} golden questions, depth=quick",
        "",
        "| id | topic | task_success | tool_call_accuracy | cost_usd | latency_ms |",
        "|---|---|---|---|---|---|",
    ]
    for r in summary["items"]:
        lines.append(
            f"| {r['id']} | {r['topic']} | {r.get('task_success', 0):.2f} | {r.get('tool_call_accuracy', 0):.2f} "
            f"| {r.get('cost_usd', 0):.4f} | {r.get('latency_ms', 0)} |"
        )
    lines += [
        "",
        f"**Aggregate:** task_success {agg['task_success_mean']:.2f} · tool_call_accuracy {agg['tool_call_accuracy_mean']:.2f} · "
        f"cost ${agg['cost_usd_total']:.4f} total (${agg['cost_usd_mean']:.5f}/run) · "
        f"latency p50 {agg['latency_ms_p50']:.0f} ms / p95 {agg['latency_ms_p95']:.0f} ms",
    ]
    if summary["dataset_run_url"]:
        lines.append(f"\nLangfuse dataset run: {summary['dataset_run_url']}")
    return "\n".join(lines) + "\n"


def run(limit: int | None = None, run_name: str | None = None, concurrency: int = 2) -> dict:
    golden = load_golden(limit)
    run_name = run_name or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")
    lf = tracing.client() or Langfuse(public_key="disabled", secret_key="disabled", tracing_enabled=False)
    data = _dataset_items(lf, golden) if tracing.enabled() else _local_items(golden)

    result = lf.run_experiment(
        name=DATASET_NAME,
        run_name=run_name,
        data=data,
        task=_task,
        evaluators=EVALUATORS,
        max_concurrency=concurrency,
        metadata={"depth": "quick", "groq_large": settings.GROQ_MODEL_LARGE, "gemini": settings.GEMINI_MODEL},
    )
    tracing.flush()

    summary = summarize(result)
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"{run_name}.json").write_text(json.dumps(summary, indent=2))
    (RESULTS_DIR / "latest.md").write_text(to_markdown(summary))
    print(to_markdown(summary))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="only run the first N golden items")
    parser.add_argument("--run-name", default=None, help="Langfuse dataset run name (default: timestamp)")
    parser.add_argument("--concurrency", type=int, default=2, help="parallel briefs (mind provider rate limits)")
    args = parser.parse_args()
    run(limit=args.limit, run_name=args.run_name, concurrency=args.concurrency)


if __name__ == "__main__":
    main()
