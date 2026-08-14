"""
WebSocket Connection Manager for Live Transcript & Incident Updates.

Maintains active WebSocket connections per meeting ID and broadcasts real-time
transcript segments and events to all connected room clients.
"""

import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages active WebSocket connections grouped by meeting_id.
    """

    def __init__(self) -> None:
        # Map meeting_id (str) -> set of connected WebSocket instances
        self.active_connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, meeting_id: str) -> None:
        """Accept connection and add to meeting connection pool."""
        await websocket.accept()
        self.active_connections[meeting_id].add(websocket)
        logger.info(
            "WebSocket client connected to meeting %s (total: %d)",
            meeting_id[:8],
            len(self.active_connections[meeting_id]),
        )

    def disconnect(self, websocket: WebSocket, meeting_id: str) -> None:
        """Remove connection from meeting pool."""
        if meeting_id in self.active_connections:
            self.active_connections[meeting_id].discard(websocket)
            if not self.active_connections[meeting_id]:
                del self.active_connections[meeting_id]
        logger.info("WebSocket client disconnected from meeting %s", meeting_id[:8])

    async def broadcast(self, meeting_id: str, message: dict[str, Any]) -> None:
        """Broadcast JSON message to all WebSocket clients listening to meeting_id."""
        connections = self.active_connections.get(meeting_id)
        if not connections:
            return

        payload = json.dumps(message, default=str)
        disconnected: list[WebSocket] = []

        for connection in list(connections):
            try:
                await connection.send_text(payload)
            except Exception as exc:
                logger.warning(
                    "Error sending WS message to client in meeting %s: %s",
                    meeting_id[:8],
                    exc,
                )
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn, meeting_id)


def broadcast_event_sync(meeting_id: str, event_type: str, item_data: dict[str, Any], evidence_data: list[dict[str, Any]] | None = None) -> None:
    """
    Sync helper for reasoning nodes to broadcast live intelligence items.
    """
    import os
    import httpx

    backend_url = os.environ.get("BACKEND_INTERNAL_URL", "http://127.0.0.1:8000")
    url = f"{backend_url}/api/meetings/{meeting_id}/broadcast"
    payload = {
        "type": event_type,
        "meeting_id": meeting_id,
        "item": item_data,
        "evidence": evidence_data or [],
    }
    try:
        httpx.post(url, json=payload, timeout=2.0)
    except Exception as exc:
        logger.debug("Sync broadcast post failed (normal if running offline/test): %s", exc)


# Global singleton instance
ws_manager = ConnectionManager()

