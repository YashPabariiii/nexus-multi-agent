import streamlit as st


def require_pro(feature_name: str = "this feature") -> bool:
    """Returns True if the tenant is Pro. Renders an upgrade banner and returns False otherwise."""
    if st.session_state.get("plan") == "pro":
        return True

    st.warning(f"🔒 **{feature_name}** is a Pro feature. Upgrade to unlock deep research, the knowledge base, and recurring briefs.")
    st.button("Upgrade to Pro", disabled=True, help="Billing not wired up in this scaffold")
    return False


def block_if_free(depth: str) -> bool:
    """For the depth selector: deep research needs Pro. Returns True if the selection is allowed."""
    if depth == "deep" and st.session_state.get("plan") != "pro":
        st.error("🔒 Deep research requires a Pro plan. Choose Quick or Standard, or upgrade.")
        return False
    return True
