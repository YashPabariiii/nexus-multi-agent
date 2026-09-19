import chromadb
import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.db.session import get_db
from app.schemas.common import LiveHealth, ReadyHealth

router = APIRouter(prefix="/health", tags=["health"])
settings = get_settings()


@router.get("/live", response_model=LiveHealth)
async def live():
    return LiveHealth()


@router.get("/ready", response_model=ReadyHealth)
async def ready(db: AsyncSession = Depends(get_db)):
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    redis_status = "ok"
    try:
        client = redis.from_url(settings.REDIS_URL)
        await client.ping()
        await client.aclose()
    except Exception:
        redis_status = "error"

    chroma_status = "ok"
    try:
        chroma_client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        chroma_client.heartbeat()
    except Exception:
        chroma_status = "error"

    return ReadyHealth(db=db_status, redis=redis_status, chroma=chroma_status)
