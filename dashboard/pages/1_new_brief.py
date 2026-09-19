import streamlit as st

from api import client
from api.client import ApiError
from auth.session import render_auth_gate, render_sidebar
from components import tier_gate
from config import AGENT_ICONS, AUDIENCES, DEPTH_INFO, DOMAINS

st.set_page_config(layout="wide", page_title="New Brief — Nexus", page_icon="🔬")
render_auth_gate()
render_sidebar()

st.title("➕ New Research Brief")

if "new_brief_step" not in st.session_state:
    st.session_state.new_brief_step = 1
if "new_brief_data" not in st.session_state:
    st.session_state.new_brief_data = {}

step = st.session_state.new_brief_step
st.progress(step / 3, text=f"Step {step} of 3")

data = st.session_state.new_brief_data

# --- Step 1: Brief setup ---------------------------------------------------
if step == 1:
    st.subheader("Step 1 — Brief Setup")
    topic = st.text_area("Topic", value=data.get("topic", ""), max_chars=500, help="What do you want researched?")
    scope = st.text_area("Scope", value=data.get("scope", ""), help="What specifically to focus on")
    domain = st.selectbox("Domain", DOMAINS, index=DOMAINS.index(data.get("domain", "general")))
    audience = st.selectbox("Audience", AUDIENCES, index=AUDIENCES.index(data.get("audience", "analyst")))

    if st.button("Next →", type="primary", disabled=not (topic and scope)):
        data.update(topic=topic, scope=scope, domain=domain, audience=audience)
        st.session_state.new_brief_step = 2
        st.rerun()

# --- Step 2: Depth & options -------------------------------------------------
elif step == 2:
    st.subheader("Step 2 — Depth & Options")

    depth_cols = st.columns(3)
    for col, (key, info) in zip(depth_cols, DEPTH_INFO.items()):
        with col:
            selected = data.get("depth") == key
            if st.button(f"{info['emoji']} {info['label']}\n\n{info['detail']}", key=f"depth_{key}", use_container_width=True):
                data["depth"] = key
    st.caption(f"Selected: **{data.get('depth', 'none')}**")

    if data.get("depth") and not tier_gate.block_if_free(data["depth"]):
        data["depth"] = None

    if data.get("depth"):
        with st.spinner("Estimating complexity..."):
            try:
                preview = client.preview_brief(st.session_state.token, data["topic"], data["scope"], data["domain"])
                with st.container(border=True):
                    st.markdown(f"**Complexity:** {preview['complexity'].upper()}")
                    st.markdown(f"**Estimated time:** {preview['estimated_minutes']} min")
                    st.markdown("**Key questions:**")
                    for q in preview["key_research_questions"]:
                        st.markdown(f"- {q}")
                    if preview["related_briefs_found"]:
                        st.info(f"📚 {preview['related_briefs_found']} similar past brief(s) found")
            except ApiError as e:
                st.warning(f"Preview unavailable: {e.detail}")

    email = st.text_input("Notification email", value=data.get("email", ""))
    consent = st.checkbox("Store this research in my knowledge base for future briefs", value=data.get("consent_store", True))

    col_back, col_next = st.columns(2)
    with col_back:
        if st.button("← Back", use_container_width=True):
            st.session_state.new_brief_step = 1
            st.rerun()
    with col_next:
        if st.button("Next →", type="primary", use_container_width=True, disabled=not (data.get("depth") and email)):
            data.update(email=email, consent_store=consent)
            st.session_state.new_brief_step = 3
            st.rerun()

# --- Step 3: Confirm & launch -------------------------------------------------
elif step == 3:
    st.subheader("Step 3 — Confirm & Launch")

    with st.container(border=True):
        st.markdown(f"**Topic:** {data['topic']}")
        st.markdown(f"**Scope:** {data['scope']}")
        st.markdown(f"**Domain:** {data['domain']} · **Audience:** {data['audience']} · **Depth:** {data['depth']}")
        st.markdown(f"**Notify:** {data['email']} · **Save to knowledge base:** {'Yes' if data['consent_store'] else 'No'}")

    agents = ["supervisor", "web_search", "domain_knowledge"]
    if data["depth"] != "quick":
        agents.append("data_analyst")
    agents += ["fact_checker", "synthesis", "writer"]

    st.markdown("**Agent team:**")
    st.markdown(" &nbsp; ".join(f"{AGENT_ICONS.get(a, '⚙️')} {a}" for a in agents), unsafe_allow_html=True)

    col_back, col_launch = st.columns(2)
    with col_back:
        if st.button("← Back", use_container_width=True):
            st.session_state.new_brief_step = 2
            st.rerun()
    with col_launch:
        if st.button("🚀 Launch Research", type="primary", use_container_width=True):
            try:
                result = client.create_brief(
                    st.session_state.token,
                    topic=data["topic"],
                    scope=data["scope"],
                    domain=data["domain"],
                    depth=data["depth"],
                    audience=data["audience"],
                    consent_store=data["consent_store"],
                    email=data["email"],
                )
                st.session_state.active_brief_id = result["brief_id"]
                st.session_state.new_brief_step = 1
                st.session_state.new_brief_data = {}
                st.switch_page("pages/2_live_research.py")
            except ApiError as e:
                st.error(f"Could not launch brief: {e.detail}")
