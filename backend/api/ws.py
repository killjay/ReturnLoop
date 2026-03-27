from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List
import json

router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections for live dashboard updates."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected dashboard clients."""
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for conn in dead:
            self.active_connections.remove(conn)

    async def broadcast_agent_trace(self, trace_data: dict):
        """Broadcast an agent trace event for the live Agent Trace panel."""
        await self.broadcast({
            "type": "agent_trace",
            "data": trace_data,
        })

    async def broadcast_metrics_update(self, metrics: dict):
        """Broadcast updated metrics (cost saved, miles saved, CO2)."""
        await self.broadcast({
            "type": "metrics_update",
            "data": metrics,
        })

    async def broadcast_return_update(self, return_data: dict):
        """Broadcast a return status change."""
        await self.broadcast({
            "type": "return_update",
            "data": return_data,
        })

    async def broadcast_voice_update(self, voice_data: dict):
        """Broadcast voice call transcript update."""
        await self.broadcast({
            "type": "voice_update",
            "data": voice_data,
        })


# Singleton manager used across the app
ws_manager = ConnectionManager()


@router.websocket("/live")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, receive any client messages
            data = await websocket.receive_text()
            # Client can send ping/pong or commands
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
