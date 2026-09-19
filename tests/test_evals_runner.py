"""Runner glue for the eval suite: run_experiment's worker loop + real Postgres/Redis, with the
LLM/search boundaries mocked (no API keys). Proves seeding, graph run, trace read-back, scoring,
and result files work end to end."""

import json
from unittest.mock import AsyncMock, patch

from evals import run_evals
from tests.test_graph_e2e import FAKE_SEARCH_RESULTS, _make_fake_llm_call, _verdict_json


async def test_run_evals_mocked(tmp_path, monkeypatch):
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path)
    fake_llm = _make_fake_llm_call(_verdict_json(0.9, disputed=False))
    with (
        patch("app.llm.groq_client.call", new=AsyncMock(side_effect=fake_llm)),
        patch("app.llm.gemini_client.call", new=AsyncMock(side_effect=fake_llm)),
        patch("app.tools.search_tool.web_search.func", return_value=FAKE_SEARCH_RESULTS),
        patch("app.tools.chroma_tool.search_knowledge_base.func", return_value=[]),
    ):
        summary = run_evals.run(limit=1, run_name="mocked")

    agg = summary["aggregate"]
    assert agg["n"] == 1
    assert agg["tool_call_accuracy_mean"] == 1.0  # web_search called with a non-empty query
    assert 0.0 <= agg["task_success_mean"] <= 1.0
    assert agg["cost_usd_total"] > 0 and agg["latency_ms_p50"] > 0
    assert json.loads((tmp_path / "mocked.json").read_text())["run_name"] == "mocked"
    assert "| g01 |" in (tmp_path / "latest.md").read_text()
