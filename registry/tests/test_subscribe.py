"""Tests for subscribe.py — SSE endpoint for real-time inbox notifications.

Covers: authentication errors (401), successful SSE connection with message
streaming, keepalive comments, and disconnect cleanup (Pub/Sub unsubscribe).

Endpoint: GET /api/v1/subscribe
Auth: Authorization: Bearer <api_key> + X-Agent-Id: <agent_id>
Response: text/event-stream
"""

import asyncio
import json
import uuid

import pytest
import fakeredis.aioredis
from httpx import AsyncClient, ASGITransport

from hikyaku_registry.main import create_app
from hikyaku_registry.pubsub import PubSubManager
from hikyaku_registry.task_store import RedisTaskStore
from hikyaku_registry.registry_store import RegistryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def sse_env():
    """Provide a full ASGI app with fakeredis for SSE endpoint testing.

    Yields a dict with client, redis, stores, and pre-registered agents.
    First agent creates a new tenant; second agent joins via returned api_key.
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app = create_app(redis=redis)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register agent_a without api_key — creates a new tenant
        agent_a = await _register_agent(client, "Agent A", "Sender")
        api_key = agent_a["api_key"]

        # Register agent_b with agent_a's api_key — joins the same tenant
        agent_b = await _register_agent(
            client, "Agent B", "Receiver", api_key=api_key
        )

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


def _jsonrpc(method: str, params: dict) -> dict:
    """Build a JSON-RPC 2.0 request."""
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": str(uuid.uuid4()),
    }


async def _send_message(client, api_key, agent_id, destination, text="Hello"):
    """Send a unicast message via A2A SendMessage."""
    payload = _jsonrpc("SendMessage", {
        "message": {
            "messageId": str(uuid.uuid4()),
            "role": "user",
            "parts": [{"kind": "text", "text": text}],
            "metadata": {"destination": destination},
        },
    })
    headers = _auth(api_key, agent_id)
    resp = await client.post("/", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data, f"Expected result, got: {data}"
    return data["result"]


def _parse_sse_events(raw_lines: list[str]) -> list[dict]:
    """Parse raw SSE lines into a list of event dicts.

    Each event dict has keys: 'event', 'id', 'data', 'comment'.
    """
    events = []
    current = {}

    for line in raw_lines:
        line = line.rstrip("\n").rstrip("\r")

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

        # Register an agent without api_key — creates a new (different) tenant
        other_agent = await _register_agent(
            client, "Other Agent", "Different tenant"
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
# Successful SSE Connection
# ---------------------------------------------------------------------------


class TestSSEConnection:
    """Tests for successful SSE connection and message streaming."""

    @pytest.mark.asyncio
    async def test_returns_event_stream_content_type(self, sse_env):
        """GET /api/v1/subscribe returns text/event-stream content type."""
        client = sse_env["client"]
        agent_b = sse_env["agent_b"]
        api_key = sse_env["api_key"]

        collected_lines = []

        async with client.stream(
            "GET",
            "/api/v1/subscribe",
            headers=_auth(api_key, agent_b["agent_id"]),
        ) as resp:
            assert resp.status_code == 200
            content_type = resp.headers.get("content-type", "")
            assert "text/event-stream" in content_type
            # Close immediately — we only needed the headers
            await resp.aclose()

    @pytest.mark.asyncio
    async def test_receives_message_event_on_publish(self, sse_env):
        """SSE stream receives a 'message' event when a message is delivered."""
        client = sse_env["client"]
        agent_a = sse_env["agent_a"]
        agent_b = sse_env["agent_b"]
        api_key = sse_env["api_key"]

        collected_lines = []

        async def read_sse():
            async with client.stream(
                "GET",
                "/api/v1/subscribe",
                headers=_auth(api_key, agent_b["agent_id"]),
            ) as resp:
                async for line in resp.aiter_lines():
                    collected_lines.append(line)
                    # Stop after we get an event with data
                    if line.startswith("data:"):
                        # Read one more empty line (event boundary) then stop
                        break

        # Start SSE listener in background
        sse_task = asyncio.create_task(read_sse())

        # Give SSE connection time to establish
        await asyncio.sleep(0.1)

        # Send a message from agent_a to agent_b
        await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], text="Hello via SSE",
        )

        # Wait for SSE to receive the event (with timeout)
        try:
            await asyncio.wait_for(sse_task, timeout=5.0)
        except asyncio.TimeoutError:
            sse_task.cancel()
            try:
                await sse_task
            except asyncio.CancelledError:
                pass

        # Parse the SSE events
        events = _parse_sse_events(collected_lines)

        # Find the message event
        message_events = [e for e in events if e.get("event") == "message"]
        assert len(message_events) >= 1, f"No message events found. Lines: {collected_lines}"

        event = message_events[0]

        # Verify event has expected fields
        assert "id" in event, "SSE event should have id field (task_id)"
        assert "data" in event, "SSE event should have data field (Task JSON)"

        # Verify data is valid A2A Task JSON
        task_data = json.loads(event["data"])
        assert "id" in task_data or "status" in task_data

    @pytest.mark.asyncio
    async def test_event_id_is_task_id(self, sse_env):
        """SSE event id field matches the delivered task's id."""
        client = sse_env["client"]
        agent_a = sse_env["agent_a"]
        agent_b = sse_env["agent_b"]
        api_key = sse_env["api_key"]

        collected_lines = []

        async def read_sse():
            async with client.stream(
                "GET",
                "/api/v1/subscribe",
                headers=_auth(api_key, agent_b["agent_id"]),
            ) as resp:
                async for line in resp.aiter_lines():
                    collected_lines.append(line)
                    if line.startswith("data:"):
                        break

        sse_task = asyncio.create_task(read_sse())
        await asyncio.sleep(0.1)

        result = await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], text="Task ID check",
        )

        try:
            await asyncio.wait_for(sse_task, timeout=5.0)
        except asyncio.TimeoutError:
            sse_task.cancel()
            try:
                await sse_task
            except asyncio.CancelledError:
                pass

        events = _parse_sse_events(collected_lines)
        message_events = [e for e in events if e.get("event") == "message"]
        assert len(message_events) >= 1

        event = message_events[0]
        task_json = json.loads(event["data"])

        # The event id should equal the task id in the data
        assert event["id"] == task_json["id"]

    @pytest.mark.asyncio
    async def test_event_data_is_full_task_json(self, sse_env):
        """SSE event data contains full A2A Task JSON with expected fields."""
        client = sse_env["client"]
        agent_a = sse_env["agent_a"]
        agent_b = sse_env["agent_b"]
        api_key = sse_env["api_key"]

        collected_lines = []

        async def read_sse():
            async with client.stream(
                "GET",
                "/api/v1/subscribe",
                headers=_auth(api_key, agent_b["agent_id"]),
            ) as resp:
                async for line in resp.aiter_lines():
                    collected_lines.append(line)
                    if line.startswith("data:"):
                        break

        sse_task = asyncio.create_task(read_sse())
        await asyncio.sleep(0.1)

        await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], text="Full Task check",
        )

        try:
            await asyncio.wait_for(sse_task, timeout=5.0)
        except asyncio.TimeoutError:
            sse_task.cancel()
            try:
                await sse_task
            except asyncio.CancelledError:
                pass

        events = _parse_sse_events(collected_lines)
        message_events = [e for e in events if e.get("event") == "message"]
        assert len(message_events) >= 1

        task_data = json.loads(message_events[0]["data"])

        # A2A Task should have id, status, and contextId (camelCase via alias)
        assert "id" in task_data
        assert "status" in task_data
        # contextId is the recipient's agent_id
        context_id_key = "contextId" if "contextId" in task_data else "context_id"
        assert task_data.get(context_id_key) == agent_b["agent_id"]

    @pytest.mark.asyncio
    async def test_multiple_messages_arrive_as_separate_events(self, sse_env):
        """Multiple messages produce multiple SSE events."""
        client = sse_env["client"]
        agent_a = sse_env["agent_a"]
        agent_b = sse_env["agent_b"]
        api_key = sse_env["api_key"]

        collected_lines = []
        data_count = 0

        async def read_sse():
            nonlocal data_count
            async with client.stream(
                "GET",
                "/api/v1/subscribe",
                headers=_auth(api_key, agent_b["agent_id"]),
            ) as resp:
                async for line in resp.aiter_lines():
                    collected_lines.append(line)
                    if line.startswith("data:"):
                        data_count += 1
                        if data_count >= 2:
                            break

        sse_task = asyncio.create_task(read_sse())
        await asyncio.sleep(0.1)

        # Send two messages
        await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], text="Message 1",
        )
        await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], text="Message 2",
        )

        try:
            await asyncio.wait_for(sse_task, timeout=5.0)
        except asyncio.TimeoutError:
            sse_task.cancel()
            try:
                await sse_task
            except asyncio.CancelledError:
                pass

        events = _parse_sse_events(collected_lines)
        message_events = [e for e in events if e.get("event") == "message"]
        assert len(message_events) >= 2, (
            f"Expected at least 2 message events, got {len(message_events)}"
        )


