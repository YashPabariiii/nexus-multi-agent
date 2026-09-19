"""Langfuse tracing. Every helper is a no-op when LANGFUSE_* keys are unset, so tests/CI
never need a Langfuse server and the hot path stays free of conditionals at call sites.

Trace tree per brief run:  research_brief (agent) -> <node> (agent) -> <provider/model> (generation) | <tool> (tool)
"""

from contextlib import nullcontext
from functools import lru_cache, wraps

from app.config.settings import get_settings

settings = get_settings()


class _NoopSpan:
    trace_id = None

    def update(self, **_):
        return self

    def set_trace_io(self, **_):
        return self

    def score_trace(self, **_):
        return None


_NOOP = _NoopSpan()


def enabled() -> bool:
    return bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


@lru_cache
def client():
    if not enabled():
        return None
    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        base_url=settings.LANGFUSE_HOST,
        environment=settings.ENVIRONMENT,
    )


def observation(name: str, as_type: str = "span", **kwargs):
    """Context manager yielding a Langfuse observation (or a no-op stand-in)."""
    lf = client()
    return lf.start_as_current_observation(name=name, as_type=as_type, **kwargs) if lf else nullcontext(_NOOP)


def trace_attributes(**kwargs):
    """Trace-level attributes (session_id / tags / metadata / trace_name) inherited by every nested span."""
    if not client():
        return nullcontext()
    from langfuse import propagate_attributes

    return propagate_attributes(**kwargs)


def traced_tool(fn):
    """Wrap a plain tool function in a Langfuse `tool` observation. Apply *under* `@tool`."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        with observation(fn.__name__, as_type="tool", input=kwargs or list(args)) as span:
            result = fn(*args, **kwargs)
            span.update(output=result)
            return result

    return wrapper


def trace_url(trace_id: str | None) -> str | None:
    lf = client()
    return lf.get_trace_url(trace_id=trace_id) if lf and trace_id else None


def flush() -> None:
    lf = client()
    if lf:
        lf.flush()
