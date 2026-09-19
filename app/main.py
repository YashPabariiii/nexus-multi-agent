from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select

from app.core.auth import generate_api_key, hash_api_key
from app.core.exceptions import NexusError, nexus_error_handler, unhandled_exception_handler
from app.core.logging import configure_logging, logger
from app.core.metrics import limiter
from app.core.middleware import CorrelationMiddleware
from app.db.session import AsyncSessionLocal
from app.models.tenant import Tenant
from app.routes import auth, briefs, health, knowledge, reports, stream

DEMO_TENANT_EMAIL = "demo@nexus.local"


async def seed_demo_tenant() -> None:
    async with AsyncSessionLocal() as db:
        existing = await db.scalar(select(Tenant).where(Tenant.email == DEMO_TENANT_EMAIL))
        if existing:
            return
        api_key = generate_api_key()
        tenant = Tenant(
            name="Demo Tenant",
            email=DEMO_TENANT_EMAIL,
            api_key_hash=hash_api_key(api_key),
            plan="free",
            onboarded=True,
        )
        db.add(tenant)
        await db.commit()
        logger.info("demo_tenant_seeded", email=DEMO_TENANT_EMAIL, api_key=api_key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await seed_demo_tenant()
    yield


app = FastAPI(title="Nexus", version="0.1.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(NexusError, nexus_error_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.add_middleware(CorrelationMiddleware)

app.include_router(auth.router)
app.include_router(health.router)
app.include_router(briefs.router)
app.include_router(stream.router)
app.include_router(knowledge.router)
app.include_router(reports.router)


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
