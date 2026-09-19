import asyncio
import time

import google.generativeai as genai

from app.config.settings import get_settings
from app.core.logging import logger

settings = get_settings()
genai.configure(api_key=settings.GEMINI_API_KEY)

MAX_RETRIES = 5

_ROLE_MAP = {"user": "user", "assistant": "model", "model": "model"}


def _to_gemini_format(messages: list[dict]) -> tuple[str | None, list[dict]]:
    system_instruction = None
    contents = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            system_instruction = (system_instruction + "\n" if system_instruction else "") + msg["content"]
            continue
        contents.append({"role": _ROLE_MAP.get(role, "user"), "parts": [msg["content"]]})
    return system_instruction, contents


async def call(
    messages: list[dict],
    model: str = settings.GEMINI_MODEL,
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> dict:
    """Returns {content, prompt_tokens, completion_tokens, latency_ms}."""
    system_instruction, contents = _to_gemini_format(messages)
    start = time.perf_counter()
    delay = 1.0
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            gemini_model = genai.GenerativeModel(model_name=model, system_instruction=system_instruction)
            response = await asyncio.to_thread(
                gemini_model.generate_content,
                contents,
                generation_config=genai.types.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            usage = getattr(response, "usage_metadata", None)
            content = response.text or ""
            logger.info(
                "gemini_call",
                model=model,
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                completion_tokens=getattr(usage, "candidates_token_count", None),
                latency_ms=latency_ms,
            )
            return {
                "content": content,
                "prompt_tokens": getattr(usage, "prompt_token_count", None),
                "completion_tokens": getattr(usage, "candidates_token_count", None),
                "latency_ms": latency_ms,
            }
        except Exception as exc:
            last_error = exc
            is_rate_limit = "429" in str(exc) or "quota" in str(exc).lower()
            if not is_rate_limit or attempt == MAX_RETRIES - 1:
                raise
            logger.warning("gemini_rate_limited", attempt=attempt, delay=delay)
            await asyncio.sleep(delay)
            delay *= 2

    raise last_error
