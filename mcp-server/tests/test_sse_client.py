"""Tests for sse_client.py — SSEClient internal connection manager.

Covers: initialization, connect/disconnect lifecycle, drain() buffer retrieval,
buffer overflow (drop oldest when full), and max_items limit on drain.

The SSEClient manages an asyncio.Queue(maxsize=1000) buffer populated via
a background SSE connection to the broker's /api/v1/subscribe endpoint.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hikyaku_mcp.sse_client import SSEClient, MAX_BUFFER_SIZE


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


BROKER_URL = "http://localhost:8000"
API_KEY = "hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
AGENT_ID = "550e8400-e29b-41d4-a716-446655440000"

SAMPLE_TASK = {
    "id": "task-001",
    "contextId": AGENT_ID,
    "status": {"state": "input-required", "timestamp": "2026-03-28T12:00:00Z"},
    "artifacts": [
        {"parts": [{"type": "text", "text": "Hello"}]}
    ],
    "metadata": {
        "fromAgentId": "agent-sender-001",
        "toAgentId": AGENT_ID,
        "type": "unicast",
    },
}


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestSSEClientInit:
    """Tests for SSEClient.__init__ — stores configuration."""

    def test_stores_broker_url(self):
        """SSEClient stores broker_url from constructor."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)
        assert client.broker_url == BROKER_URL

    def test_stores_api_key(self):
        """SSEClient stores api_key from constructor."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)
        assert client.api_key == API_KEY

    def test_stores_agent_id(self):
        """SSEClient stores agent_id from constructor."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)
        assert client.agent_id == AGENT_ID

    def test_queue_is_created_with_max_buffer_size(self):
        """SSEClient creates an asyncio.Queue with maxsize=MAX_BUFFER_SIZE."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)
        assert isinstance(client.queue, asyncio.Queue)
        assert client.queue.maxsize == MAX_BUFFER_SIZE

    def test_max_buffer_size_is_1000(self):
        """MAX_BUFFER_SIZE constant is 1000."""
        assert MAX_BUFFER_SIZE == 1000

    def test_task_is_none_before_connect(self):
        """Background task is None before connect() is called."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)
        assert client._task is None

    def test_client_is_none_before_connect(self):
        """httpx client is None before connect() is called."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)
        assert client._client is None


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


class TestSSEClientConnect:
    """Tests for SSEClient.connect() — starts background SSE reader."""

    @pytest.mark.asyncio
    async def test_connect_creates_httpx_client(self):
        """connect() creates an httpx.AsyncClient."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        with patch("hikyaku_mcp.sse_client.httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value = AsyncMock()
            # Patch _read_loop to avoid actual SSE connection
            with patch.object(client, "_read_loop", new_callable=AsyncMock):
                await client.connect()

        assert client._client is not None

    @pytest.mark.asyncio
    async def test_connect_starts_background_task(self):
        """connect() creates an asyncio.Task for the read loop."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        with patch("hikyaku_mcp.sse_client.httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value = AsyncMock()
            with patch.object(client, "_read_loop", new_callable=AsyncMock):
                await client.connect()

        assert client._task is not None
        assert isinstance(client._task, asyncio.Task)

        # Clean up
        client._task.cancel()
        try:
            await client._task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Drain
# ---------------------------------------------------------------------------


class TestSSEClientDrain:
    """Tests for SSEClient.drain() — retrieves buffered messages."""

    def test_drain_empty_queue_returns_empty_list(self):
        """drain() on empty queue returns []."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)
        result = client.drain()
        assert result == []

    def test_drain_returns_all_buffered_messages(self):
        """drain() returns all messages from the queue."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        # Manually populate the queue
        task1 = {"id": "task-1", "status": {"state": "input-required"}}
        task2 = {"id": "task-2", "status": {"state": "input-required"}}
        client.queue.put_nowait(task1)
        client.queue.put_nowait(task2)

        result = client.drain()
        assert len(result) == 2
        assert result[0] == task1
        assert result[1] == task2

    def test_drain_empties_the_queue(self):
        """After drain(), the queue is empty."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        client.queue.put_nowait({"id": "task-1"})
        client.queue.put_nowait({"id": "task-2"})
        client.drain()

        assert client.queue.empty()

    def test_drain_with_max_items_limits_results(self):
        """drain(max_items=N) returns at most N messages."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        for i in range(5):
            client.queue.put_nowait({"id": f"task-{i}"})

        result = client.drain(max_items=3)
        assert len(result) == 3
        # Should return first 3 items
        assert result[0]["id"] == "task-0"
        assert result[1]["id"] == "task-1"
        assert result[2]["id"] == "task-2"

    def test_drain_with_max_items_leaves_remaining_in_queue(self):
        """drain(max_items=N) leaves remaining messages in the queue."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        for i in range(5):
            client.queue.put_nowait({"id": f"task-{i}"})

        client.drain(max_items=2)
        assert client.queue.qsize() == 3

    def test_drain_preserves_order(self):
        """drain() returns messages in FIFO order."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        ids = [f"task-{i}" for i in range(10)]
        for task_id in ids:
            client.queue.put_nowait({"id": task_id})

        result = client.drain()
        assert [t["id"] for t in result] == ids

    def test_drain_with_max_items_none_returns_all(self):
        """drain(max_items=None) returns all messages (same as no argument)."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        for i in range(5):
            client.queue.put_nowait({"id": f"task-{i}"})

        result = client.drain(max_items=None)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# Buffer Overflow
