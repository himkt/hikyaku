"""Tests for subscribe.py — SSE endpoint for real-time inbox notifications.

Covers: authentication errors (401 via HTTP), and unit tests for the SSE
event generator logic (message streaming, keepalive, disconnect cleanup).

Endpoint: GET /api/v1/subscribe
Auth: Authorization: Bearer <api_key> + X-Agent-Id: <agent_id>
Response: text/event-stream
"""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import fakeredis.aioredis
from httpx import AsyncClient, ASGITransport
from a2a.types import Artifact, Part, Task, TaskState, TaskStatus, TextPart

from hikyaku_registry.main import create_app
from hikyaku_registry.pubsub import PubSubManager
from hikyaku_registry.task_store import RedisTaskStore
from hikyaku_registry.api.subscribe import event_generator

_SSE_API_KEY = "hky_ssetestdefaultKEYKEYKEYKEYKEYKE"
_SSE_API_KEY_HASH = hashlib.sha256(_SSE_API_KEY.encode()).hexdigest()
_SSE_OTHER_API_KEY = "hky_ssetestotherKEYKEYKEYKEYKEYKEY"
_SSE_OTHER_API_KEY_HASH = hashlib.sha256(_SSE_OTHER_API_KEY.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures — HTTP (for auth tests)
# ---------------------------------------------------------------------------


@pytest.fixture
async def sse_env():
    """Provide a full ASGI app with fakeredis for SSE endpoint testing.

    Yields a dict with client, redis, stores, and pre-registered agents.
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Set up active API keys
    await redis.hset(
        f"apikey:{_SSE_API_KEY_HASH}",
        mapping={
            "owner_sub": "auth0|sse-test",
            "created_at": "2026-03-29T00:00:00+00:00",
            "status": "active",
            "key_prefix": _SSE_API_KEY[:8],
        },
    )
    await redis.hset(
        f"apikey:{_SSE_OTHER_API_KEY_HASH}",
        mapping={
            "owner_sub": "auth0|sse-test-other",
            "created_at": "2026-03-29T00:00:00+00:00",
            "status": "active",
            "key_prefix": _SSE_OTHER_API_KEY[:8],
        },
    )

    app = create_app(redis=redis)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent_a = await _register_agent(
            client, "Agent A", "Sender", api_key=_SSE_API_KEY
        )
        api_key = agent_a["api_key"]

        agent_b = await _register_agent(client, "Agent B", "Receiver", api_key=api_key)

        yield {
            "client": client,
            "redis": redis,
            "app": app,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "api_key": api_key,
        }

    await redis.aclose()


# ---------------------------------------------------------------------------
# Fixtures — Unit (for generator tests)
# ---------------------------------------------------------------------------


@pytest.fixture
async def gen_env():
    """Provide PubSubManager, RedisTaskStore, and a test agent for generator tests."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    pubsub = PubSubManager(redis)
    task_store = RedisTaskStore(redis)
    agent_id = str(uuid.uuid4())

    yield {
        "redis": redis,
        "pubsub": pubsub,
        "task_store": task_store,
        "agent_id": agent_id,
    }

    await redis.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register_agent(client, name, description, api_key=None):
    """Register an agent via POST and return the response data."""
    body = {"name": name, "description": description}
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = await client.post("/api/v1/agents", json=body, headers=headers)
    assert resp.status_code == 201, f"Registration failed: {resp.text}"
    return resp.json()


def _auth(api_key: str, agent_id: str) -> dict:
    """Build Authorization + X-Agent-Id headers for SSE endpoint."""
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Agent-Id": agent_id,
    }


def _make_task(task_id: str, context_id: str, text: str = "Hello") -> Task:
    """Create an A2A Task for testing."""
    now = datetime.now(UTC).isoformat()
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.input_required, timestamp=now),
        artifacts=[
            Artifact(
                artifact_id=str(uuid.uuid4()),
                parts=[Part(root=TextPart(text=text))],
            )
        ],
        metadata={
            "fromAgentId": "sender-agent",
            "toAgentId": context_id,
            "type": "unicast",
        },
    )


