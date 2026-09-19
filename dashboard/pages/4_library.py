import streamlit as st

from api import client
from api.client import ApiError
from auth.session import render_auth_gate, render_sidebar
from components import confidence_badge
from config import DEPTHS, DOMAINS

st.set_page_config(layout="wide", page_title="Library — Nexus", page_icon="🔬")
render_auth_gate()
render_sidebar()

st.title("📚 Brief Library")

filter_col1, filter_col2, filter_col3 = st.columns(3)
with filter_col1:
    domain_filter = st.multiselect("Domain", DOMAINS)
with filter_col2:
    depth_filter = st.multiselect("Depth", DEPTHS)
with filter_col3:
    min_confidence = st.slider("Min confidence", 0.0, 1.0, 0.0)

try:
    result = client.list_briefs(st.session_state.token, page=1, page_size=50)
    briefs = result["items"]
except ApiError as e:
    st.error(f"Could not load briefs: {e.detail}")
    st.stop()

if domain_filter:
    briefs = [b for b in briefs if b["domain"] in domain_filter]
if depth_filter:
    briefs = [b for b in briefs if b["depth"] in depth_filter]

if not briefs:
    st.info("No briefs yet.")
    st.page_link("pages/1_new_brief.py", label="➕ Create your first brief")
    st.stop()

for brief in briefs:
    with st.container(border=True):
        col_main, col_actions = st.columns([0.75, 0.25])

        with col_main:
            st.markdown(f"### {brief['topic']}")
            st.markdown(
                f"`{brief['domain']}` &nbsp; `{brief['depth']}` &nbsp; **Status:** {brief['status']}"
            )
            if brief.get("supervisor_plan"):
                justification = brief["supervisor_plan"].get("depth_justification", "")
                if justification:
                    st.caption(justification[:160])
            st.caption(f"Created: {brief['created_at'][:10]}")

        with col_actions:
            if st.button("View Report", key=f"view_{brief['id']}", use_container_width=True):
                st.session_state.active_brief_id = brief["id"]
                st.switch_page("pages/3_review.py")

            try:
                pdf_bytes = client.download_report_pdf(st.session_state.token, brief["id"])
                st.download_button(
                    "Download PDF", pdf_bytes, file_name=f"{brief['id']}-report.pdf", mime="application/pdf",
                    key=f"dl_{brief['id']}", use_container_width=True,
                )
            except ApiError:
                st.button("Download PDF", disabled=True, key=f"dl_disabled_{brief['id']}", use_container_width=True)

            if st.button("Re-run", key=f"rerun_{brief['id']}", use_container_width=True):
                st.session_state.new_brief_data = {
                    "topic": brief["topic"],
                    "scope": f"Focus on what has changed since {brief['created_at'][:10]}",
                    "domain": brief["domain"],
                    "audience": brief["audience"],
                }
                st.session_state.new_brief_step = 2
                st.switch_page("pages/1_new_brief.py")
