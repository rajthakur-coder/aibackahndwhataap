from fastapi import WebSocket


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
