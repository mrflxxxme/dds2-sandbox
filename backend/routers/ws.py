"""
WebSocket endpoint for real-time sync progress notifications.

Replaces polling for sync status — frontend connects and receives
progress events as JSON messages.

Usage (frontend):
    const ws = new WebSocket('ws://host/api/v1/ws/sync?token=JWT');
    ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        // data = { event: "sync_progress", project_id: 1, progress: 50, message: "..." }
    };
"""

import asyncio
import json
import logging
from typing import Dict, Set
from weakref import WeakSet

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import jwt, JWTError

from backend.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Active WebSocket connections grouped by project_id
_connections: Dict[int, Set[WebSocket]] = {}
_lock = asyncio.Lock()


async def _add_connection(project_id: int, ws: WebSocket):
    async with _lock:
        if project_id not in _connections:
            _connections[project_id] = set()
        _connections[project_id].add(ws)


async def _remove_connection(project_id: int, ws: WebSocket):
    async with _lock:
        if project_id in _connections:
            _connections[project_id].discard(ws)
            if not _connections[project_id]:
                del _connections[project_id]


async def broadcast(project_id: int, event: str, data: dict):
    """
    Broadcast a message to all WebSocket connections for a project.

    Call from sync/scheduler code:
        from backend.routers.ws import broadcast
        await broadcast(project_id, "sync_progress", {"progress": 50, "message": "..."})
    """
    async with _lock:
        connections = list(_connections.get(project_id, set()))

    if not connections:
        return

    message = json.dumps({"event": event, "project_id": project_id, **data})
    dead = []
    for ws in connections:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)

    # Clean up dead connections
    for ws in dead:
        await _remove_connection(project_id, ws)


def _verify_ws_token(token: str) -> dict:
    """Verify JWT token from WebSocket query param."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        return payload
    except JWTError:
        return {}


@router.websocket("/ws/sync")
async def ws_sync(
    websocket: WebSocket,
    token: str = Query(...),
    project_id: int = Query(...),
):
    """
    WebSocket endpoint for real-time sync progress.

    Connect: ws://host/api/v1/ws/sync?token=JWT&project_id=1
    Receives JSON messages with sync events.
    """
    # Verify JWT
    payload = _verify_ws_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await websocket.accept()
    await _add_connection(project_id, websocket)
    logger.info("WS connected: project_id=%s, user=%s", project_id, payload.get("sub"))

    try:
        # Send initial connected message
        await websocket.send_json({
            "event": "connected",
            "project_id": project_id,
            "message": "Подключено к обновлениям синхронизации",
        })

        # Keep connection alive — wait for disconnect
        while True:
            try:
                # Wait for client messages (ping/pong or close)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive ping
                try:
                    await websocket.send_json({"event": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("WS error: %s", e)
    finally:
        await _remove_connection(project_id, websocket)
        logger.info("WS disconnected: project_id=%s", project_id)
