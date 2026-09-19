import time

import streamlit as st

from api import client
from api.client import ApiError
from auth.session import render_auth_gate, render_sidebar
from components import agent_trace_feed
from config import AGENT_ICONS, STAGE_ORDER

st.set_page_config(layout="wide", page_title="Live Research — Nexus", page_icon="🔬")
render_auth_gate()
render_sidebar()

brief_id = st.session_state.get("active_brief_id")
if not brief_id:
    st.warning("No active brief. Start one from New Brief.")
    st.page_link("pages/1_new_brief.py", label="➕ New Brief")
    st.stop()

try:
    brief = client.get_brief(st.session_state.token, brief_id)
except ApiError as e:
    st.error(f"Could not load brief: {e.detail}")
    st.stop()

st.title(f"🔴 LIVE — {brief['topic']}")
st.markdown(
    f"**Domain:** `{brief['domain']}` &nbsp; **Depth:** `{brief['depth']}` &nbsp; **Audience:** `{brief['audience']}`"
)

stage = brief["status"]
try:
    stage_index = STAGE_ORDER.index(stage)
except ValueError:
    stage_index = 0
st.progress(min(stage_index / (len(STAGE_ORDER) - 1), 1.0), text=f"Stage: {stage.replace('_', ' ').title()}")

if stage == "awaiting_review":
    st.success("⏸️ Draft complete — awaiting your review.")
    if st.button("Go to Review →", type="primary"):
        st.switch_page("pages/3_review.py")
elif stage == "complete":
    st.success("✅ Research complete.")
    if st.button("View Report →", type="primary"):
        st.switch_page("pages/3_review.py")
elif stage == "failed":
    st.error(f"❌ Brief failed: {brief.get('error_message', 'unknown error')}")

agent_trace_feed.ensure_connection(st.session_state.token, brief_id)
events = agent_trace_feed.poll_events(brief_id)

main_col, side_col = st.columns([0.7, 0.3])

with main_col:
    st.subheader("Live Agent Trace")
    feed_box = st.container(height=520)
    with feed_box:
        agent_trace_feed.render(events)

with side_col:
    st.subheader("Live Stats")
    try:
        claims = client.get_claims(st.session_state.token, brief_id)
        traces = client.get_traces(st.session_state.token, brief_id)
    except ApiError:
        claims, traces = [], []

    verified = sum(1 for c in claims if c.get("verified"))
    disputed = sum(1 for c in claims if c.get("disputed_by_agent"))
    debate_rounds = sum(1 for t in traces if t["agent_type"].endswith("_debate_verdict"))
    sources = len({c["source_url"] for c in claims if c.get("source_url")})

    st.metric("Claims found", len(claims))
    st.metric("Claims verified", verified)
    st.metric("Claims disputed", disputed)
    st.metric("Debate rounds", debate_rounds)
    st.metric("Sources used", sources)

    st.divider()
    st.markdown("**Agent status**")
    active_agents = {t["agent_type"] for t in traces}
    for agent, icon in AGENT_ICONS.items():
        dot = "🟢" if agent in active_agents else "⚪"
        st.markdown(f"{dot} {icon} {agent.replace('_', ' ').title()}")

    st.divider()
    groq_tokens = sum((t.get("prompt_tokens") or 0) + (t.get("completion_tokens") or 0) for t in traces if t["llm_provider"] == "groq")
    gemini_tokens = sum((t.get("prompt_tokens") or 0) + (t.get("completion_tokens") or 0) for t in traces if t["llm_provider"] == "gemini")
    st.markdown("**Token usage**")
    st.text(f"Groq: {groq_tokens:,}")
    st.text(f"Gemini: {gemini_tokens:,}")

if stage not in ("complete", "failed", "awaiting_review"):
    time.sleep(1.5)
    st.rerun()
