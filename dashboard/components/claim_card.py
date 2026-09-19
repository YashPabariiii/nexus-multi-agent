import streamlit as st

from components import confidence_badge


def render(claim: dict, disputed: bool = False) -> None:
    border_color = "#cf222e" if disputed else "#d0d7de"
    conf = claim.get("final_confidence") if claim.get("final_confidence") is not None else claim.get("confidence", 0.0)

    with st.container():
        st.markdown(
            f'<div style="border-left:4px solid {border_color};padding:10px 14px;margin-bottom:8px;'
            f'background:rgba(0,0,0,0.02);border-radius:4px;">'
            f'<div style="font-weight:500;">{claim.get("claim_text", "")}</div>'
            f'<div style="margin-top:6px;font-size:0.85rem;">'
            f'{confidence_badge.inline(conf)} &nbsp;·&nbsp; '
            f'<a href="{claim.get("source_url", "#")}" target="_blank">source</a> &nbsp;·&nbsp; '
            f'agent: {claim.get("agent_id", "unknown")}'
            f"</div>"
            + (f'<div style="margin-top:6px;color:#cf222e;font-size:0.82rem;">Original: {claim.get("confidence", 0):.0%} → '
               f'Final: {conf:.0%} — {claim.get("dispute_reason", "")}</div>' if disputed else "")
            + "</div>",
            unsafe_allow_html=True,
        )
