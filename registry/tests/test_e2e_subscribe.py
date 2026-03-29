"""E2E integration tests for streaming subscribe feature.

Tests the full integration chain:
1. Send message via JSON-RPC → executor publishes to Redis Pub/Sub →
   event_generator receives and yields SSE event
2. Existing API operations (send, list, get, ack, cancel) still work
   with the pubsub-enabled executor

Uses fakeredis + ASGI transport (same as existing tests).

Success criteria from design doc:
- Server pushes new messages via SSE within 1 second of delivery
- Existing API continues to work independently
- No messages are lost
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest
import fakeredis.aioredis
from httpx import AsyncClient, ASGITransport
from a2a.types import Task

from hikyaku_registry.main import create_app
from hikyaku_registry.pubsub import PubSubManager
from hikyaku_registry.task_store import RedisTaskStore
from hikyaku_registry.api.subscribe import event_generator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def env():
    """Full ASGI app with fakeredis, two registered agents in the same tenant.

    Also exposes pubsub and task_store for direct integration verification.
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app = create_app(redis=redis)
    transport = ASGITransport(app=app)
    pubsub = PubSubManager(redis)
    task_store = RedisTaskStore(redis)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register agent A (creates tenant)
        resp_a = await client.post(
            "/api/v1/agents",
            json={"name": "AgentA", "description": "Sender"},
        )
        assert resp_a.status_code == 201
        agent_a = resp_a.json()

        # Register agent B (joins tenant)
        resp_b = await client.post(
            "/api/v1/agents",
            json={"name": "AgentB", "description": "Receiver"},
            headers={"Authorization": f"Bearer {agent_a['api_key']}"},
        )
        assert resp_b.status_code == 201
        agent_b = resp_b.json()

        yield {
            "client": client,
            "redis": redis,
            "pubsub": pubsub,
            "task_store": task_store,
            "api_key": agent_a["api_key"],
            "agent_a": agent_a,
            "agent_b": agent_b,
        }

    await redis.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(api_key: str, agent_id: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Agent-Id": agent_id,
    }


async def _send_message(client, api_key, from_id, to_id, text):
    """Send a unicast message via JSON-RPC SendMessage."""
    body = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
                "metadata": {"destination": to_id},
            }
        },
        "id": str(uuid.uuid4()),
    }
    resp = await client.post(
        "/", json=body, headers=_auth(api_key, from_id)
    )
    resp.raise_for_status()
    data = resp.json()
    assert "result" in data, f"Expected 'result' in response, got: {data}"
    return data


async def _broadcast_message(client, api_key, from_id, text):
    """Broadcast a message via JSON-RPC SendMessage with destination '*'."""
    body = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
                "metadata": {"destination": "*"},
            }
        },
        "id": str(uuid.uuid4()),
    }
    resp = await client.post(
        "/", json=body, headers=_auth(api_key, from_id)
    )
    resp.raise_for_status()
    data = resp.json()
    assert "result" in data, f"Expected 'result' in response, got: {data}"
    return data


def _parse_sse_events(raw_chunks: list[str]) -> list[dict]:
    """Parse raw SSE output into list of event dicts."""
    events = []
    current = {}
    lines = []
    for chunk in raw_chunks:
        lines.extend(chunk.split("\n"))

    for line in lines:
        line = line.rstrip("\r")
        if line == "":
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith(":"):
            current["comment"] = line[1:].strip()
            events.append(current)
            current = {}
            continue
        if ":" in line:
            field, _, value = line.partition(":")
            value = value.lstrip(" ")
            current[field] = value

    if current:
        events.append(current)
    return events


# ---------------------------------------------------------------------------
# E2E: Executor publishes to Pub/Sub on message delivery
# ---------------------------------------------------------------------------


