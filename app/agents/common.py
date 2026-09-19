import json
import re
import uuid

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.core.logging import logger
from app.core.metrics import agent_calls_total, agent_latency_seconds, agent_tokens_total, claims_extracted_total
from app.models.agent_trace import AgentTrace

settings = get_settings()

_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def parse_llm_json(text: str) -> dict | list:
    """Extract and parse a JSON object/array from an LLM response, tolerating markdown fences."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(cleaned)
        if not match:
            logger.warning("llm_json_parse_failed", snippet=cleaned[:200])
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("llm_json_parse_failed", snippet=cleaned[:200])
            return {}


async def publish_event(brief_id: str, event_type: str, agent_type: str, message: str, confidence: float | None = None) -> None:
    client = redis.from_url(settings.REDIS_URL)
    try:
        payload = {
            "event_type": event_type,
            "agent_type": agent_type,
            "message": message,
            "confidence": confidence,
        }
        await client.publish(f"brief:{brief_id}:events", json.dumps(payload))
    finally:
        await client.aclose()


async def record_trace(
    db: AsyncSession,
    brief_id: str,
    agent_type: str,
    llm_provider: str,
    llm_model: str,
    input_summary: str = "",
    output_summary: str = "",
    tool_calls: list | None = None,
    reasoning_trace: str = "",
    claims: list | None = None,
    confidence_scores: dict | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
    status: str = "complete",
) -> dict:
    agent_id = f"{agent_type}-{uuid.uuid4().hex[:8]}"
    trace = AgentTrace(
        brief_id=uuid.UUID(brief_id),
        agent_id=agent_id,
        agent_type=agent_type,
        llm_provider=llm_provider,
        llm_model=llm_model,
        input_summary=input_summary[:4000] if input_summary else None,
        output_summary=output_summary[:4000] if output_summary else None,
        tool_calls=tool_calls,
        reasoning_trace=reasoning_trace[:4000] if reasoning_trace else None,
        claims=claims,
        confidence_scores=confidence_scores,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        status=status,
    )
    db.add(trace)
    await db.commit()

    agent_calls_total.labels(agent_type=agent_type, provider=llm_provider, status=status).inc()
    if latency_ms is not None:
        agent_latency_seconds.labels(agent_type=agent_type, provider=llm_provider).observe(latency_ms / 1000)
    if prompt_tokens:
        agent_tokens_total.labels(agent_type=agent_type, provider=llm_provider, type="prompt").inc(prompt_tokens)
    if completion_tokens:
        agent_tokens_total.labels(agent_type=agent_type, provider=llm_provider, type="completion").inc(completion_tokens)
    if claims:
        claims_extracted_total.labels(agent_type=agent_type).inc(len(claims))

    await publish_event(
        brief_id=brief_id,
        event_type="agent_update",
        agent_type=agent_type,
        message=output_summary[:280] if output_summary else f"{agent_type} {status}",
    )

    return {"agent_id": agent_id, "agent_type": agent_type, "status": status}
