import streamlit as st

from api import client
from api.client import ApiError
from auth.session import render_auth_gate, render_sidebar

st.set_page_config(layout="wide", page_title="Nexus Research", page_icon="🔬")

render_auth_gate()
render_sidebar()

st.title("🔬 Nexus Research")
st.caption("Autonomous multi-agent research intelligence")

if not st.session_state.get("onboarded"):
    st.subheader("Welcome — let's get you set up")
    st.markdown("**What do you research most?**")
    domain = st.selectbox("Primary domain", ["finance", "tech", "regulatory", "general"], key="onboard_domain")

    st.markdown("**Run a quick test brief?**")
    topic = st.text_input("Topic", value="AI industry competitive landscape", key="onboard_topic")

    if st.button("Start test brief", type="primary"):
        try:
            result = client.create_brief(
                st.session_state.token,
                topic=topic,
                scope="Overview of key players, recent moves, and market direction",
                domain=domain,
                depth="quick",
                audience="analyst",
                consent_store=True,
                email="onboarding@nexus.local",
            )
            st.session_state.onboarded = True
            st.session_state.active_brief_id = result["brief_id"]
            st.switch_page("pages/2_live_research.py")
        except ApiError as e:
            st.error(f"Could not start brief: {e.detail}")

    if st.button("Skip onboarding"):
        st.session_state.onboarded = True
        st.rerun()

else:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.page_link("pages/1_new_brief.py", label="➕ New Brief", use_container_width=True)
    with col2:
        st.page_link("pages/4_library.py", label="📚 Library", use_container_width=True)
    with col3:
        st.page_link("pages/5_knowledge.py", label="🧠 Knowledge Base", use_container_width=True)

    st.divider()
    st.markdown(
        "Nexus deploys a team of 7 specialist AI agents — a supervisor plans, researchers gather claims in "
        "parallel, a fact-checker adversarially disputes weak claims, agents defend or retract in structured "
        "debate, and a writer produces the final report. Every step streams live."
    )
