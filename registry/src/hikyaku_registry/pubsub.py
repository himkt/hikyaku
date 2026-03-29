"""PubSubManager for Redis Pub/Sub inbox notifications.

Channel pattern: inbox:{agent_id}
Payload: task_id string (lightweight; full Task fetched separately by SSE endpoint)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class _Subscription:
    """Async iterator wrapper around a Redis Pub/Sub subscription."""

    def __init__(self, pubsub) -> None:
        self._pubsub = pubsub

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        while True:
            msg = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if msg is not None and msg["type"] == "message":
                return msg["data"]


class PubSubManager:
    """Manages Redis Pub/Sub subscriptions for inbox notification channels."""

    def __init__(self, redis) -> None:
        self._redis = redis
        self._subscriptions: dict[str, Any] = {}

    async def publish(self, channel: str, message: str) -> None:
        """Publish a message (task_id) to a Redis Pub/Sub channel."""
        await self._redis.publish(channel, message)

    async def subscribe(self, channel: str) -> _Subscription:
        """Subscribe to a channel and return an async iterator that yields messages."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        self._subscriptions[channel] = pubsub
        return _Subscription(pubsub)

    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel and clean up resources."""
        pubsub = self._subscriptions.pop(channel, None)
        if pubsub is not None:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
