import streamlit as st

from api import client
from api.client import ApiError
from auth.session import render_auth_gate, render_sidebar
from components import claim_card, confidence_badge, debate_viewer

st.set_page_config(layout="wide", page_title="Review — Nexus", page_icon="🔬")
render_auth_gate()
render_sidebar()

brief_id = st.session_state.get("active_brief_id")
if not brief_id:
    st.warning("No active brief.")
    st.page_link("pages/4_library.py", label="📚 Go to Library")
    st.stop()

try:
    brief = client.get_brief(st.session_state.token, brief_id)
    claims = client.get_claims(st.session_state.token, brief_id)
    traces = client.get_traces(st.session_state.token, brief_id)
except ApiError as e:
    st.error(f"Could not load brief: {e.detail}")
    st.stop()

try:
    preview = client.get_report_preview(st.session_state.token, brief_id)
except ApiError:
    preview = None

st.title(f"📋 Review — {brief['topic']}")

left, right = st.columns([0.4, 0.6])

with left:
    st.subheader("Report Preview")
    if preview:
        confidence_badge.render(preview["overall_confidence"], size="1.1rem")
        st.markdown(f"### {preview['title']}")
        st.markdown(preview["executive_summary"])

        st.markdown("**Sections**")
        for s in preview["sections"]:
            with st.expander(f"{s['title']}"):
                confidence_badge.render(s.get("confidence") or 0.0)
    else:
        st.info("Report not generated yet — this brief may still be running.")

    st.divider()
    st.markdown("**Downloads**")
    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        try:
            pdf_bytes = client.download_report_pdf(st.session_state.token, brief_id)
            st.download_button("📄 Download PDF", pdf_bytes, file_name=f"{brief_id}-report.pdf", mime="application/pdf")
        except ApiError:
            st.button("📄 Download PDF", disabled=True, help="Not generated yet")
    with dl_col2:
        try:
            trace_bytes = client.download_trace_pdf(st.session_state.token, brief_id)
            st.download_button("🔍 Download Agent Trace", trace_bytes, file_name=f"{brief_id}-trace.pdf", mime="application/pdf")
        except ApiError:
            st.button("🔍 Download Agent Trace", disabled=True, help="Not generated yet")

    with st.form("email_report_form"):
        email = st.text_input("Email report to")
        if st.form_submit_button("✉️ Email Report") and email:
            try:
                client.email_report(st.session_state.token, brief_id, email)
                st.success(f"Queued — sending to {email}")
            except ApiError as e:
                st.error(f"Could not send: {e.detail}")

with right:
    st.subheader("Claims Review")
    verified_claims = [c for c in claims if c.get("verified") and not c.get("disputed_by_agent")]
    disputed_claims = [c for c in claims if c.get("disputed_by_agent")]

    tab_verified, tab_disputed, tab_all = st.tabs(
        [f"Verified ({len(verified_claims)})", f"Disputed ({len(disputed_claims)})", f"All ({len(claims)})"]
    )
    with tab_verified:
        for c in verified_claims:
            claim_card.render(c)
    with tab_disputed:
        for c in disputed_claims:
            claim_card.render(c, disputed=True)
            debate_viewer.render(c, traces)
    with tab_all:
        for c in claims:
            claim_card.render(c, disputed=bool(c.get("disputed_by_agent")))

st.divider()
st.subheader("Actions")

if brief["status"] != "awaiting_review":
    st.info(f"This brief is not currently awaiting review (status: {brief['status']}).")
else:
    action_col1, action_col2, action_col3 = st.columns(3)

    with action_col1:
        if st.button("✅ Approve & Generate Report", type="primary", use_container_width=True):
            try:
                client.resume_brief(st.session_state.token, brief_id, action="approve")
                st.success("Approved — generating final report and PDF...")
                st.switch_page("pages/2_live_research.py")
            except ApiError as e:
                st.error(f"Could not approve: {e.detail}")

    with action_col2:
        with st.popover("🔄 Revise Sections", use_container_width=True):
            section_titles = [s["title"] for s in (preview["sections"] if preview else [])]
            sections_to_revise = st.multiselect("Sections to re-research", section_titles)
            feedback = st.text_area("Feedback")
            if st.button("Submit revision"):
                try:
                    client.resume_brief(st.session_state.token, brief_id, action="revise", feedback=feedback, sections_to_revise=sections_to_revise)
                    st.success("Revision requested — resuming research...")
                    st.switch_page("pages/2_live_research.py")
                except ApiError as e:
                    st.error(f"Could not submit revision: {e.detail}")

    with action_col3:
        with st.popover("📝 Expand Topic", use_container_width=True):
            expand_text = st.text_input("What should we add?")
            if st.button("Submit expansion"):
                try:
                    client.resume_brief(st.session_state.token, brief_id, action="expand", feedback=expand_text)
                    st.success("Expansion requested — resuming research...")
                    st.switch_page("pages/2_live_research.py")
                except ApiError as e:
                    st.error(f"Could not submit expansion: {e.detail}")
