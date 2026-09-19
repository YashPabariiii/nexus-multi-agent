import streamlit as st
import websocket

from config import AGENT_ICONS, WS_BASE_URL

EVENT_COLORS = {
    "agent_update": "#2563eb",
    "claim_disputed": "#dc2626",
    "debate_resolved": "#f97316",
    "human_review": "#7c3aed",
    "human_review_resumed": "#7c3aed",
    "brief_complete": "#16a34a",
    "brief_failed": "#dc2626",
    "report_ready": "#16a34a",
    "heartbeat": "#9ca3af",
}


def _ws_key(brief_id: str) -> str:
    return f"_ws_conn_{brief_id}"


def _events_key(brief_id: str) -> str:
    return f"_ws_events_{brief_id}"


def ensure_connection(token: str, brief_id: str) -> None:
    key = _ws_key(brief_id)
    if key in st.session_state and st.session_state[key] is not None:
        return

    ws = websocket.WebSocket()
    ws.settimeout(0.3)
    try:
        ws.connect(f"{WS_BASE_URL}/v1/briefs/{brief_id}/stream?token={token}")
    except Exception as exc:
        st.session_state[key] = None
        st.error(f"Could not connect to live trace stream: {exc}")
        return

    st.session_state[key] = ws
    st.session_state.setdefault(_events_key(brief_id), [])


def poll_events(brief_id: str) -> list[dict]:
    """Non-blocking drain of whatever the socket has buffered since the last rerun."""
    import json

    ws = st.session_state.get(_ws_key(brief_id))
    events = st.session_state.setdefault(_events_key(brief_id), [])
    if ws is None:
        return events

    while True:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            break
        except Exception:
            st.session_state[_ws_key(brief_id)] = None
            break
        if not raw:
            break
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if event.get("event_type") != "heartbeat":
            events.append(event)
        if event.get("event_type") in ("brief_complete", "brief_failed"):
            st.session_state[_ws_key(brief_id)] = None

    return events


def is_terminal(events: list[dict]) -> bool:
    return any(e.get("event_type") in ("brief_complete", "brief_failed") for e in events)


def render(events: list[dict]) -> None:
    for event in events[-200:]:
        event_type = event.get("event_type", "")
        agent_type = event.get("agent_type", "")
        icon = AGENT_ICONS.get(agent_type, "⚙️")
        if event_type == "claim_disputed":
            icon = "🔴"
        elif event_type == "debate_resolved":
            icon = "🥊"
        elif event_type in ("human_review", "human_review_resumed"):
            icon = "⏸️"
        elif event_type in ("brief_complete", "report_ready"):
            icon = "✅"
        elif event_type == "brief_failed":
            icon = "❌"

        color = EVENT_COLORS.get(event_type, "#6b7280")
        confidence = event.get("confidence")
        conf_str = f" · confidence: {confidence:.0%}" if isinstance(confidence, (int, float)) else ""

        st.markdown(
            f'<div style="border-left:3px solid {color};padding:6px 12px;margin-bottom:4px;font-size:0.9rem;">'
            f"{icon} <b>{agent_type or event_type}</b>: {event.get('message', '')}{conf_str}"
            f"</div>",
            unsafe_allow_html=True,
        )
