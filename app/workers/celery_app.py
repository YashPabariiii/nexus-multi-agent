from celery import Celery

from app.config.settings import get_settings

settings = get_settings()


def _redis_db(url: str, db: int) -> str:
    base = url.rsplit("/", 1)[0]
    return f"{base}/{db}"


celery_app = Celery(
    "nexus",
    broker=_redis_db(settings.REDIS_URL, 0),
    backend=_redis_db(settings.REDIS_URL, 1),
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)