class TestE2EExecutorPublishIntegration:
    """Verify that sending a message via JSON-RPC triggers Pub/Sub publish,
    and the SSE event_generator picks up the message end-to-end."""

    @pytest.mark.asyncio
    async def test_unicast_triggers_pubsub_and_sse_receives(self, env):
        """Send via API → executor publishes → event_generator yields Task event."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]
        pubsub = env["pubsub"]
        task_store = env["task_store"]

        # Start event_generator for agent B
        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)
        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_b["agent_id"],
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)

        # Send message via JSON-RPC (this goes through BrokerExecutor)
        result = await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], "E2E pubsub test"
        )
        task_id = result["result"]["task"]["id"]

        # Wait for SSE generator to receive the message
        await asyncio.wait_for(consumer_task, timeout=5.0)

        events = _parse_sse_events(collected)
        msg_events = [e for e in events if e.get("event") == "message"]
        assert len(msg_events) >= 1

        # Verify the SSE event matches the sent task
        task_data = json.loads(msg_events[0]["data"])
        assert task_data["id"] == task_id
        assert msg_events[0]["id"] == task_id

    @pytest.mark.asyncio
    async def test_sse_event_has_full_task_json(self, env):
        """SSE event data contains complete A2A Task JSON."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]
        pubsub = env["pubsub"]
        task_store = env["task_store"]

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)
        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_b["agent_id"],
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)

        await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], "Full JSON check"
        )

        await asyncio.wait_for(consumer_task, timeout=5.0)

        events = _parse_sse_events(collected)
        msg_events = [e for e in events if e.get("event") == "message"]
        task_data = json.loads(msg_events[0]["data"])

        # Verify A2A Task fields
        assert "id" in task_data
        assert "status" in task_data
        assert task_data["status"]["state"] == "input-required"
        assert "artifacts" in task_data
        assert len(task_data["artifacts"]) >= 1

        # Verify text content
        parts = task_data["artifacts"][0]["parts"]
        texts = [p["text"] for p in parts if "text" in p]
        assert "Full JSON check" in texts

    @pytest.mark.asyncio
    async def test_sse_event_has_correct_metadata(self, env):
        """SSE event metadata has fromAgentId and toAgentId."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]
        pubsub = env["pubsub"]
        task_store = env["task_store"]

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)
        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_b["agent_id"],
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)

        await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], "Metadata check"
        )

        await asyncio.wait_for(consumer_task, timeout=5.0)

        events = _parse_sse_events(collected)
        msg_events = [e for e in events if e.get("event") == "message"]
        task_data = json.loads(msg_events[0]["data"])

        assert task_data["metadata"]["fromAgentId"] == agent_a["agent_id"]
        assert task_data["metadata"]["toAgentId"] == agent_b["agent_id"]
        assert task_data["metadata"]["type"] == "unicast"

    @pytest.mark.asyncio
    async def test_multiple_messages_arrive_via_sse(self, env):
        """Multiple messages sent sequentially all arrive via SSE."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]
        pubsub = env["pubsub"]
        task_store = env["task_store"]

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)
        collected = []
        msg_count = 0

        async def consume():
            nonlocal msg_count
            async for chunk in event_generator(
                agent_id=agent_b["agent_id"],
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    msg_count += 1
                    if msg_count >= 3:
                        break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)

        sent_ids = []
        for text in ["msg-1", "msg-2", "msg-3"]:
            result = await _send_message(
                client, api_key, agent_a["agent_id"],
                agent_b["agent_id"], text
            )
            sent_ids.append(result["result"]["task"]["id"])

        await asyncio.wait_for(consumer_task, timeout=5.0)

        events = _parse_sse_events(collected)
        msg_events = [e for e in events if e.get("event") == "message"]
        assert len(msg_events) >= 3

        received_ids = [json.loads(e["data"])["id"] for e in msg_events]
        assert received_ids == sent_ids

    @pytest.mark.asyncio
    async def test_broadcast_triggers_pubsub_for_recipient(self, env):
        """Broadcast via API triggers Pub/Sub for each recipient."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]
        pubsub = env["pubsub"]
        task_store = env["task_store"]

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)
        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_b["agent_id"],
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)

        await _broadcast_message(
            client, api_key, agent_a["agent_id"], "Broadcast E2E"
        )

        await asyncio.wait_for(consumer_task, timeout=5.0)

        events = _parse_sse_events(collected)
        msg_events = [e for e in events if e.get("event") == "message"]
        assert len(msg_events) >= 1

        task_data = json.loads(msg_events[0]["data"])
        parts = task_data["artifacts"][0]["parts"]
        assert parts[0]["text"] == "Broadcast E2E"


# ---------------------------------------------------------------------------
# E2E: Existing API independence
# ---------------------------------------------------------------------------


class TestE2EExistingAPIWorks:
    """Existing API operations still work independently with pubsub-enabled executor."""

    @pytest.mark.asyncio
    async def test_send_and_list_tasks(self, env):
        """Send → ListTasks flow works."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]

        result = await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], "List test"
        )
        task_id = result["result"]["task"]["id"]

        list_body = {
            "jsonrpc": "2.0",
            "method": "ListTasks",
            "params": {"contextId": agent_b["agent_id"]},
            "id": str(uuid.uuid4()),
        }
        list_resp = await client.post(
            "/", json=list_body,
            headers=_auth(api_key, agent_b["agent_id"]),
        )
        list_resp.raise_for_status()
        tasks = list_resp.json()["result"]["tasks"]
        assert any(t["id"] == task_id for t in tasks)

    @pytest.mark.asyncio
    async def test_get_task(self, env):
        """GetTask works."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]

        result = await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], "GetTask test"
        )
        task_id = result["result"]["task"]["id"]

        get_body = {
            "jsonrpc": "2.0",
            "method": "GetTask",
            "params": {"id": task_id},
            "id": str(uuid.uuid4()),
        }
        get_resp = await client.post(
            "/", json=get_body,
            headers=_auth(api_key, agent_b["agent_id"]),
        )
        get_resp.raise_for_status()
        assert get_resp.json()["result"]["task"]["id"] == task_id

    @pytest.mark.asyncio
    async def test_ack(self, env):
        """ACK flow works."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]

        result = await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], "ACK test"
        )
        task_id = result["result"]["task"]["id"]

        ack_body = {
            "jsonrpc": "2.0",
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "user",
                    "taskId": task_id,
                    "parts": [{"kind": "text", "text": "ack"}],
                }
            },
            "id": str(uuid.uuid4()),
        }
        ack_resp = await client.post(
            "/", json=ack_body,
            headers=_auth(api_key, agent_b["agent_id"]),
        )
        ack_resp.raise_for_status()
        state = ack_resp.json()["result"]["task"]["status"]["state"]
        assert state == "completed"

    @pytest.mark.asyncio
    async def test_cancel(self, env):
        """CancelTask works."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]

        result = await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], "Cancel test"
        )
        task_id = result["result"]["task"]["id"]

        cancel_body = {
            "jsonrpc": "2.0",
            "method": "CancelTask",
            "params": {"id": task_id},
            "id": str(uuid.uuid4()),
        }
        cancel_resp = await client.post(
            "/", json=cancel_body,
            headers=_auth(api_key, agent_a["agent_id"]),
        )
        cancel_resp.raise_for_status()
        state = cancel_resp.json()["result"]["task"]["status"]["state"]
        assert state == "canceled"

    @pytest.mark.asyncio
    async def test_agent_card(self, env):
        """Agent card endpoint still works."""
        client = env["client"]
        resp = await client.get("/.well-known/agent-card.json")
        resp.raise_for_status()
        assert "name" in resp.json()


# ---------------------------------------------------------------------------
# E2E: SSE + polling coexistence
# ---------------------------------------------------------------------------


class TestE2ESSEAndPollingCoexist:
    """Message is available via both SSE and traditional polling."""

    @pytest.mark.asyncio
    async def test_message_visible_via_sse_and_list_tasks(self, env):
        """Same message is accessible via SSE event AND ListTasks."""
        client = env["client"]
        api_key = env["api_key"]
        agent_a = env["agent_a"]
        agent_b = env["agent_b"]
        pubsub = env["pubsub"]
        task_store = env["task_store"]

        request = AsyncMock()
        request.is_disconnected = AsyncMock(return_value=False)
        collected = []

        async def consume():
            async for chunk in event_generator(
                agent_id=agent_b["agent_id"],
                pubsub=pubsub,
                task_store=task_store,
                request=request,
            ):
                collected.append(chunk)
                if "data:" in chunk:
                    break

        consumer_task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)

        result = await _send_message(
            client, api_key, agent_a["agent_id"],
            agent_b["agent_id"], "Both SSE and poll"
        )
        task_id = result["result"]["task"]["id"]

        await asyncio.wait_for(consumer_task, timeout=5.0)

        # Verify via SSE
        events = _parse_sse_events(collected)
        msg_events = [e for e in events if e.get("event") == "message"]
        assert len(msg_events) >= 1
        sse_task_id = json.loads(msg_events[0]["data"])["id"]
        assert sse_task_id == task_id

        # Verify via ListTasks (polling)
        list_body = {
            "jsonrpc": "2.0",
            "method": "ListTasks",
            "params": {"contextId": agent_b["agent_id"]},
            "id": str(uuid.uuid4()),
        }
        list_resp = await client.post(
            "/", json=list_body,
            headers=_auth(api_key, agent_b["agent_id"]),
        )
        list_resp.raise_for_status()
        tasks = list_resp.json()["result"]["tasks"]
        assert any(t["id"] == task_id for t in tasks)
