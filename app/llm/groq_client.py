import asyncio
import time

from groq import AsyncGroq

from app.config.settings import get_settings
from app.core.logging import logger

settings = get_settings()
_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

MAX_RETRIES = 5


async def call(
    messages: list[dict],
    model: str = settings.GROQ_MODEL_LARGE,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> dict:
    """Returns {content, prompt_tokens, completion_tokens, latency_ms}."""
    start = time.perf_counter()
    delay = 1.0
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            response = await _client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            usage = response.usage
            content = response.choices[0].message.content or ""
            logger.info(
                "groq_call",
                model=model,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                latency_ms=latency_ms,
            )
            return {
                "content": content,
                "prompt_tokens": usage.prompt_tokens if usage else None,
                "completion_tokens": usage.completion_tokens if usage else None,
                "latency_ms": latency_ms,
            }
        except Exception as exc:
            last_error = exc
            is_rate_limit = "rate_limit" in str(exc).lower() or "429" in str(exc)
            if not is_rate_limit or attempt == MAX_RETRIES - 1:
                raise
            logger.warning("groq_rate_limited", attempt=attempt, delay=delay)
            await asyncio.sleep(delay)
            delay *= 2

    raise last_error