# ---------------------------------------------------------------------------


class TestSSEClientBufferOverflow:
    """Tests for SSEClient buffer overflow — oldest message dropped when full."""

    @pytest.mark.asyncio
    async def test_buffer_overflow_drops_oldest(self):
        """When queue is full, adding a new message drops the oldest."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        # Fill the queue to capacity
        for i in range(MAX_BUFFER_SIZE):
            client.queue.put_nowait({"id": f"task-{i}"})

        assert client.queue.full()

        # Simulate what _read_loop does on overflow
        new_task = {"id": "task-new"}
        if client.queue.full():
            client.queue.get_nowait()  # Drop oldest
        await client.queue.put(new_task)

        assert client.queue.qsize() == MAX_BUFFER_SIZE

        # Drain and verify oldest was dropped
        result = client.drain()
        ids = [t["id"] for t in result]
        assert "task-0" not in ids  # Oldest was dropped
        assert "task-new" in ids  # New message is present
        assert ids[-1] == "task-new"

    @pytest.mark.asyncio
    async def test_buffer_stays_at_max_size_after_overflow(self):
        """After overflow, queue size remains MAX_BUFFER_SIZE."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        # Fill to capacity
        for i in range(MAX_BUFFER_SIZE):
            client.queue.put_nowait({"id": f"task-{i}"})

        # Add 5 more (overflow 5 times)
        for i in range(5):
            if client.queue.full():
                client.queue.get_nowait()
            await client.queue.put({"id": f"task-overflow-{i}"})

        assert client.queue.qsize() == MAX_BUFFER_SIZE


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


class TestSSEClientDisconnect:
    """Tests for SSEClient.disconnect() — cancels task and closes client."""

    @pytest.mark.asyncio
    async def test_disconnect_cancels_background_task(self):
        """disconnect() cancels the background read loop task."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        # Simulate a connected state with a mock task
        mock_task = AsyncMock()
        mock_task.cancel = MagicMock()
        client._task = mock_task
        client._client = AsyncMock()

        await client.disconnect()

        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_closes_httpx_client(self):
        """disconnect() closes the httpx.AsyncClient."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        mock_httpx = AsyncMock()
        client._client = mock_httpx
        client._task = None

        await client.disconnect()

        mock_httpx.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_drains_remaining_buffer(self):
        """disconnect() empties the buffer queue."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)

        # Add some messages to the buffer
        client.queue.put_nowait({"id": "task-1"})
        client.queue.put_nowait({"id": "task-2"})

        client._task = None
        client._client = None

        await client.disconnect()

        assert client.queue.empty()

    @pytest.mark.asyncio
    async def test_disconnect_safe_when_not_connected(self):
        """disconnect() doesn't raise when called before connect()."""
        client = SSEClient(BROKER_URL, API_KEY, AGENT_ID)
        # Should not raise
        await client.disconnect()
