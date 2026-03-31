"""Tests for server.py — MCP server tool definitions and behavior.

Covers: poll tool (drain buffer, page_size, since filter, empty buffer),
and forwarding tools (send, broadcast, ack, cancel, get_task, agents,
register, deregister) calling RegistryForwarder correctly.

The MCP server is a transparent proxy: poll reads from the SSEClient buffer,
all other tools forward to the registry via RegistryForwarder.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hikyaku_mcp.server import (
    _build_server,
    handle_poll,
    handle_send,
    handle_broadcast,
    handle_ack,
    handle_cancel,
    handle_get_task,
    handle_agents,
    handle_register,
    handle_deregister,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


AGENT_ID = "550e8400-e29b-41d4-a716-446655440000"
OTHER_AGENT_ID = "660f9500-f30c-52e5-b827-557766551111"


def _make_sample_task(task_id: str, timestamp: str = "2026-03-28T12:00:00Z") -> dict:
    """Create a sample A2A Task dict."""
    return {
        "id": task_id,
        "contextId": AGENT_ID,
        "status": {"state": "input-required", "timestamp": timestamp},
        "artifacts": [{"parts": [{"type": "text", "text": f"Message {task_id}"}]}],
        "metadata": {
            "fromAgentId": OTHER_AGENT_ID,
            "toAgentId": AGENT_ID,
            "type": "unicast",
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_sse_client():
    """Provide a mock SSEClient with controllable drain behavior."""
    client = MagicMock()
    client.drain = MagicMock(return_value=[])
    return client


@pytest.fixture
def mock_forwarder():
    """Provide a mock RegistryForwarder with all async methods."""
    fwd = AsyncMock()
    fwd.send = AsyncMock(return_value={"task": _make_sample_task("task-sent")})
    fwd.broadcast = AsyncMock(return_value={"task": _make_sample_task("task-bcast")})
    fwd.ack = AsyncMock(return_value={"task": _make_sample_task("task-acked")})
    fwd.cancel = AsyncMock(return_value={"task": _make_sample_task("task-canceled")})
    fwd.get_task = AsyncMock(return_value={"task": _make_sample_task("task-001")})
    fwd.agents = AsyncMock(return_value={"agents": []})
    fwd.register = AsyncMock(
        return_value={"agent_id": "new-agent", "api_key": "hky_new"}
    )
    fwd.deregister = AsyncMock(return_value={"status": "deregistered"})
    return fwd


# ---------------------------------------------------------------------------
# Poll Tool — Buffer Drain
# ---------------------------------------------------------------------------


class TestPollTool:
    """Tests for poll MCP tool — drains SSEClient buffer."""

    @pytest.mark.asyncio
    async def test_poll_returns_empty_list_when_buffer_empty(self, mock_sse_client):
        """poll returns [] when SSEClient buffer is empty."""
        mock_sse_client.drain.return_value = []

        result = await handle_poll(sse_client=mock_sse_client)

        assert result == []

    @pytest.mark.asyncio
    async def test_poll_returns_buffered_messages(self, mock_sse_client):
        """poll returns all buffered messages from SSEClient."""
        tasks = [_make_sample_task(f"task-{i}") for i in range(3)]
        mock_sse_client.drain.return_value = tasks

        result = await handle_poll(sse_client=mock_sse_client)

        assert len(result) == 3
        assert result[0]["id"] == "task-0"
        assert result[1]["id"] == "task-1"
        assert result[2]["id"] == "task-2"

    @pytest.mark.asyncio
    async def test_poll_calls_drain(self, mock_sse_client):
        """poll calls SSEClient.drain() to retrieve messages."""
        await handle_poll(sse_client=mock_sse_client)

        mock_sse_client.drain.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_with_page_size_passes_max_items(self, mock_sse_client):
        """poll with page_size passes max_items to SSEClient.drain()."""
        mock_sse_client.drain.return_value = [_make_sample_task("task-0")]

        await handle_poll(sse_client=mock_sse_client, page_size=5)

        mock_sse_client.drain.assert_called_once_with(max_items=5)

    @pytest.mark.asyncio
    async def test_poll_without_page_size_drains_all(self, mock_sse_client):
        """poll without page_size drains all messages (no max_items)."""
        await handle_poll(sse_client=mock_sse_client)

        mock_sse_client.drain.assert_called_once()
        call_args = mock_sse_client.drain.call_args
        # Either called with no args or max_items=None
        if call_args[1]:
            assert call_args[1].get("max_items") is None
        # else called with no kwargs = drain all

    @pytest.mark.asyncio
    async def test_poll_with_since_filters_by_timestamp(self, mock_sse_client):
        """poll with since parameter filters tasks by timestamp."""
        old_task = _make_sample_task("task-old", timestamp="2026-03-27T00:00:00Z")
        new_task = _make_sample_task("task-new", timestamp="2026-03-29T00:00:00Z")
        mock_sse_client.drain.return_value = [old_task, new_task]

        result = await handle_poll(
            sse_client=mock_sse_client,
            since="2026-03-28T00:00:00Z",
        )

        # Only the task after the since timestamp should be returned
        assert len(result) == 1
        assert result[0]["id"] == "task-new"

    @pytest.mark.asyncio
    async def test_poll_with_since_returns_empty_when_all_older(self, mock_sse_client):
        """poll with since returns [] when all tasks are older."""
        old_task = _make_sample_task("task-old", timestamp="2026-03-27T00:00:00Z")
        mock_sse_client.drain.return_value = [old_task]

        result = await handle_poll(
            sse_client=mock_sse_client,
            since="2026-03-28T00:00:00Z",
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_poll_with_page_size_and_since_combined(self, mock_sse_client):
        """poll with both page_size and since applies both filters."""
        tasks = [
            _make_sample_task("old-1", timestamp="2026-03-27T00:00:00Z"),
            _make_sample_task("new-1", timestamp="2026-03-29T01:00:00Z"),
            _make_sample_task("new-2", timestamp="2026-03-29T02:00:00Z"),
            _make_sample_task("new-3", timestamp="2026-03-29T03:00:00Z"),
        ]
        mock_sse_client.drain.return_value = tasks

        result = await handle_poll(
            sse_client=mock_sse_client,
            since="2026-03-28T00:00:00Z",
            page_size=2,
        )

        # Filtered by since (3 tasks), then limited by page_size (2)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Send Tool — Forwarding
# ---------------------------------------------------------------------------


class TestSendTool:
    """Tests for send MCP tool — forwards to RegistryForwarder."""

    @pytest.mark.asyncio
    async def test_send_calls_forwarder_send(self, mock_forwarder):
        """send tool calls RegistryForwarder.send with correct params."""
        await handle_send(
            forwarder=mock_forwarder,
            to=OTHER_AGENT_ID,
            text="Hello Agent B",
        )

        mock_forwarder.send.assert_called_once_with(
            to=OTHER_AGENT_ID, text="Hello Agent B"
        )

    @pytest.mark.asyncio
    async def test_send_returns_forwarder_result(self, mock_forwarder):
        """send tool returns the result from RegistryForwarder."""
        result = await handle_send(
            forwarder=mock_forwarder,
            to=OTHER_AGENT_ID,
            text="Hello",
        )

        assert "task" in result


# ---------------------------------------------------------------------------
# Broadcast Tool — Forwarding
# ---------------------------------------------------------------------------


class TestBroadcastTool:
    """Tests for broadcast MCP tool — forwards to RegistryForwarder."""

    @pytest.mark.asyncio
    async def test_broadcast_calls_forwarder_broadcast(self, mock_forwarder):
        """broadcast tool calls RegistryForwarder.broadcast."""
        await handle_broadcast(forwarder=mock_forwarder, text="Alert everyone")

        mock_forwarder.broadcast.assert_called_once_with(text="Alert everyone")

    @pytest.mark.asyncio
    async def test_broadcast_returns_forwarder_result(self, mock_forwarder):
        """broadcast tool returns the result from RegistryForwarder."""
        result = await handle_broadcast(forwarder=mock_forwarder, text="Alert")

        assert "task" in result


# ---------------------------------------------------------------------------
# Ack Tool — Forwarding
# ---------------------------------------------------------------------------


class TestAckTool:
    """Tests for ack MCP tool — forwards to RegistryForwarder."""

    @pytest.mark.asyncio
    async def test_ack_calls_forwarder_ack(self, mock_forwarder):
        """ack tool calls RegistryForwarder.ack with task_id."""
        await handle_ack(forwarder=mock_forwarder, task_id="task-001")

        mock_forwarder.ack.assert_called_once_with(task_id="task-001")


# ---------------------------------------------------------------------------
# Cancel Tool — Forwarding
# ---------------------------------------------------------------------------


class TestCancelTool:
    """Tests for cancel MCP tool — forwards to RegistryForwarder."""

    @pytest.mark.asyncio
    async def test_cancel_calls_forwarder_cancel(self, mock_forwarder):
        """cancel tool calls RegistryForwarder.cancel with task_id."""
        await handle_cancel(forwarder=mock_forwarder, task_id="task-001")

        mock_forwarder.cancel.assert_called_once_with(task_id="task-001")


# ---------------------------------------------------------------------------
# GetTask Tool — Forwarding
# ---------------------------------------------------------------------------


class TestGetTaskTool:
    """Tests for get_task MCP tool — forwards to RegistryForwarder."""

    @pytest.mark.asyncio
    async def test_get_task_calls_forwarder_get_task(self, mock_forwarder):
        """get_task tool calls RegistryForwarder.get_task with task_id."""
        await handle_get_task(forwarder=mock_forwarder, task_id="task-001")

        mock_forwarder.get_task.assert_called_once_with(task_id="task-001")


# ---------------------------------------------------------------------------
# Agents Tool — Forwarding
# ---------------------------------------------------------------------------


class TestAgentsTool:
    """Tests for agents MCP tool — forwards to RegistryForwarder."""

    @pytest.mark.asyncio
    async def test_agents_list_calls_forwarder_agents(self, mock_forwarder):
        """agents tool without id calls RegistryForwarder.agents()."""
        await handle_agents(forwarder=mock_forwarder)

        mock_forwarder.agents.assert_called_once_with(id=None)

    @pytest.mark.asyncio
    async def test_agents_detail_calls_forwarder_with_id(self, mock_forwarder):
        """agents tool with id calls RegistryForwarder.agents(id=...)."""
        await handle_agents(forwarder=mock_forwarder, id=OTHER_AGENT_ID)

        mock_forwarder.agents.assert_called_once_with(id=OTHER_AGENT_ID)


# ---------------------------------------------------------------------------
# Register Tool — Forwarding
# ---------------------------------------------------------------------------


class TestRegisterTool:
    """Tests for register MCP tool — forwards to RegistryForwarder."""

    @pytest.mark.asyncio
    async def test_register_calls_forwarder_register(self, mock_forwarder):
        """register tool calls RegistryForwarder.register without api_key."""
        await handle_register(
            forwarder=mock_forwarder,
            name="New Agent",
            description="Test agent",
        )

        mock_forwarder.register.assert_called_once()
        call_kwargs = mock_forwarder.register.call_args[1]
        assert call_kwargs["name"] == "New Agent"
        assert call_kwargs["description"] == "Test agent"
        assert "api_key" not in call_kwargs

    @pytest.mark.asyncio
    async def test_register_with_optional_skills(self, mock_forwarder):
        """register tool passes optional skills (api_key is not accepted)."""
        await handle_register(
            forwarder=mock_forwarder,
            name="Skilled Agent",
            description="Has skills",
            skills='[{"name": "code-review"}]',
        )

        call_kwargs = mock_forwarder.register.call_args[1]
        assert call_kwargs["skills"] == '[{"name": "code-review"}]'
        assert "api_key" not in call_kwargs

    @pytest.mark.asyncio
    async def test_register_does_not_accept_api_key(self, mock_forwarder):
        """handle_register does not accept api_key parameter."""
        with pytest.raises(TypeError):
            await handle_register(
                forwarder=mock_forwarder,
                name="Agent",
                description="Test",
                api_key="hky_test",
            )

    @pytest.mark.asyncio
    async def test_register_output_includes_api_key_for_call_tool(self, mock_forwarder):
        """handle_register returns agent_id and api_key so call_tool can later strip api_key."""
        mock_forwarder.register = AsyncMock(
            return_value={"agent_id": "new-agent", "api_key": "hky_secret", "name": "test"}
        )
        result = await handle_register(
            forwarder=mock_forwarder,
            name="Agent",
            description="Test",
        )
        assert "agent_id" in result
        assert result["agent_id"] == "new-agent"


# ---------------------------------------------------------------------------
# Deregister Tool — Forwarding
# ---------------------------------------------------------------------------


class TestDeregisterTool:
    """Tests for deregister MCP tool — forwards to RegistryForwarder."""

    @pytest.mark.asyncio
    async def test_deregister_calls_forwarder_deregister(self, mock_forwarder):
        """deregister tool calls RegistryForwarder.deregister."""
        await handle_deregister(forwarder=mock_forwarder)

        mock_forwarder.deregister.assert_called_once()


# ---------------------------------------------------------------------------
# call_tool — register output stripping
# ---------------------------------------------------------------------------


class TestCallToolRegisterOutputStripping:
    """Tests for call_tool stripping api_key from register tool output."""

    @pytest.mark.asyncio
    async def test_call_tool_register_strips_api_key(self):
        """call_tool strips api_key from register result in JSON output."""
        mock_fwd = AsyncMock()
        mock_fwd.register = AsyncMock(
            return_value={"agent_id": "new-agent", "api_key": "hky_secret", "name": "test"}
        )

        with patch("hikyaku_mcp.server.get_config") as mock_config, \
             patch("hikyaku_mcp.server.SSEClient") as mock_sse_cls, \
             patch("hikyaku_mcp.server.RegistryForwarder") as mock_fwd_cls:
            mock_config.return_value = {
                "broker_url": "http://localhost:8000",
                "api_key": "hky_test",
                "agent_id": "agent-1",
            }
            mock_sse_cls.return_value = MagicMock()
            mock_fwd_cls.return_value = mock_fwd

            server, _, _ = _build_server()

            from mcp.types import CallToolRequest, CallToolRequestParams

            handler = server.request_handlers.get(CallToolRequest)
            assert handler is not None

            request = CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="register",
                    arguments={"name": "Agent", "description": "Test"},
                ),
            )
            result = await handler(request)

            # ServerResult wraps CallToolResult in .root
            output_text = result.root.content[0].text
            parsed = json.loads(output_text)
            assert "agent_id" in parsed
            assert parsed["agent_id"] == "new-agent"
            assert "name" in parsed
            assert "api_key" not in parsed
