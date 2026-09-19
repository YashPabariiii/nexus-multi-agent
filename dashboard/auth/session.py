import base64
import json
import time

import streamlit as st

from api import client
from api.client import ApiError


def _decode_jwt_payload(token: str) -> dict:
    """Reads claims for UI display only — the API is the source of truth for verification."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


def init_session_state() -> None:
    defaults = {
        "token": None,
        "tenant_id": None,
        "plan": None,
        "token_exp": None,
        "onboarded": False,
        "active_brief_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def is_authenticated() -> bool:
    if not st.session_state.get("token"):
        return False
    exp = st.session_state.get("token_exp")
    if exp and time.time() > exp:
        logout()
        return False
    return True


def logout() -> None:
    for key in ("token", "tenant_id", "plan", "token_exp", "active_brief_id"):
        st.session_state[key] = None


def _set_session_from_token(token: str) -> None:
    claims = _decode_jwt_payload(token)
    st.session_state.token = token
    st.session_state.tenant_id = claims.get("tenant_id")
    st.session_state.plan = claims.get("plan", "free")
    st.session_state.token_exp = claims.get("exp")


def render_auth_gate() -> None:
    """Renders Login/Register tabs. Call at the top of every page; returns only once authenticated."""
    init_session_state()
    if is_authenticated():
        return

    st.title("🔬 Nexus — Autonomous Research Intelligence")
    tab_login, tab_register = st.tabs(["Login", "Register"])

    with tab_register:
        with st.form("register_form"):
            name = st.text_input("Organization / name")
            email = st.text_input("Email")
            submitted = st.form_submit_button("Register")
        if submitted:
            try:
                result = client.register(name, email)
                st.success("Registered! Save your API key — it's shown once.")
                st.code(result["api_key"], language=None)
                st.session_state["_just_registered_key"] = result["api_key"]
            except ApiError as e:
                st.error(f"Registration failed: {e.detail}")

        if st.session_state.get("_just_registered_key"):
            st.info("Copy the key above, then log in with it on the Login tab.")

    with tab_login:
        with st.form("login_form"):
            api_key = st.text_input("API key", type="password")
            submitted = st.form_submit_button("Login")
        if submitted:
            try:
                result = client.login(api_key)
                _set_session_from_token(result["access_token"])
                st.rerun()
            except ApiError as e:
                st.error(f"Login failed: {e.detail}")

    st.stop()


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## 🔬 NEXUS")
        plan = st.session_state.get("plan", "free")
        badge = "🟢 PRO" if plan == "pro" else "⚪ FREE"
        st.markdown(f"**Plan:** {badge}")
        st.caption(f"Tenant: `{(st.session_state.get('tenant_id') or '')[:8]}...`")
        st.divider()
        if st.button("Logout", use_container_width=True):
            logout()
            st.rerun()
