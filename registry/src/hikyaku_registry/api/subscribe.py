"""SSE endpoint for real-time inbox notifications.

GET /api/v1/subscribe — authenticated via Authorization + X-Agent-Id headers.
Streams A2A Task JSON as SSE events when messages arrive in the agent's inbox.
Sends keepalive comments every 30 seconds (configurable via _keepalive_interval).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from hikyaku_registry.auth import get_authenticated_agent
from hikyaku_registry.pubsub import PubSubManager
from hikyaku_registry.task_store import RedisTaskStore

subscribe_router = APIRouter()

_keepalive_interval: float = 30.0
_poll_interval: float = 0.5


def _get_pubsub() -> PubSubManager:
    raise RuntimeError("PubSubManager dependency not configured")


def _get_task_store() -> RedisTaskStore:
    raise RuntimeError("RedisTaskStore dependency not configured")


async def event_generator(
    agent_id: str,
    pubsub: PubSubManager,
    task_store: RedisTaskStore,
    request: Request,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE events for an agent's inbox.

    Subscribes to Redis Pub/Sub channel inbox:{agent_id}, fetches full Task
    objects from task_store, and yields them as SSE message events. Sends
    keepalive comments every _keepalive_interval seconds.
    """
    channel = f"inbox:{agent_id}"
    subscription = await pubsub.subscribe(channel)
    try:
        last_keepalive = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            try:
                timeout = min(_poll_interval, _keepalive_interval)
                msg = await asyncio.wait_for(subscription.__anext__(), timeout=timeout)
                # msg is a task_id; fetch the full Task
                task = await task_store.get(msg)
                if task is not None:
                    task_json = json.dumps(task.model_dump(mode="json", by_alias=True))
                    yield f"event: message\nid: {msg}\ndata: {task_json}\n\n"
                last_keepalive = time.monotonic()
            except asyncio.TimeoutError:
                now = time.monotonic()
                if now - last_keepalive >= _keepalive_interval:
                    yield ": keepalive\n\n"
                    last_keepalive = now
            except (asyncio.CancelledError, GeneratorExit):
                break
    finally:
        await pubsub.unsubscribe(channel)


@subscribe_router.get("/subscribe")
async def subscribe(
    request: Request,
    auth: tuple[str, str] = Depends(get_authenticated_agent),
    pubsub: PubSubManager = Depends(_get_pubsub),
    task_store: RedisTaskStore = Depends(_get_task_store),
):
    agent_id, tenant_id = auth

    return StreamingResponse(
        event_generator(agent_id, pubsub, task_store, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