# ---------------------------------------------------------------------------
# Keepalive
# ---------------------------------------------------------------------------


class TestSSEKeepalive:
    """Tests for SSE keepalive comments."""

    @pytest.mark.asyncio
    async def test_keepalive_comment_sent_periodically(self, sse_env):
        """SSE stream sends ': keepalive' comment to prevent timeouts.

        Note: The actual interval is 30 seconds in production. For testing,
        we use a shorter wait and verify that keepalive comments eventually
        appear in the stream. If the implementation supports a configurable
        interval, tests can be faster.
        """
        client = sse_env["client"]
        agent_b = sse_env["agent_b"]
        api_key = sse_env["api_key"]

        collected_lines = []

        async def read_sse():
            async with client.stream(
                "GET",
                "/api/v1/subscribe",
                headers=_auth(api_key, agent_b["agent_id"]),
            ) as resp:
                async for line in resp.aiter_lines():
                    collected_lines.append(line)
                    # Check if we got a keepalive comment
                    if line.startswith(":"):
                        break

        sse_task = asyncio.create_task(read_sse())

        # Wait enough for at least one keepalive (may take up to 30s in prod;
        # if the implementation uses a short test interval this will be faster)
        try:
            await asyncio.wait_for(sse_task, timeout=35.0)
        except asyncio.TimeoutError:
            sse_task.cancel()
            try:
                await sse_task
            except asyncio.CancelledError:
                pass

        # Verify at least one keepalive comment was received
        keepalive_lines = [l for l in collected_lines if l.startswith(":")]
        assert len(keepalive_lines) >= 1, (
            f"No keepalive comments found. Lines: {collected_lines}"
        )
        assert any("keepalive" in l for l in keepalive_lines)