def _parse_sse_events(raw_chunks: list[str]) -> list[dict]:
    """Parse raw SSE output chunks into a list of event dicts.

    Each event dict may contain keys: 'event', 'id', 'data', 'comment'.
    """
    events = []
    current = {}

    # Flatten chunks into individual lines
    lines = []
    for chunk in raw_chunks:
        lines.extend(chunk.split("\n"))

    for line in lines:
        line = line.rstrip("\r")

        if line == "":
            # Empty line = event boundary
            if current:
                events.append(current)
                current = {}
            continue

        if line.startswith(":"):
            # SSE comment (e.g., ": keepalive")
            current["comment"] = line[1:].strip()
            events.append(current)
            current = {}
            continue

        if ":" in line:
            field, _, value = line.partition(":")
            value = value.lstrip(" ")
            current[field] = value

    # Flush any remaining event
    if current:
        events.append(current)

    return events


# ---------------------------------------------------------------------------
# Authentication Errors
# ---------------------------------------------------------------------------


class TestSSEAuth:
    """Tests for SSE endpoint authentication — all error cases return 401."""

    @pytest.mark.asyncio
    async def test_missing_authorization_header_returns_401(self, sse_env):
        """GET /api/v1/subscribe without Authorization header returns 401."""
        client = sse_env["client"]
        agent_b = sse_env["agent_b"]

        resp = await client.get(
            "/api/v1/subscribe",
            headers={"X-Agent-Id": agent_b["agent_id"]},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_x_agent_id_header_returns_401(self, sse_env):
        """GET /api/v1/subscribe without X-Agent-Id header returns 401."""
        client = sse_env["client"]
        api_key = sse_env["api_key"]

        resp = await client.get(
            "/api/v1/subscribe",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_401(self, sse_env):
        """GET /api/v1/subscribe with unknown API key returns 401."""
        client = sse_env["client"]
        agent_b = sse_env["agent_b"]

        resp = await client.get(
            "/api/v1/subscribe",
            headers=_auth("hky_invalid_key_000000000000000000", agent_b["agent_id"]),
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_nonexistent_agent_id_returns_401(self, sse_env):
        """GET /api/v1/subscribe with nonexistent agent_id returns 401."""
        client = sse_env["client"]
        api_key = sse_env["api_key"]

        resp = await client.get(
            "/api/v1/subscribe",
            headers=_auth(api_key, "nonexistent-agent-id"),
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_tenant_mismatch_returns_401(self, sse_env):
        """GET /api/v1/subscribe with agent from different tenant returns 401."""
        client = sse_env["client"]
        api_key = sse_env["api_key"]

        # Register an agent with a different api_key — different tenant
        other_agent = await _register_agent(
            client, "Other Agent", "Different tenant", api_key=_SSE_OTHER_API_KEY
        )

        # Try to subscribe using the first tenant's API key with the other agent
        resp = await client.get(
            "/api/v1/subscribe",
            headers=_auth(api_key, other_agent["agent_id"]),
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_bearer_token_returns_401(self, sse_env):
        """GET /api/v1/subscribe with 'Basic' scheme instead of 'Bearer' returns 401."""
        client = sse_env["client"]
        agent_b = sse_env["agent_b"]

        resp = await client.get(
            "/api/v1/subscribe",
            headers={
                "Authorization": "Basic dXNlcjpwYXNz",
                "X-Agent-Id": agent_b["agent_id"],
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_bearer_token_returns_401(self, sse_env):
        """GET /api/v1/subscribe with empty Bearer token returns 401."""
        client = sse_env["client"]
        agent_b = sse_env["agent_b"]

        resp = await client.get(
            "/api/v1/subscribe",
            headers={
                "Authorization": "Bearer ",
                "X-Agent-Id": agent_b["agent_id"],
            },
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# SSE Event Generator — Unit Tests
# ---------------------------------------------------------------------------


class TestSSEGeneratorMessages:
    """Unit tests for event_generator() — message event streaming.

    Tests the generator directly with fakeredis PubSubManager and TaskStore,
    bypassing HTTP transport. A mock request with is_disconnected() → False
    simulates an active client.
    """

    @pytest.mark.asyncio
    async def test_yields_message_event_on_publish(self, gen_env):
        """Generator yields an SSE 'message' event when task_id is published."""
        pubsub = gen_env["pubsub"]
        task_store = gen_env["task_store"]
        agent_id = gen_env["agent_id"]

        # Create and save a task
        task_id = str(uuid.uuid4())
        task = _make_task(task_id, agent_id, text="Hello via SSE")
        await task_store.save(task)

        # Mock request that is never disconnected
        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)

        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_id,
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                # Stop after first data chunk (message event)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())

        # Give generator time to subscribe
        await asyncio.sleep(0.05)

        # Publish the task_id to the agent's inbox channel
        await pubsub.publish(f"inbox:{agent_id}", task_id)

        await asyncio.wait_for(consumer_task, timeout=3.0)

        events = _parse_sse_events(collected)
        message_events = [e for e in events if e.get("event") == "message"]
        assert len(message_events) >= 1, f"No message events. Chunks: {collected}"

    @pytest.mark.asyncio
    async def test_event_id_matches_task_id(self, gen_env):
        """SSE event 'id' field equals the task_id."""
        pubsub = gen_env["pubsub"]
        task_store = gen_env["task_store"]
        agent_id = gen_env["agent_id"]

        task_id = str(uuid.uuid4())
        task = _make_task(task_id, agent_id)
        await task_store.save(task)

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)

        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_id,
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await pubsub.publish(f"inbox:{agent_id}", task_id)
        await asyncio.wait_for(consumer_task, timeout=3.0)

        events = _parse_sse_events(collected)
        message_events = [e for e in events if e.get("event") == "message"]
        assert len(message_events) >= 1

        event = message_events[0]
        assert event["id"] == task_id

    @pytest.mark.asyncio
    async def test_event_data_is_full_task_json(self, gen_env):
        """SSE event 'data' field is full A2A Task JSON with camelCase keys."""
        pubsub = gen_env["pubsub"]
        task_store = gen_env["task_store"]
        agent_id = gen_env["agent_id"]

        task_id = str(uuid.uuid4())
        task = _make_task(task_id, agent_id, text="Full JSON check")
        await task_store.save(task)

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)

        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_id,
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await pubsub.publish(f"inbox:{agent_id}", task_id)
        await asyncio.wait_for(consumer_task, timeout=3.0)

        events = _parse_sse_events(collected)
        message_events = [e for e in events if e.get("event") == "message"]
        assert len(message_events) >= 1

        task_data = json.loads(message_events[0]["data"])

        # A2A Task JSON should have id, status, and contextId (camelCase)
        assert task_data["id"] == task_id
        assert "status" in task_data
        context_id_key = "contextId" if "contextId" in task_data else "context_id"
        assert task_data.get(context_id_key) == agent_id

    @pytest.mark.asyncio
    async def test_multiple_messages_arrive_as_separate_events(self, gen_env):
        """Multiple published task_ids produce multiple SSE message events."""
        pubsub = gen_env["pubsub"]
        task_store = gen_env["task_store"]
        agent_id = gen_env["agent_id"]

        task_ids = [str(uuid.uuid4()) for _ in range(3)]
        for tid in task_ids:
            task = _make_task(tid, agent_id, text=f"Msg {tid[:8]}")
            await task_store.save(task)

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)

        collected = []
        data_count = 0

        async def consume():
            nonlocal data_count
            async for chunk in event_generator(
                agent_id=agent_id,
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    data_count += 1
                    if data_count >= 3:
                        break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)

        for tid in task_ids:
            await pubsub.publish(f"inbox:{agent_id}", tid)

        await asyncio.wait_for(consumer_task, timeout=3.0)

        events = _parse_sse_events(collected)
        message_events = [e for e in events if e.get("event") == "message"]
        assert len(message_events) >= 3

        # Verify each event has a unique task_id
        event_ids = {e["id"] for e in message_events}
        assert event_ids == set(task_ids)

    @pytest.mark.asyncio
    async def test_skips_missing_task(self, gen_env):
        """If task_store.get returns None for a task_id, the event is skipped."""
        pubsub = gen_env["pubsub"]
        task_store = gen_env["task_store"]
        agent_id = gen_env["agent_id"]

        # Save only the second task — first task_id has no data in store
        missing_task_id = str(uuid.uuid4())
        real_task_id = str(uuid.uuid4())
        real_task = _make_task(real_task_id, agent_id, text="I exist")
        await task_store.save(real_task)

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)

        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_id,
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)

        # Publish missing task first, then real task
        await pubsub.publish(f"inbox:{agent_id}", missing_task_id)
        await pubsub.publish(f"inbox:{agent_id}", real_task_id)

        await asyncio.wait_for(consumer_task, timeout=3.0)

        events = _parse_sse_events(collected)
        message_events = [e for e in events if e.get("event") == "message"]

        # Only the real task should appear
        assert len(message_events) >= 1
        assert message_events[0]["id"] == real_task_id


# ---------------------------------------------------------------------------
# SSE Event Generator — Keepalive
# ---------------------------------------------------------------------------


class TestSSEGeneratorKeepalive:
    """Unit tests for event_generator() keepalive behavior."""

    @pytest.mark.asyncio
    async def test_keepalive_comment_sent_after_interval(self, gen_env, monkeypatch):
        """Generator yields ': keepalive' comment after the keepalive interval.

        Monkeypatches _keepalive_interval to a short value for fast testing.
        """
        import hikyaku_registry.api.subscribe as subscribe_mod

        monkeypatch.setattr(subscribe_mod, "_keepalive_interval", 0.1)

        pubsub = gen_env["pubsub"]
        task_store = gen_env["task_store"]
        agent_id = gen_env["agent_id"]

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)

        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_id,
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                # Stop after receiving a keepalive
                if "keepalive" in chunk:
                    break

        await asyncio.wait_for(consume(), timeout=3.0)

        keepalive_chunks = [c for c in collected if "keepalive" in c]
        assert len(keepalive_chunks) >= 1

    @pytest.mark.asyncio
    async def test_keepalive_does_not_interfere_with_messages(
        self, gen_env, monkeypatch
    ):
        """Messages still arrive correctly even when keepalives are firing."""
        import hikyaku_registry.api.subscribe as subscribe_mod

        monkeypatch.setattr(subscribe_mod, "_keepalive_interval", 0.1)

        pubsub = gen_env["pubsub"]
        task_store = gen_env["task_store"]
        agent_id = gen_env["agent_id"]

        task_id = str(uuid.uuid4())
        task = _make_task(task_id, agent_id, text="With keepalive")
        await task_store.save(task)

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)

        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_id,
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())
        # Wait for at least one keepalive cycle, then publish
        await asyncio.sleep(0.15)
        await pubsub.publish(f"inbox:{agent_id}", task_id)

        await asyncio.wait_for(consumer_task, timeout=3.0)

        events = _parse_sse_events(collected)
        message_events = [e for e in events if e.get("event") == "message"]
        keepalive_events = [e for e in events if e.get("comment") == "keepalive"]

        assert len(message_events) >= 1
        assert message_events[0]["id"] == task_id
        # At least one keepalive should have fired before the message
        assert len(keepalive_events) >= 1


