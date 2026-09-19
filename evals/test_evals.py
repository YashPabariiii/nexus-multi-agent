"""pytest entry for the live eval suite: `.venv/bin/python -m pytest -m evals evals/`.
Skipped (not failed) when provider keys are absent, so CI never burns credits."""

import os

import pytest

from app.config.settings import get_settings
from evals.run_evals import run

settings = get_settings()
_HAS_KEYS = all([settings.GROQ_API_KEY, settings.GEMINI_API_KEY, settings.TAVILY_API_KEY])


@pytest.mark.evals
@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ/GEMINI/TAVILY keys required for live evals")
def test_golden_set():
    summary = run(limit=int(os.environ.get("EVAL_LIMIT", "0")) or None, run_name=os.environ.get("EVAL_RUN_NAME"))
    agg = summary["aggregate"]
    assert agg["task_success_mean"] >= float(os.environ.get("EVAL_MIN_SUCCESS", "0.5")), agg
    assert agg["tool_call_accuracy_mean"] >= float(os.environ.get("EVAL_MIN_TOOL_ACC", "0.9")), agg
