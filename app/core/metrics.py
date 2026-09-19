from prometheus_client import Counter, Gauge, Histogram
from slowapi import Limiter

from app.core.auth import decode_token


def rate_limit_key(request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            tenant = decode_token(auth_header.removeprefix("Bearer "))
            return str(tenant.tenant_id)
        except Exception:
            pass
    return request.client.host if request.client else "anonymous"


def plan_rate_limit(request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            tenant = decode_token(auth_header.removeprefix("Bearer "))
            return "200/minute" if tenant.plan == "pro" else "20/minute"
        except Exception:
            pass
    return "20/minute"


limiter = Limiter(key_func=rate_limit_key)

# --- Brief lifecycle ---------------------------------------------------
briefs_submitted_total = Counter("briefs_submitted_total", "Briefs submitted", ["domain", "depth", "plan"])
briefs_completed_total = Counter("briefs_completed_total", "Briefs completed", ["domain", "depth"])
briefs_failed_total = Counter("briefs_failed_total", "Briefs failed", ["reason"])
brief_duration_seconds = Histogram(
    "brief_duration_seconds", "Brief wall-clock duration", ["depth"], buckets=[60, 180, 300, 600, 900, 1800, 2700]
)

# --- Agent execution -----------------------------------------------------
agent_calls_total = Counter("agent_calls_total", "Agent invocations", ["agent_type", "provider", "status"])
agent_latency_seconds = Histogram("agent_latency_seconds", "Agent call latency", ["agent_type", "provider"])
agent_tokens_total = Counter("agent_tokens_total", "Tokens consumed", ["agent_type", "provider", "type"])

# --- Claims / debate -------------------------------------------------
claims_extracted_total = Counter("claims_extracted_total", "Claims extracted", ["agent_type"])
claims_disputed_total = Counter("claims_disputed_total", "Claims disputed", [])
claims_verified_total = Counter("claims_verified_total", "Claims verified", [])
debate_rounds_total = Counter("debate_rounds_total", "Debate rounds run", [])
debate_resolution_total = Counter("debate_resolution_total", "Debate resolutions", ["resolution"])

# --- Reports & knowledge -----------------------------------------------
confidence_score_histogram = Histogram(
    "confidence_score_histogram", "Final report confidence scores", buckets=[0.2, 0.4, 0.6, 0.8, 1.0]
)
knowledge_base_entries_total = Counter("knowledge_base_entries_total", "KB entries indexed", ["tenant_id"])
knowledge_queries_total = Counter("knowledge_queries_total", "KB semantic search queries", ["tenant_id"])

human_reviews_total = Counter("human_reviews_total", "Human review actions", ["action"])
reports_generated_total = Counter("reports_generated_total", "Reports generated", ["tenant_id"])
reports_emailed_total = Counter("reports_emailed_total", "Reports emailed", [])

# --- Platform -------------------------------------------------------------
tier_limit_hits_total = Counter("tier_limit_hits_total", "Tier limit rejections", ["plan", "limit_type"])
websocket_connections_active = Gauge("websocket_connections_active", "Active WebSocket connections")