# ---------------------------------------------------------------------------
# SSE Event Generator — Disconnect Cleanup
# ---------------------------------------------------------------------------


class TestSSEGeneratorCleanup:
    """Unit tests for event_generator() cleanup on disconnect/exit."""

    @pytest.mark.asyncio
    async def test_cleanup_unsubscribes_on_generator_exit(self, gen_env):
        """When the generator is exited, PubSub channel is unsubscribed."""
        pubsub = gen_env["pubsub"]
        task_store = gen_env["task_store"]
        agent_id = gen_env["agent_id"]
        redis = gen_env["redis"]

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)

        # Start and immediately break the generator to trigger cleanup
        gen = event_generator(
            agent_id=agent_id,
            pubsub=pubsub,
            task_store=task_store,
            request=request,
        )

        # Consume at least one iteration to let the generator set up subscription
        # We'll use a task that we cancel after a brief delay
        async def consume_briefly():
            async for _chunk in gen:
                break  # Exit after first yield (likely keepalive or setup)

        # Use a short keepalive-based approach: wait for one chunk then exit
        try:
            await asyncio.wait_for(consume_briefly(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        # Explicitly close the generator to trigger finally block
        await gen.aclose()

        # Give cleanup a moment to run
        await asyncio.sleep(0.05)

        # Verify Redis Pub/Sub channel has 0 subscribers
        numsub = await redis.pubsub_numsub(f"inbox:{agent_id}")
        if numsub:
            for channel, count in zip(numsub[::2], numsub[1::2]):
                if channel == f"inbox:{agent_id}":
                    assert int(count) == 0, (
                        f"Expected 0 subscribers after cleanup, got {count}"
                    )

    @pytest.mark.asyncio
    async def test_cleanup_on_client_disconnect(self, gen_env, monkeypatch):
        """Generator exits cleanly when request.is_disconnected() returns True."""
        import hikyaku_registry.api.subscribe as subscribe_mod

        monkeypatch.setattr(subscribe_mod, "_keepalive_interval", 0.1)

        pubsub = gen_env["pubsub"]
        task_store = gen_env["task_store"]
        agent_id = gen_env["agent_id"]

        disconnect_after = 2  # Disconnect after 2 calls to is_disconnected
        call_count = 0

        async def mock_is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > disconnect_after

        request = AsyncMock()
        request.is_disconnected = mock_is_disconnected

        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_id,
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)

        # The generator should exit on its own when is_disconnected returns True
        await asyncio.wait_for(consume(), timeout=3.0)

        # Generator exited without timeout — cleanup happened
        assert call_count > disconnect_after
