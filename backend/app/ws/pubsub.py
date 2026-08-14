"""
Redis Pub/Sub Manager for Real-Time Event Distribution.

Allows multiple backend workers and LangGraph reasoning nodes to publish
events (facts, assumptions, decisions, action_items, conflicts) to Redis channels.
Subscribers forward events to WebSocket clients connected to /meetings/{id}/live.
"""

import json
import logging
import os
from typing import Any, Callable

import redis.asyncio as aioredis

from app.config.settings import settings

logger = logging.getLogger(__name__)


class RedisPubSubManager:
    """
    Manages Redis Pub/Sub channels per meeting for scaling real-time WebSocket distribution.
    """

    def __init__(self) -> None:
        self.redis_url = settings.redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._redis_client: aioredis.Redis | None = None

    async def get_client(self) -> aioredis.Redis:
        if self._redis_client is None:
            self._redis_client = aioredis.from_url(self.redis_url, decode_responses=True)
        return self._redis_client

    async def publish_event(self, meeting_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """Publishes an event payload to meeting channel 'meeting:<id>:events'."""
        try:
            client = await self.get_client()
            channel = f"meeting:{meeting_id}:events"
            message = {
                "type": event_type,
                "meeting_id": meeting_id,
                "data": payload,
            }
            await client.publish(channel, json.dumps(message, default=str))
            logger.debug("Published event %s to Redis channel %s", event_type, channel)
        except Exception as exc:
            logger.warning("Redis publish failed for meeting %s: %s", meeting_id[:8], exc)

    async def listen_meeting(self, meeting_id: str, callback: Callable[[dict[str, Any]], None]) -> None:
        """Listens to Redis channel 'meeting:<id>:events' and calls callback on message."""
        try:
            client = await self.get_client()
            pubsub = client.pubsub()
            channel = f"meeting:{meeting_id}:events"
            await pubsub.subscribe(channel)
            logger.info("Subscribed to Redis channel %s", channel)

            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await callback(data)
                    except Exception as exc:
                        logger.warning("Error handling Redis message: %s", exc)
        except Exception as exc:
            logger.warning("Redis subscription error for meeting %s: %s", meeting_id[:8], exc)


# Global instance
redis_pubsub = RedisPubSubManager()