# ---------------------------------------------------------------------------
# Disconnect Cleanup
# ---------------------------------------------------------------------------


class TestSSEDisconnectCleanup:
    """Tests for SSE disconnect cleanup — Pub/Sub unsubscribe on disconnect."""

    @pytest.mark.asyncio
    async def test_disconnect_unsubscribes_from_pubsub(self, sse_env):
        """When SSE client disconnects, the PubSubManager subscription is cleaned up."""
        client = sse_env["client"]
        agent_b = sse_env["agent_b"]
        api_key = sse_env["api_key"]
        redis = sse_env["redis"]

        # Connect to SSE
        async with client.stream(
            "GET",
            "/api/v1/subscribe",
            headers=_auth(api_key, agent_b["agent_id"]),
        ) as resp:
            assert resp.status_code == 200
            # Give time for subscription to be established
            await asyncio.sleep(0.1)
            # Client disconnects here (context manager exits)
            await resp.aclose()

        # Give time for cleanup to happen
        await asyncio.sleep(0.1)

        # After disconnect, publishing to the channel should have no active
        # subscribers. We verify by checking that the subscription was
        # cleaned up — new messages should not accumulate anywhere.
        # The exact verification depends on implementation but the key
        # invariant is: no resource leak after disconnect.
        #
        # We can verify by checking Redis Pub/Sub channel has no subscribers
        pubsub_channels = await redis.pubsub_numsub(f"inbox:{agent_b['agent_id']}")
        # pubsub_numsub returns list of (channel, count) pairs
        if pubsub_channels:
            for channel, count in zip(pubsub_channels[::2], pubsub_channels[1::2]):
                if channel == f"inbox:{agent_b['agent_id']}":
                    assert int(count) == 0, (
                        f"Expected 0 subscribers after disconnect, got {count}"
                    )

    @pytest.mark.asyncio
    async def test_reconnect_after_disconnect_works(self, sse_env):
        """After disconnecting, a new SSE connection can be established."""
        client = sse_env["client"]
        agent_b = sse_env["agent_b"]
        api_key = sse_env["api_key"]

        # First connection
        async with client.stream(
            "GET",
            "/api/v1/subscribe",
            headers=_auth(api_key, agent_b["agent_id"]),
        ) as resp:
            assert resp.status_code == 200
            await resp.aclose()

        await asyncio.sleep(0.1)

        # Second connection should also work
        async with client.stream(
            "GET",
            "/api/v1/subscribe",
            headers=_auth(api_key, agent_b["agent_id"]),
        ) as resp:
            assert resp.status_code == 200
            content_type = resp.headers.get("content-type", "")
            assert "text/event-stream" in content_type
            await resp.aclose()
