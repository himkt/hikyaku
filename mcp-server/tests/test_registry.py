"""Tests for registry.py — RegistryForwarder for proxying requests to broker.

Covers: All forwarding methods (send, broadcast, ack, cancel, get_task,
register, agents, deregister). Verifies correct URLs, HTTP methods,
headers, and payloads are sent to the broker.

The RegistryForwarder uses httpx.AsyncClient to forward requests to the
hikyaku-registry broker at HIKYAKU_URL.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from hikyaku_mcp.registry import RegistryForwarder


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


BROKER_URL = "http://localhost:8000"
API_KEY = "hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
AGENT_ID = "550e8400-e29b-41d4-a716-446655440000"
OTHER_AGENT_ID = "660f9500-f30c-52e5-b827-557766551111"

SAMPLE_TASK_RESPONSE = {
    "jsonrpc": "2.0",
    "result": {
        "task": {
            "id": "task-001",
            "contextId": OTHER_AGENT_ID,
            "status": {"state": "input-required", "timestamp": "2026-03-28T12:00:00Z"},
        }
    },
    "id": "req-1",
}

SAMPLE_REGISTER_RESPONSE = {
    "agent_id": "new-agent-001",
    "api_key": "hky_newkey000000000000000000000000",
    "name": "New Agent",
    "registered_at": "2026-03-28T12:00:00Z",
}

SAMPLE_AGENTS_RESPONSE = {
    "agents": [
        {"agent_id": AGENT_ID, "name": "Agent A", "description": "Test"},
        {"agent_id": OTHER_AGENT_ID, "name": "Agent B", "description": "Test"},
    ]
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_httpx_client():
    """Provide a mock httpx.AsyncClient for RegistryForwarder."""
    client = AsyncMock()

    # Default mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = SAMPLE_TASK_RESPONSE
    mock_response.raise_for_status = MagicMock()
    client.post.return_value = mock_response
    client.get.return_value = mock_response
    client.delete.return_value = mock_response

    return client


@pytest.fixture
def forwarder(mock_httpx_client):
    """Provide a RegistryForwarder with mocked httpx client."""
    fwd = RegistryForwarder(
        broker_url=BROKER_URL,
        api_key=API_KEY,
        agent_id=AGENT_ID,
    )
    fwd._client = mock_httpx_client
    return fwd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict:
    """Expected auth headers for all requests."""
    return {
        "Authorization": f"Bearer {API_KEY}",
        "X-Agent-Id": AGENT_ID,
    }


# ---------------------------------------------------------------------------
# Send (unicast)
# ---------------------------------------------------------------------------


class TestForwardSend:
    """Tests for RegistryForwarder.send — unicast SendMessage."""

    @pytest.mark.asyncio
    async def test_send_posts_to_jsonrpc_endpoint(self, forwarder, mock_httpx_client):
        """send() posts to the root JSON-RPC endpoint."""
        await forwarder.send(to=OTHER_AGENT_ID, text="Hello")

        mock_httpx_client.post.assert_called_once()
        call_args = mock_httpx_client.post.call_args
        assert call_args[0][0] == f"{BROKER_URL}/"

    @pytest.mark.asyncio
    async def test_send_uses_send_message_method(self, forwarder, mock_httpx_client):
        """send() sends JSON-RPC method 'SendMessage'."""
        await forwarder.send(to=OTHER_AGENT_ID, text="Hello")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert body["method"] == "SendMessage"

    @pytest.mark.asyncio
    async def test_send_includes_destination_in_metadata(
        self, forwarder, mock_httpx_client
    ):
        """send() includes destination agent_id in message metadata."""
        await forwarder.send(to=OTHER_AGENT_ID, text="Hello")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        message = body["params"]["message"]
        assert message["metadata"]["destination"] == OTHER_AGENT_ID

    @pytest.mark.asyncio
    async def test_send_includes_text_in_parts(self, forwarder, mock_httpx_client):
        """send() includes text as a text part in message parts."""
        await forwarder.send(to=OTHER_AGENT_ID, text="Did the API schema change?")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        parts = body["params"]["message"]["parts"]
        assert any(p.get("text") == "Did the API schema change?" for p in parts)

    @pytest.mark.asyncio
    async def test_send_includes_auth_headers(self, forwarder, mock_httpx_client):
        """send() includes Authorization and X-Agent-Id headers."""
        await forwarder.send(to=OTHER_AGENT_ID, text="Hello")

        call_args = mock_httpx_client.post.call_args
        headers = call_args[1].get("headers", {})
        assert headers.get("Authorization") == f"Bearer {API_KEY}"
        assert headers.get("X-Agent-Id") == AGENT_ID


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


class TestForwardBroadcast:
    """Tests for RegistryForwarder.broadcast — SendMessage with destination '*'."""

    @pytest.mark.asyncio
    async def test_broadcast_uses_send_message_method(
        self, forwarder, mock_httpx_client
    ):
        """broadcast() sends JSON-RPC method 'SendMessage'."""
        await forwarder.broadcast(text="Build failed on main")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert body["method"] == "SendMessage"

    @pytest.mark.asyncio
    async def test_broadcast_sets_destination_to_star(
        self, forwarder, mock_httpx_client
    ):
        """broadcast() sets destination to '*' in message metadata."""
        await forwarder.broadcast(text="Build failed")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        message = body["params"]["message"]
        assert message["metadata"]["destination"] == "*"


# ---------------------------------------------------------------------------
# ACK (multi-turn)
# ---------------------------------------------------------------------------


class TestForwardAck:
    """Tests for RegistryForwarder.ack — SendMessage with taskId (multi-turn)."""

    @pytest.mark.asyncio
    async def test_ack_uses_send_message_method(self, forwarder, mock_httpx_client):
        """ack() sends JSON-RPC method 'SendMessage'."""
        await forwarder.ack(task_id="task-001")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert body["method"] == "SendMessage"

    @pytest.mark.asyncio
    async def test_ack_includes_task_id_in_message(self, forwarder, mock_httpx_client):
        """ack() includes taskId in the message for multi-turn."""
        await forwarder.ack(task_id="task-001")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        message = body["params"]["message"]
        assert message.get("taskId") == "task-001"


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


class TestForwardCancel:
    """Tests for RegistryForwarder.cancel — CancelTask JSON-RPC."""

    @pytest.mark.asyncio
    async def test_cancel_uses_cancel_task_method(self, forwarder, mock_httpx_client):
        """cancel() sends JSON-RPC method 'CancelTask'."""
        await forwarder.cancel(task_id="task-001")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert body["method"] == "CancelTask"

    @pytest.mark.asyncio
    async def test_cancel_includes_task_id_in_params(
        self, forwarder, mock_httpx_client
    ):
        """cancel() includes task id in params."""
        await forwarder.cancel(task_id="task-001")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert body["params"]["id"] == "task-001"


# ---------------------------------------------------------------------------
# GetTask
# ---------------------------------------------------------------------------


class TestForwardGetTask:
    """Tests for RegistryForwarder.get_task — GetTask JSON-RPC."""

    @pytest.mark.asyncio
    async def test_get_task_uses_get_task_method(self, forwarder, mock_httpx_client):
        """get_task() sends JSON-RPC method 'GetTask'."""
        await forwarder.get_task(task_id="task-001")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert body["method"] == "GetTask"

    @pytest.mark.asyncio
    async def test_get_task_includes_task_id_in_params(
        self, forwarder, mock_httpx_client
    ):
        """get_task() includes task id in params."""
        await forwarder.get_task(task_id="task-001")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert body["params"]["id"] == "task-001"


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


class TestForwardRegister:
    """Tests for RegistryForwarder.register — POST /api/v1/agents."""

    @pytest.mark.asyncio
    async def test_register_posts_to_agents_endpoint(
        self, forwarder, mock_httpx_client
    ):
        """register() posts to /api/v1/agents."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = SAMPLE_REGISTER_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        await forwarder.register(name="New Agent", description="Test agent")

        call_args = mock_httpx_client.post.call_args
        assert call_args[0][0] == f"{BROKER_URL}/api/v1/agents"

    @pytest.mark.asyncio
    async def test_register_sends_name_and_description(
        self, forwarder, mock_httpx_client
    ):
        """register() sends name and description in request body."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = SAMPLE_REGISTER_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        await forwarder.register(name="New Agent", description="Test agent")

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert body["name"] == "New Agent"
        assert body["description"] == "Test agent"

    @pytest.mark.asyncio
    async def test_register_with_api_key_sends_auth_header(
        self, forwarder, mock_httpx_client
    ):
        """register() with api_key sends Authorization header to join tenant."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = SAMPLE_REGISTER_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        join_key = "hky_joinkey00000000000000000000000"
        await forwarder.register(
            name="Joining Agent", description="Joining", api_key=join_key
        )

        call_args = mock_httpx_client.post.call_args
        headers = call_args[1].get("headers", {})
        assert headers.get("Authorization") == f"Bearer {join_key}"

    @pytest.mark.asyncio
    async def test_register_with_skills(self, forwarder, mock_httpx_client):
        """register() with skills includes them in request body."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = SAMPLE_REGISTER_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        skills_json = '[{"name": "code-review"}]'
        await forwarder.register(
            name="Skilled Agent", description="Has skills", skills=skills_json
        )

        call_args = mock_httpx_client.post.call_args
        body = call_args[1].get("json") or call_args[0][1]
        assert "skills" in body


# ---------------------------------------------------------------------------
# Agents (list / detail)
# ---------------------------------------------------------------------------


class TestForwardAgents:
    """Tests for RegistryForwarder.agents — GET /api/v1/agents."""

    @pytest.mark.asyncio
    async def test_agents_list_gets_agents_endpoint(self, forwarder, mock_httpx_client):
        """agents() without id calls GET /api/v1/agents."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_AGENTS_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.get.return_value = mock_response

        await forwarder.agents()

        mock_httpx_client.get.assert_called_once()
        call_args = mock_httpx_client.get.call_args
        assert call_args[0][0] == f"{BROKER_URL}/api/v1/agents"

    @pytest.mark.asyncio
    async def test_agents_detail_gets_specific_agent(
        self, forwarder, mock_httpx_client
    ):
        """agents(id=...) calls GET /api/v1/agents/{id}."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "agent_id": OTHER_AGENT_ID,
            "name": "Agent B",
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.get.return_value = mock_response

        await forwarder.agents(id=OTHER_AGENT_ID)

        call_args = mock_httpx_client.get.call_args
        assert call_args[0][0] == f"{BROKER_URL}/api/v1/agents/{OTHER_AGENT_ID}"

    @pytest.mark.asyncio
    async def test_agents_includes_auth_headers(self, forwarder, mock_httpx_client):
        """agents() includes Authorization and X-Agent-Id headers."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_AGENTS_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.get.return_value = mock_response

        await forwarder.agents()

        call_args = mock_httpx_client.get.call_args
        headers = call_args[1].get("headers", {})
        assert headers.get("Authorization") == f"Bearer {API_KEY}"
        assert headers.get("X-Agent-Id") == AGENT_ID


# ---------------------------------------------------------------------------
# Deregister
# ---------------------------------------------------------------------------


class TestForwardDeregister:
    """Tests for RegistryForwarder.deregister — DELETE /api/v1/agents/{id}."""

    @pytest.mark.asyncio
    async def test_deregister_sends_delete_request(self, forwarder, mock_httpx_client):
        """deregister() sends DELETE to /api/v1/agents/{agent_id}."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "deregistered"}
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.delete.return_value = mock_response

        await forwarder.deregister()

        mock_httpx_client.delete.assert_called_once()
        call_args = mock_httpx_client.delete.call_args
        assert call_args[0][0] == f"{BROKER_URL}/api/v1/agents/{AGENT_ID}"

    @pytest.mark.asyncio
    async def test_deregister_includes_auth_headers(self, forwarder, mock_httpx_client):
        """deregister() includes Authorization and X-Agent-Id headers."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "deregistered"}
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.delete.return_value = mock_response

        await forwarder.deregister()

        call_args = mock_httpx_client.delete.call_args
        headers = call_args[1].get("headers", {})
        assert headers.get("Authorization") == f"Bearer {API_KEY}"
        assert headers.get("X-Agent-Id") == AGENT_ID
