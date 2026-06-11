import asyncio
import json
import logging

from fastapi import WebSocket

from app.shared.redis import get_redis


log = logging.getLogger(__name__)
LIVE_CHAT_EVENTS_CHANNEL = "live_chat_events"


class LiveChatConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[str, set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, tenant_id: str) -> None:
        await websocket.accept()
        self.active_connections.setdefault(tenant_id, set()).add(websocket)

    def disconnect(self, websocket: WebSocket, tenant_id: str | None = None) -> None:
        if tenant_id:
            connections = self.active_connections.get(tenant_id)
            if not connections:
                return
            connections.discard(websocket)
            if not connections:
                self.active_connections.pop(tenant_id, None)
            return

        for room_tenant_id, connections in list(self.active_connections.items()):
            connections.discard(websocket)
            if not connections:
                self.active_connections.pop(room_tenant_id, None)

    async def broadcast(self, payload: dict, tenant_id: str | None = None) -> None:
        if not tenant_id:
            return

        disconnected = []
        connections = self.active_connections.get(tenant_id, set())
        for websocket in list(connections):
            try:
                await websocket.send_json(payload)
            except Exception:
                disconnected.append(websocket)

        for websocket in disconnected:
            self.disconnect(websocket, tenant_id=tenant_id)


live_chat_manager = LiveChatConnectionManager()


async def publish_live_chat_event(payload: dict, tenant_id: str | None) -> None:
    if not tenant_id:
        return

    redis = await get_redis()
    await redis.publish(
        LIVE_CHAT_EVENTS_CHANNEL,
        json.dumps(
            {
                "tenant_id": tenant_id,
                "payload": payload,
            },
            ensure_ascii=True,
        ),
    )


async def live_chat_pubsub_loop() -> None:
    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(LIVE_CHAT_EVENTS_CHANNEL)
    log.info("Live chat pub/sub subscriber started")

    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue

            try:
                data = json.loads(message.get("data") or "{}")
            except json.JSONDecodeError:
                log.warning("Skipping invalid live chat pub/sub payload")
                continue

            tenant_id = data.get("tenant_id")
            payload = data.get("payload")
            if not tenant_id or not isinstance(payload, dict):
                continue

            await live_chat_manager.broadcast(payload, tenant_id=tenant_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Live chat pub/sub subscriber stopped unexpectedly")
        raise
    finally:
        try:
            await pubsub.unsubscribe(LIVE_CHAT_EVENTS_CHANNEL)
        finally:
            await pubsub.aclose()
            log.info("Live chat pub/sub subscriber stopped")
