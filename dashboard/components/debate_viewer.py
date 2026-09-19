import streamlit as st


def _matches_claim(trace: dict, claim_text: str) -> bool:
    snippet = claim_text[:40].lower()
    return snippet in (trace.get("input_summary") or "").lower() or snippet in (trace.get("output_summary") or "").lower()


def render(claim: dict, traces: list[dict]) -> None:
    claim_text = claim.get("claim_text", "")
    debate_traces = [
        t for t in traces if "_debate_" in t.get("agent_type", "") and _matches_claim(t, claim_text)
    ]
    if not debate_traces:
        return

    with st.expander("🥊 View debate log"):
        for t in sorted(debate_traces, key=lambda x: x.get("created_at", "")):
            if t["agent_type"].endswith("_debate_defense"):
                st.markdown(f"**Round 1 — Defense** ({t['agent_type'].replace('_debate_defense', '')})")
            else:
                st.markdown("**Round 2 — Fact-checker verdict**")
            st.markdown(t.get("output_summary", ""))
            st.caption(f"{t.get('llm_provider')} / {t.get('llm_model')} — {t.get('latency_ms')}ms")
            st.divider()
