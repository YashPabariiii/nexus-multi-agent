import streamlit as st

_COLORS = {"HIGH": "#1a7f37", "MEDIUM": "#9a6700", "LOW": "#cf222e", "UNVERIFIED": "#57606a"}


def confidence_label(score: float) -> str:
    if score >= 0.8:
        return "HIGH"
    if score >= 0.6:
        return "MEDIUM"
    if score >= 0.4:
        return "LOW"
    return "UNVERIFIED"


def render(score: float, size: str = "1rem") -> None:
    label = confidence_label(score)
    color = _COLORS[label]
    st.markdown(
        f'<span style="background:{color}20;color:{color};padding:2px 10px;border-radius:12px;'
        f'font-weight:600;font-size:{size};">{label} · {score:.0%}</span>',
        unsafe_allow_html=True,
    )


def inline(score: float) -> str:
    label = confidence_label(score)
    color = _COLORS[label]
    return f'<span style="color:{color};font-weight:600;">{label} ({score:.0%})</span>'
