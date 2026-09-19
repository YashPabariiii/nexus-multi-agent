import streamlit as st

from api import client
from api.client import ApiError
from auth.session import render_auth_gate, render_sidebar
from components import tier_gate

st.set_page_config(layout="wide", page_title="Knowledge Base — Nexus", page_icon="🔬")
render_auth_gate()
render_sidebar()

st.title("🧠 Knowledge Base")

if not tier_gate.require_pro("The knowledge base"):
    st.stop()

try:
    summary = client.knowledge_summary(st.session_state.token)
except ApiError as e:
    st.error(f"Could not load knowledge base: {e.detail}")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Briefs indexed", summary["total_briefs_indexed"])
col2.metric("Topics covered", len(summary["topics"]))
col3.metric("Domains", len(summary["domains_covered"]))
col4.metric("Last updated", (summary["last_indexed_at"] or "never")[:10] if summary["last_indexed_at"] else "never")

st.divider()
st.subheader("Topics")
if summary["topics"]:
    # ponytail: the API only exposes distinct topics (no per-topic frequency), so this renders
    # as tags rather than a fabricated word-cloud weighting. Add a /knowledge/topic-counts
    # endpoint if true frequency weighting is needed.
    st.markdown(
        " ".join(
            f'<span style="background:#eef2ff;color:#3730a3;padding:4px 10px;border-radius:14px;'
            f'margin:2px;display:inline-block;font-size:0.85rem;">{t}</span>'
            for t in summary["topics"]
        ),
        unsafe_allow_html=True,
    )
else:
    st.info("No knowledge indexed yet — completed briefs with consent_store enabled populate this.")

st.divider()
st.subheader("Related Briefs Explorer")
search_topic = st.text_input("Search topic")
if search_topic:
    try:
        related = client.knowledge_related(st.session_state.token, search_topic)
    except ApiError as e:
        related = []
        st.error(f"Search failed: {e.detail}")

    for r in related:
        with st.container(border=True):
            st.markdown(f"**{r.get('topic')}** — similarity {r.get('similarity', 0):.0%}")
            st.caption(r.get("date", ""))
            st.caption(r.get("key_findings_preview", ""))
            if st.button("Open report", key=f"open_{r.get('brief_id')}"):
                st.session_state.active_brief_id = r["brief_id"]
                st.switch_page("pages/3_review.py")

st.divider()
st.subheader("Compare Two Briefs")
try:
    briefs = client.list_briefs(st.session_state.token, page=1, page_size=50)["items"]
except ApiError:
    briefs = []

options = {f"{b['topic']} ({b['created_at'][:10]})": b["id"] for b in briefs}
compare_col1, compare_col2 = st.columns(2)
with compare_col1:
    label_a = st.selectbox("Brief A", list(options.keys()), key="cmp_a") if options else None
with compare_col2:
    label_b = st.selectbox("Brief B", list(options.keys()), key="cmp_b") if options else None

if label_a and label_b and st.button("Compare"):
    try:
        result = client.compare_briefs(st.session_state.token, options[label_a], options[label_b])
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**Common findings**")
            for f in result["common_findings"]:
                st.markdown(f"- {f}")
        with col_b:
            st.markdown("**New developments**")
            for f in result["new_developments"]:
                st.markdown(f"- {f}")
        with col_c:
            st.markdown("**Contradictions**")
            for f in result["contradictions"]:
                st.markdown(f"- {f}")
        st.metric("Confidence delta", f"{result['confidence_delta']:+.2f}")
    except ApiError as e:
        st.error(f"Comparison failed: {e.detail}")

st.divider()
st.subheader("Recurring Brief")
st.caption("Track this topic over time — auto-run on a schedule, email on complete.")
recurring_col1, recurring_col2 = st.columns(2)
with recurring_col1:
    st.text_input("Topic to track", key="recurring_topic")
with recurring_col2:
    st.selectbox("Schedule", ["weekly", "monthly"], key="recurring_schedule")
st.toggle("Enable recurring brief", disabled=True, help="Scheduling isn't wired to a backend yet — no cron/beat service exists in this build.")
