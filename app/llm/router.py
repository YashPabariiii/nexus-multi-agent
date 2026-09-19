from collections.abc import Callable
from dataclasses import dataclass

from app.config.settings import get_settings
from app.core import tracing
from app.llm import gemini_client, groq_client

settings = get_settings()


@dataclass
class LLMBinding:
    provider: str
    model: str
    call: Callable


AGENT_LLM_MAP = {
    "supervisor": ("groq", settings.GROQ_MODEL_LARGE),
    "web_search": ("groq", settings.GROQ_MODEL_SMALL),
    "domain_knowledge": ("groq", settings.GROQ_MODEL_LARGE),
    "data_analyst": ("gemini", settings.GEMINI_MODEL),
    "fact_checker": ("groq", settings.GROQ_MODEL_LARGE),
    "synthesis": ("gemini", settings.GEMINI_MODEL),
    "writer": ("groq", settings.GROQ_MODEL_LARGE),
}

_MODULES = {"groq": groq_client, "gemini": gemini_client}


def _traced(provider: str, module) -> Callable:
    """Wrap the provider call in a Langfuse `generation` observation (input, output, token usage).
    `module.call` is looked up per invocation so monkeypatched clients in tests are honoured."""

    async def call(messages: list[dict], model: str, **kwargs) -> dict:
        with tracing.observation(
            f"{provider}/{model}", as_type="generation", model=model, input=messages, metadata={"provider": provider}
        ) as gen:
            result = await module.call(messages, model=model, **kwargs)
            usage = {k: v for k, v in (("input", result.get("prompt_tokens")), ("output", result.get("completion_tokens"))) if v}
            gen.update(output=result.get("content"), usage_details=usage or None)
            return result

    return call


def get_llm(agent_type: str) -> LLMBinding:
    provider, model = AGENT_LLM_MAP[agent_type]
    return LLMBinding(provider=provider, model=model, call=_traced(provider, _MODULES[provider]))
