"""Pure scoring logic of the eval suite — no LLM, no DB."""

from evals.run_evals import (
    cost_usd,
    heuristic_match,
    load_golden,
    percentile,
    report_text,
    tool_call_accuracy,
)


def test_heuristic_match_is_order_insensitive_and_needs_80_percent_coverage():
    brief = "Satoshi Nakamoto published the Bitcoin whitepaper in 2008; supply is capped at 21 million."
    assert heuristic_match("whitepaper published in 2008", brief)
    assert heuristic_match("supply capped at 21 million bitcoin", brief)
    assert not heuristic_match("genesis block mined January 2009", brief)


def test_report_text_flattens_nested_report():
    report = {"title": "T", "sections": [{"title": "S1", "content": "body one"}], "overall_confidence": 0.8}
    text = report_text(report)
    assert "T" in text and "S1" in text and "body one" in text


def test_cost_uses_per_model_prices():
    traces = [
        {"llm_model": "llama-3.1-8b-instant", "prompt_tokens": 1_000_000, "completion_tokens": 0},
        {"llm_model": "gemini-1.5-flash", "prompt_tokens": 0, "completion_tokens": 1_000_000},
        {"llm_model": "unknown-model", "prompt_tokens": 5, "completion_tokens": 5},
    ]
    assert cost_usd(traces) == 0.35  # 0.05 + 0.30, unknown model priced at 0


def test_tool_call_accuracy_requires_non_empty_query():
    good = [{"tool_calls": [{"tool": "web_search", "query": "bitcoin origin", "result_count": 5}]}]
    blank = [{"tool_calls": [{"tool": "web_search", "query": "  ", "result_count": 0}]}]
    no_query_field = [{"tool_calls": [{"tool": "calculate", "computed_count": 1}]}]
    assert tool_call_accuracy(["web_search"], good) == 1.0
    assert tool_call_accuracy(["web_search"], blank) == 0.0
    assert tool_call_accuracy(["web_search", "calculate"], good + no_query_field) == 1.0
    assert tool_call_accuracy([], []) == 1.0


def test_percentile_nearest_rank():
    assert percentile([10, 20, 30, 40], 50) == 20
    assert percentile([10, 20, 30, 40], 95) == 40
    assert percentile([], 50) == 0.0


def test_golden_set_shape():
    items = load_golden()
    assert len(items) == 20
    for g in items:
        assert 3 <= len(g["expected_facts"]) <= 5, g["id"]
        assert g["expected_tools"] and g["domain"] in {"general", "tech", "finance", "regulatory"}
