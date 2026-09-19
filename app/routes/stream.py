import asyncio
import json
import uuid
from datetime import UTC, datetime

import redis.asyncio as redis
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.config.settings import get_settings
from app.core.auth import decode_token
from app.core.metrics import websocket_connections_active
from app.db.session import AsyncSessionLocal
from app.models.brief import ResearchBrief

router = APIRouter(prefix="/v1/briefs", tags=["stream"])
settings = get_settings()

HEARTBEAT_SECONDS = 30
TERMINAL_EVENTS = {"brief_complete", "brief_failed"}


@router.websocket("/{brief_id}/stream")
async def stream_brief(websocket: WebSocket, brief_id: uuid.UUID, token: str = Query(...)):
    try:
        current_tenant = decode_token(token)
    except Exception:
        await websocket.close(code=4401)
        return

    async with AsyncSessionLocal() as db:
        brief = await db.scalar(
            select(ResearchBrief).where(ResearchBrief.id == brief_id, ResearchBrief.tenant_id == current_tenant.tenant_id)
        )
    if brief is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    websocket_connections_active.inc()

    client = redis.from_url(settings.REDIS_URL)
    pubsub = client.pubsub()
    channel = f"brief:{brief_id}:events"
    await pubsub.subscribe(channel)

    async def receive_loop():
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    receiver = asyncio.create_task(receive_loop())

    try:
        last_heartbeat = asyncio.get_event_loop().time()
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                payload = json.loads(message["data"])
                payload["timestamp"] = datetime.now(UTC).isoformat()
                await websocket.send_json(payload)
                if payload.get("event_type") in TERMINAL_EVENTS:
                    break

            now = asyncio.get_event_loop().time()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                await websocket.send_json({"event_type": "heartbeat", "timestamp": datetime.now(UTC).isoformat()})
                last_heartbeat = now

            if receiver.done():
                break
    except WebSocketDisconnect:
        pass
    finally:
        websocket_connections_active.dec()
        receiver.cancel()
        await pubsub.unsubscribe(channel)
        await client.aclose()
        try:
            await websocket.close()
        except RuntimeError:
            pass
