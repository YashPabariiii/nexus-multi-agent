import os

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8099")
WS_BASE_URL = API_BASE_URL.replace("https://", "wss://").replace("http://", "ws://")

DOMAINS = ["finance", "tech", "regulatory", "general"]
AUDIENCES = ["executive", "analyst", "technical"]
DEPTHS = ["quick", "standard", "deep"]

DEPTH_INFO = {
    "quick": {"emoji": "⚡", "label": "Quick", "detail": "3 agents, ~5 min, headline only"},
    "standard": {"emoji": "📊", "label": "Standard", "detail": "5 agents, ~15 min, full report"},
    "deep": {"emoji": "🔬", "label": "Deep", "detail": "7 agents + debates, ~45 min (Pro only)"},
}

AGENT_ICONS = {
    "supervisor": "🔵",
    "web_search": "🟡",
    "domain_knowledge": "🟡",
    "data_analyst": "🟡",
    "fact_checker": "🔴",
    "synthesis": "🟢",
    "writer": "🟢",
}

STAGE_ORDER = ["queued", "planning", "researching", "fact_checking", "synthesizing", "writing", "awaiting_review", "complete"]
