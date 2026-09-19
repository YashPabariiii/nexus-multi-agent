import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.auth import decode_token

logger = structlog.get_logger()


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        tenant_id = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                tenant_id = str(decode_token(auth_header.removeprefix("Bearer ")).tenant_id)
            except Exception:
                pass

        start = time.perf_counter()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id, tenant_id=tenant_id)
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request",
                correlation_id=correlation_id,
                tenant_id=tenant_id,
                path=request.url.path,
                method=request.method,
                status_code=status_code,
                latency_ms=latency_ms,
            )
            structlog.contextvars.clear_contextvars()
