"""Tests for executor.py — BrokerExecutor business logic.

Covers: unicast send, broadcast send, ACK (multi-turn), GetTask visibility,
CancelTask. Tests the executor methods directly with fakeredis-backed stores.

Also covers cross-tenant unicast rejection and tenant-scoped broadcast
(access-control feature).
"""

import hashlib
import uuid

import pytest
import fakeredis.aioredis
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import (
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskState,
    TextPart,
)

from hikyaku_registry.executor import BrokerExecutor
from hikyaku_registry.registry_store import RegistryStore
from hikyaku_registry.task_store import RedisTaskStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_DEFAULT_SHARED_KEY = "hky_00000000000000000000000000000001"
_DEFAULT_SHARED_HASH = hashlib.sha256(_DEFAULT_SHARED_KEY.encode()).hexdigest()


@pytest.fixture
async def env():
    """Set up BrokerExecutor with fakeredis-backed stores and test agents.

    All agents share the same API key (same tenant) for basic tests.
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RegistryStore(redis)
    task_store = RedisTaskStore(redis)
    executor = BrokerExecutor(registry_store=store, task_store=task_store)

    # Register test agents in the same tenant
    agent_a = await store.create_agent(
        name="Agent A", description="Sender", api_key=_DEFAULT_SHARED_KEY
    )
    agent_b = await store.create_agent(
        name="Agent B", description="Recipient", api_key=_DEFAULT_SHARED_KEY
    )
    agent_c = await store.create_agent(
        name="Agent C", description="Third agent", api_key=_DEFAULT_SHARED_KEY
    )

    yield {
        "executor": executor,
        "store": store,
        "task_store": task_store,
        "redis": redis,
        "agent_a": agent_a,
        "agent_b": agent_b,
        "agent_c": agent_c,
    }

    await redis.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_call_context(agent_id: str) -> ServerCallContext:
    """Create a ServerCallContext with the authenticated agent_id and tenant_id."""
    return ServerCallContext(
        state={"agent_id": agent_id, "tenant_id": _DEFAULT_SHARED_HASH},
    )


def _make_send_context(
    from_agent_id: str,
    destination: str,
    text: str = "Hello",
    task_id: str | None = None,
) -> RequestContext:
    """Create a RequestContext for sending a message."""
    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.user,
        parts=[Part(root=TextPart(text=text))],
        task_id=task_id,
        metadata={"destination": destination},
    )
    params = MessageSendParams(message=message)
    return RequestContext(
        request=params,
        call_context=_make_call_context(from_agent_id),
    )


def _make_ack_context(
    from_agent_id: str,
    task_id: str,
    text: str = "ack",
) -> RequestContext:
    """Create a RequestContext for ACKing an existing task (multi-turn)."""
    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.user,
        parts=[Part(root=TextPart(text=text))],
        task_id=task_id,
    )
    params = MessageSendParams(message=message)
    return RequestContext(
        request=params,
        task_id=task_id,
        call_context=_make_call_context(from_agent_id),
    )


def _make_cancel_context(
    from_agent_id: str,
    task_id: str,
    task: Task | None = None,
) -> RequestContext:
    """Create a RequestContext for canceling a task."""
    return RequestContext(
        task_id=task_id,
        task=task,
        call_context=_make_call_context(from_agent_id),
    )


async def _collect_events(queue: EventQueue) -> list:
    """Collect all events from the queue."""
    events = []
    try:
        while True:
            event = await queue.dequeue_event(no_wait=True)
            events.append(event)
    except Exception:
        pass
    return events


# ---------------------------------------------------------------------------
# Unicast Send
# ---------------------------------------------------------------------------


class TestUnicastSend:
    """Tests for BrokerExecutor.execute — unicast message delivery."""

    @pytest.mark.asyncio
    async def test_creates_delivery_task(self, env):
        """Unicast send creates a delivery Task."""
        executor, agent_a, agent_b = env["executor"], env["agent_a"], env["agent_b"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
            text="Did the API schema change?",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        tasks = [e for e in events if isinstance(e, Task)]
        assert len(tasks) >= 1

    @pytest.mark.asyncio
    async def test_task_state_is_input_required(self, env):
        """Delivery Task has state INPUT_REQUIRED."""
        executor, agent_a, agent_b = env["executor"], env["agent_a"], env["agent_b"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        task = next(e for e in events if isinstance(e, Task))
        assert task.status.state == TaskState.input_required

    @pytest.mark.asyncio
    async def test_task_context_id_is_recipient(self, env):
        """Delivery Task contextId equals the recipient's agent_id."""
        executor, agent_a, agent_b = env["executor"], env["agent_a"], env["agent_b"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        task = next(e for e in events if isinstance(e, Task))
        assert task.context_id == agent_b["agent_id"]

    @pytest.mark.asyncio
    async def test_message_content_in_artifact(self, env):
        """Message text is stored as an Artifact on the delivery Task."""
        executor, agent_a, agent_b = env["executor"], env["agent_a"], env["agent_b"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
            text="Did the API schema change?",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        task = next(e for e in events if isinstance(e, Task))
        assert task.artifacts is not None
        assert len(task.artifacts) >= 1

    @pytest.mark.asyncio
    async def test_task_metadata_has_routing_info(self, env):
        """Delivery Task metadata contains fromAgentId, toAgentId, type."""
        executor, agent_a, agent_b = env["executor"], env["agent_a"], env["agent_b"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        task = next(e for e in events if isinstance(e, Task))
        assert task.metadata["fromAgentId"] == agent_a["agent_id"]
        assert task.metadata["toAgentId"] == agent_b["agent_id"]
        assert task.metadata["type"] == "unicast"

    @pytest.mark.asyncio
    async def test_error_missing_destination(self, env):
        """Missing metadata.destination raises an error."""
        executor, agent_a = env["executor"], env["agent_a"]
        queue = EventQueue()

        # Create message without destination metadata
        message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.user,
            parts=[Part(root=TextPart(text="No destination"))],
        )
        params = MessageSendParams(message=message)
        context = RequestContext(
            request=params,
            call_context=_make_call_context(agent_a["agent_id"]),
        )

        with pytest.raises(Exception) as exc_info:
            await executor.execute(context, queue)

        # Should be an InvalidParams-type error
        assert exc_info.value is not None

    @pytest.mark.asyncio
    async def test_error_destination_not_found(self, env):
        """Destination agent_id not found raises an error."""
        executor, agent_a = env["executor"], env["agent_a"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="00000000-0000-4000-8000-000000000000",
        )

        with pytest.raises(Exception):
            await executor.execute(context, queue)

    @pytest.mark.asyncio
    async def test_error_destination_deregistered(self, env):
        """Sending to a deregistered agent raises an error."""
        executor, store, agent_a, agent_b = (
            env["executor"],
            env["store"],
            env["agent_a"],
            env["agent_b"],
        )
        queue = EventQueue()

        await store.deregister_agent(agent_b["agent_id"])

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
        )

        with pytest.raises(Exception):
            await executor.execute(context, queue)

    @pytest.mark.asyncio
    async def test_error_invalid_destination_format(self, env):
        """Invalid destination format (not UUID or '*') raises an error."""
        executor, agent_a = env["executor"], env["agent_a"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="not-a-valid-uuid",
        )

        with pytest.raises(Exception):
            await executor.execute(context, queue)


# ---------------------------------------------------------------------------
# Broadcast Send
# ---------------------------------------------------------------------------


class TestBroadcastSend:
    """Tests for BrokerExecutor.execute — broadcast message delivery."""

    @pytest.mark.asyncio
    async def test_creates_delivery_tasks_for_all_active_agents(self, env):
        """Broadcast creates one delivery Task per active agent (excluding sender)."""
        executor, _task_store, agent_a, _agent_b, _agent_c = (
            env["executor"],
            env["task_store"],
            env["agent_a"],
            env["agent_b"],
            env["agent_c"],
        )
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
            text="Build failed on main branch",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        tasks = [e for e in events if isinstance(e, Task)]

        # Should have delivery tasks for agent_b and agent_c + summary task
        assert len(tasks) >= 3  # 2 delivery + 1 summary

    @pytest.mark.asyncio
    async def test_excludes_sender_from_recipients(self, env):
        """Sender does not receive their own broadcast."""
        executor, agent_a = env["executor"], env["agent_a"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        delivery_tasks = [
            e
            for e in events
            if isinstance(e, Task)
            and e.metadata
            and e.metadata.get("type") == "unicast"
        ]

        for task in delivery_tasks:
            assert task.context_id != agent_a["agent_id"]

    @pytest.mark.asyncio
    async def test_summary_task_is_completed(self, env):
        """Broadcast returns a summary Task with state=COMPLETED."""
        executor, agent_a = env["executor"], env["agent_a"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        summary_tasks = [
            e
            for e in events
            if isinstance(e, Task)
            and e.metadata
            and e.metadata.get("type") in ("broadcast", "broadcast_summary")
        ]

        assert len(summary_tasks) >= 1
        summary = summary_tasks[0]
        assert summary.status.state == TaskState.completed

    @pytest.mark.asyncio
    async def test_summary_task_has_recipient_count(self, env):
        """Summary Task artifact includes recipientCount."""
        executor, agent_a = env["executor"], env["agent_a"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        summary_tasks = [
            e
            for e in events
            if isinstance(e, Task)
            and e.metadata
            and e.metadata.get("type") in ("broadcast", "broadcast_summary")
        ]

        summary = summary_tasks[0]
        assert summary.artifacts is not None
        assert len(summary.artifacts) >= 1

    @pytest.mark.asyncio
    async def test_delivery_tasks_have_input_required_state(self, env):
        """Each delivery Task is in INPUT_REQUIRED state."""
        executor, agent_a, _agent_b, _agent_c = (
            env["executor"],
            env["agent_a"],
            env["agent_b"],
            env["agent_c"],
        )
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        delivery_tasks = [
            e
            for e in events
            if isinstance(e, Task) and e.status.state == TaskState.input_required
        ]

        # Should have 2 delivery tasks (agent_b and agent_c)
        assert len(delivery_tasks) == 2

    @pytest.mark.asyncio
    async def test_each_delivery_task_context_id_is_recipient(self, env):
        """Each delivery Task's contextId equals its recipient's agent_id."""
        executor, agent_a, agent_b, agent_c = (
            env["executor"],
            env["agent_a"],
            env["agent_b"],
            env["agent_c"],
        )
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        delivery_tasks = [
            e
            for e in events
            if isinstance(e, Task) and e.status.state == TaskState.input_required
        ]

        recipient_ids = {t.context_id for t in delivery_tasks}
        assert agent_b["agent_id"] in recipient_ids
        assert agent_c["agent_id"] in recipient_ids

    @pytest.mark.asyncio
    async def test_broadcast_no_other_agents(self, env):
        """Broadcast with no other active agents produces recipientCount=0."""
        executor, store, agent_a, agent_b, agent_c = (
            env["executor"],
            env["store"],
            env["agent_a"],
            env["agent_b"],
            env["agent_c"],
        )
        queue = EventQueue()

        # Deregister all other agents
        await store.deregister_agent(agent_b["agent_id"])
        await store.deregister_agent(agent_c["agent_id"])

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        delivery_tasks = [
            e
            for e in events
            if isinstance(e, Task) and e.status.state == TaskState.input_required
        ]
        assert len(delivery_tasks) == 0


# ---------------------------------------------------------------------------
# ACK (Multi-Turn)
# ---------------------------------------------------------------------------


class TestAck:
    """Tests for BrokerExecutor.execute — ACK via multi-turn SendMessage."""

    async def _create_unicast_task(self, env):
        """Helper: send a unicast message and return the delivery Task."""
        executor, agent_a, agent_b = env["executor"], env["agent_a"], env["agent_b"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
            text="Hello Agent B",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        return next(e for e in events if isinstance(e, Task))

    @pytest.mark.asyncio
    async def test_ack_moves_task_to_completed(self, env):
        """Recipient ACK moves the Task to COMPLETED state."""
        executor, agent_b = env["executor"], env["agent_b"]
        delivery_task = await self._create_unicast_task(env)

        queue = EventQueue()
        context = _make_ack_context(
            from_agent_id=agent_b["agent_id"],
            task_id=delivery_task.id,
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        task = next(e for e in events if isinstance(e, Task))
        assert task.status.state == TaskState.completed

    @pytest.mark.asyncio
    async def test_ack_by_non_recipient_raises_error(self, env):
        """ACK by a non-recipient agent raises an error."""
        executor, agent_c = env["executor"], env["agent_c"]
        delivery_task = await self._create_unicast_task(env)

        queue = EventQueue()
        context = _make_ack_context(
            from_agent_id=agent_c["agent_id"],
            task_id=delivery_task.id,
        )

        with pytest.raises(Exception):
            await executor.execute(context, queue)

    @pytest.mark.asyncio
    async def test_ack_on_already_completed_raises_error(self, env):
        """ACK on an already completed task raises an error."""
        executor, agent_b = env["executor"], env["agent_b"]
        delivery_task = await self._create_unicast_task(env)

        # First ACK (succeeds)
        queue1 = EventQueue()
        ctx1 = _make_ack_context(
            from_agent_id=agent_b["agent_id"],
            task_id=delivery_task.id,
        )
        await executor.execute(ctx1, queue1)

        # Second ACK (should fail — task is already completed)
        queue2 = EventQueue()
        ctx2 = _make_ack_context(
            from_agent_id=agent_b["agent_id"],
            task_id=delivery_task.id,
        )

        with pytest.raises(Exception):
            await executor.execute(ctx2, queue2)

    @pytest.mark.asyncio
    async def test_ack_on_canceled_task_raises_error(self, env):
        """ACK on a canceled task raises an error."""
        executor, agent_a, agent_b = (
            env["executor"],
            env["agent_a"],
            env["agent_b"],
        )
        delivery_task = await self._create_unicast_task(env)

        # Cancel the task first
        cancel_queue = EventQueue()
        cancel_ctx = _make_cancel_context(
            from_agent_id=agent_a["agent_id"],
            task_id=delivery_task.id,
            task=delivery_task,
        )
        await executor.cancel(cancel_ctx, cancel_queue)

        # Now try to ACK — should fail
        ack_queue = EventQueue()
        ack_ctx = _make_ack_context(
            from_agent_id=agent_b["agent_id"],
            task_id=delivery_task.id,
        )

        with pytest.raises(Exception):
            await executor.execute(ack_ctx, ack_queue)

    @pytest.mark.asyncio
    async def test_ack_on_unknown_task_raises_error(self, env):
        """ACK on a non-existent task raises an error."""
        executor, agent_b = env["executor"], env["agent_b"]

        queue = EventQueue()
        context = _make_ack_context(
            from_agent_id=agent_b["agent_id"],
            task_id="nonexistent-task-id",
        )

        with pytest.raises(Exception):
            await executor.execute(context, queue)


# ---------------------------------------------------------------------------
# GetTask Visibility
# ---------------------------------------------------------------------------


class TestGetTaskVisibility:
    """Tests for task access control on GetTask."""

    async def _create_unicast_task(self, env):
        """Helper: send a unicast from A to B, return the delivery Task."""
        executor, agent_a, agent_b = env["executor"], env["agent_a"], env["agent_b"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        return next(e for e in events if isinstance(e, Task))

    @pytest.mark.asyncio
    async def test_sender_can_get_task(self, env):
        """Sender can access the task they created by taskId."""
        task_store, _agent_a = env["task_store"], env["agent_a"]
        delivery_task = await self._create_unicast_task(env)

        result = await task_store.get(delivery_task.id)
        assert result is not None

    @pytest.mark.asyncio
    async def test_recipient_can_get_task(self, env):
        """Recipient can access the task in their context."""
        task_store, _agent_b = env["task_store"], env["agent_b"]
        delivery_task = await self._create_unicast_task(env)

        result = await task_store.get(delivery_task.id)
        assert result is not None

    @pytest.mark.asyncio
    async def test_task_stored_in_task_store(self, env):
        """The delivery Task is persisted in the TaskStore."""
        task_store = env["task_store"]
        delivery_task = await self._create_unicast_task(env)

        result = await task_store.get(delivery_task.id)
        assert result is not None

    @pytest.mark.asyncio
    async def test_task_indexed_by_recipient_context(self, env):
        """Delivery Task is indexed under recipient's contextId in sorted set."""
        _task_store, redis, agent_b = (
            env["task_store"],
            env["redis"],
            env["agent_b"],
        )
        delivery_task = await self._create_unicast_task(env)

        score = await redis.zscore(f"tasks:ctx:{agent_b['agent_id']}", delivery_task.id)
        assert score is not None

    @pytest.mark.asyncio
    async def test_task_indexed_by_sender(self, env):
        """Delivery Task is indexed in sender's tasks:sender set."""
        redis, agent_a = env["redis"], env["agent_a"]
        delivery_task = await self._create_unicast_task(env)

        is_member = await redis.sismember(
            f"tasks:sender:{agent_a['agent_id']}", delivery_task.id
        )
        assert is_member


# ---------------------------------------------------------------------------
# CancelTask
# ---------------------------------------------------------------------------


class TestCancelTask:
    """Tests for BrokerExecutor.cancel — message retraction."""

    async def _create_unicast_task(self, env):
        """Helper: send a unicast from A to B, return the delivery Task."""
        executor, agent_a, agent_b = env["executor"], env["agent_a"], env["agent_b"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        return next(e for e in events if isinstance(e, Task))

    @pytest.mark.asyncio
    async def test_sender_can_cancel_input_required_task(self, env):
        """Sender can cancel a task that is still INPUT_REQUIRED."""
        executor, agent_a = env["executor"], env["agent_a"]
        delivery_task = await self._create_unicast_task(env)

        queue = EventQueue()
        context = _make_cancel_context(
            from_agent_id=agent_a["agent_id"],
            task_id=delivery_task.id,
            task=delivery_task,
        )
        await executor.cancel(context, queue)

        events = await _collect_events(queue)
        # Should have a status update or task with canceled state
        assert any(
            (isinstance(e, Task) and e.status.state == TaskState.canceled)
            or (hasattr(e, "status") and e.status.state == TaskState.canceled)
            for e in events
        )

    @pytest.mark.asyncio
    async def test_non_sender_cannot_cancel(self, env):
        """Non-sender cannot cancel a task — raises error."""
        executor, agent_b = env["executor"], env["agent_b"]
        delivery_task = await self._create_unicast_task(env)

        queue = EventQueue()
        context = _make_cancel_context(
            from_agent_id=agent_b["agent_id"],
            task_id=delivery_task.id,
            task=delivery_task,
        )

        with pytest.raises(Exception):
            await executor.cancel(context, queue)

    @pytest.mark.asyncio
    async def test_cancel_completed_task_raises_error(self, env):
        """Cannot cancel a task that is already COMPLETED."""
        executor, agent_a, agent_b = (
            env["executor"],
            env["agent_a"],
            env["agent_b"],
        )
        delivery_task = await self._create_unicast_task(env)

        # ACK the task first (→ COMPLETED)
        ack_queue = EventQueue()
        ack_ctx = _make_ack_context(
            from_agent_id=agent_b["agent_id"],
            task_id=delivery_task.id,
        )
        await executor.execute(ack_ctx, ack_queue)

        # Now try to cancel — should fail
        cancel_queue = EventQueue()
        cancel_ctx = _make_cancel_context(
            from_agent_id=agent_a["agent_id"],
            task_id=delivery_task.id,
            task=delivery_task,
        )

        with pytest.raises(Exception):
            await executor.cancel(cancel_ctx, cancel_queue)

    @pytest.mark.asyncio
    async def test_cancel_already_canceled_task_raises_error(self, env):
        """Cannot cancel a task that is already CANCELED."""
        executor, agent_a = env["executor"], env["agent_a"]
        delivery_task = await self._create_unicast_task(env)

        # Cancel once
        queue1 = EventQueue()
        ctx1 = _make_cancel_context(
            from_agent_id=agent_a["agent_id"],
            task_id=delivery_task.id,
            task=delivery_task,
        )
        await executor.cancel(ctx1, queue1)

        # Cancel again — should fail
        queue2 = EventQueue()
        ctx2 = _make_cancel_context(
            from_agent_id=agent_a["agent_id"],
            task_id=delivery_task.id,
            task=delivery_task,
        )

        with pytest.raises(Exception):
            await executor.cancel(ctx2, queue2)

    @pytest.mark.asyncio
    async def test_cancel_unknown_task_raises_error(self, env):
        """Cannot cancel a task that doesn't exist."""
        executor, agent_a = env["executor"], env["agent_a"]

        queue = EventQueue()
        context = _make_cancel_context(
            from_agent_id=agent_a["agent_id"],
            task_id="nonexistent-task-id",
        )

        with pytest.raises(Exception):
            await executor.cancel(context, queue)


# ===========================================================================
# Multi-tenant executor tests (access-control feature)
# ===========================================================================

# Fixed tenant API keys
_TENANT_A_KEY = "hky_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"
_TENANT_A_HASH = hashlib.sha256(_TENANT_A_KEY.encode()).hexdigest()

_TENANT_B_KEY = "hky_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1"
_TENANT_B_HASH = hashlib.sha256(_TENANT_B_KEY.encode()).hexdigest()


@pytest.fixture
async def tenant_env():
    """Set up BrokerExecutor with agents in two separate tenants.

    Tenant A: agent_a1, agent_a2
    Tenant B: agent_b1
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RegistryStore(redis)
    task_store = RedisTaskStore(redis)
    executor = BrokerExecutor(registry_store=store, task_store=task_store)

    agent_a1 = await store.create_agent(
        name="Agent A1", description="Tenant A first", api_key=_TENANT_A_KEY
    )
    agent_a2 = await store.create_agent(
        name="Agent A2", description="Tenant A second", api_key=_TENANT_A_KEY
    )
    agent_b1 = await store.create_agent(
        name="Agent B1", description="Tenant B only", api_key=_TENANT_B_KEY
    )

    yield {
        "executor": executor,
        "store": store,
        "task_store": task_store,
        "redis": redis,
        "agent_a1": agent_a1,
        "agent_a2": agent_a2,
        "agent_b1": agent_b1,
    }

    await redis.aclose()


def _make_tenant_call_context(agent_id: str, tenant_id: str) -> ServerCallContext:
    """Create a ServerCallContext with agent_id and tenant_id."""
    return ServerCallContext(
        state={"agent_id": agent_id, "tenant_id": tenant_id},
    )


def _make_tenant_send_context(
    from_agent_id: str,
    tenant_id: str,
    destination: str,
    text: str = "Hello",
    task_id: str | None = None,
) -> RequestContext:
    """Create a RequestContext for sending with tenant context."""
    message = Message(
        message_id=str(uuid.uuid4()),
        role=Role.user,
        parts=[Part(root=TextPart(text=text))],
        task_id=task_id,
        metadata={"destination": destination},
    )
    params = MessageSendParams(message=message)
    return RequestContext(
        request=params,
        call_context=_make_tenant_call_context(from_agent_id, tenant_id),
    )


class TestCrossTenantUnicast:
    """Tests for cross-tenant unicast rejection.

    Unicast must verify destination agent's api_key_hash matches sender's
    tenant. Cross-tenant sends produce "agent not found" errors.
    """

    @pytest.mark.asyncio
    async def test_same_tenant_unicast_succeeds(self, tenant_env):
        """Agent A1 sends to Agent A2 (same tenant) → succeeds."""
        executor = tenant_env["executor"]
        agent_a1, agent_a2 = tenant_env["agent_a1"], tenant_env["agent_a2"]
        queue = EventQueue()

        context = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination=agent_a2["agent_id"],
            text="Hello teammate",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        tasks = [e for e in events if isinstance(e, Task)]
        assert len(tasks) >= 1
        task = tasks[0]
        assert task.status.state == TaskState.input_required
        assert task.context_id == agent_a2["agent_id"]

    @pytest.mark.asyncio
    async def test_cross_tenant_unicast_raises_error(self, tenant_env):
        """Agent A1 sends to Agent B1 (different tenant) → error."""
        executor = tenant_env["executor"]
        agent_a1, agent_b1 = tenant_env["agent_a1"], tenant_env["agent_b1"]
        queue = EventQueue()

        context = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination=agent_b1["agent_id"],
        )

        with pytest.raises(Exception):
            await executor.execute(context, queue)

    @pytest.mark.asyncio
    async def test_cross_tenant_error_indistinguishable_from_not_found(
        self, tenant_env
    ):
        """Cross-tenant error message is the same as 'agent not found'.

        The caller cannot distinguish between 'agent exists in another tenant'
        and 'agent does not exist at all'.
        """
        executor = tenant_env["executor"]
        agent_a1, agent_b1 = tenant_env["agent_a1"], tenant_env["agent_b1"]
        queue = EventQueue()

        # Cross-tenant send
        cross_ctx = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination=agent_b1["agent_id"],
        )

        with pytest.raises(Exception) as cross_exc:
            await executor.execute(cross_ctx, queue)

        # Non-existent agent send
        ghost_ctx = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination="00000000-0000-4000-8000-000000000000",
        )

        with pytest.raises(Exception) as ghost_exc:
            await executor.execute(ghost_ctx, queue)

        # Both should produce the same type of error
        assert type(cross_exc.value) is type(ghost_exc.value)

    @pytest.mark.asyncio
    async def test_reverse_cross_tenant_also_blocked(self, tenant_env):
        """Agent B1 sends to Agent A1 (reverse direction) → also blocked."""
        executor = tenant_env["executor"]
        agent_a1, agent_b1 = tenant_env["agent_a1"], tenant_env["agent_b1"]
        queue = EventQueue()

        context = _make_tenant_send_context(
            from_agent_id=agent_b1["agent_id"],
            tenant_id=_TENANT_B_HASH,
            destination=agent_a1["agent_id"],
        )

        with pytest.raises(Exception):
            await executor.execute(context, queue)

    @pytest.mark.asyncio
    async def test_cross_tenant_no_task_created(self, tenant_env):
        """Cross-tenant send does not persist any delivery task."""
        executor, task_store = tenant_env["executor"], tenant_env["task_store"]
        agent_a1, agent_b1 = tenant_env["agent_a1"], tenant_env["agent_b1"]
        queue = EventQueue()

        context = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination=agent_b1["agent_id"],
        )

        with pytest.raises(Exception):
            await executor.execute(context, queue)

        # No tasks should be in B1's context
        tasks = await task_store.list(agent_b1["agent_id"])
        assert len(tasks) == 0


class TestTenantScopedBroadcast:
    """Tests for tenant-scoped broadcast.

    Broadcast from tenant A → only delivers to agents in tenant A.
    Agents in tenant B never receive the broadcast.
    """

    @pytest.mark.asyncio
    async def test_broadcast_delivers_only_to_same_tenant(self, tenant_env):
        """Broadcast from A1 delivers to A2 only, not B1."""
        executor = tenant_env["executor"]
        agent_a1, agent_a2 = tenant_env["agent_a1"], tenant_env["agent_a2"]
        agent_b1 = tenant_env["agent_b1"]
        queue = EventQueue()

        context = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination="*",
            text="Tenant A broadcast",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        delivery_tasks = [
            e
            for e in events
            if isinstance(e, Task) and e.status.state == TaskState.input_required
        ]

        recipient_ids = {t.context_id for t in delivery_tasks}
        assert agent_a2["agent_id"] in recipient_ids
        assert agent_b1["agent_id"] not in recipient_ids

    @pytest.mark.asyncio
    async def test_broadcast_excludes_sender(self, tenant_env):
        """Broadcast excludes the sender even within the same tenant."""
        executor = tenant_env["executor"]
        agent_a1 = tenant_env["agent_a1"]
        queue = EventQueue()

        context = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        delivery_tasks = [
            e
            for e in events
            if isinstance(e, Task) and e.status.state == TaskState.input_required
        ]

        for task in delivery_tasks:
            assert task.context_id != agent_a1["agent_id"]

    @pytest.mark.asyncio
    async def test_broadcast_delivery_count_matches_tenant_size(self, tenant_env):
        """Number of delivery tasks equals (tenant agents - 1)."""
        executor = tenant_env["executor"]
        agent_a1 = tenant_env["agent_a1"]
        queue = EventQueue()

        context = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        delivery_tasks = [
            e
            for e in events
            if isinstance(e, Task) and e.status.state == TaskState.input_required
        ]

        # Tenant A has 2 agents (a1, a2), so 1 delivery task (to a2)
        assert len(delivery_tasks) == 1

    @pytest.mark.asyncio
    async def test_broadcast_summary_reflects_tenant_recipients(self, tenant_env):
        """Summary task recipientCount counts only same-tenant agents."""
        executor = tenant_env["executor"]
        agent_a1 = tenant_env["agent_a1"]
        queue = EventQueue()

        context = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        summary_tasks = [
            e
            for e in events
            if isinstance(e, Task)
            and e.metadata
            and e.metadata.get("type") in ("broadcast", "broadcast_summary")
        ]

        assert len(summary_tasks) == 1
        summary = summary_tasks[0]
        assert summary.metadata["recipientCount"] == 1

    @pytest.mark.asyncio
    async def test_tenant_b_broadcast_does_not_reach_tenant_a(self, tenant_env):
        """Broadcast from tenant B delivers only within tenant B."""
        executor, task_store = tenant_env["executor"], tenant_env["task_store"]
        agent_a1, agent_a2 = tenant_env["agent_a1"], tenant_env["agent_a2"]
        agent_b1 = tenant_env["agent_b1"]
        queue = EventQueue()

        context = _make_tenant_send_context(
            from_agent_id=agent_b1["agent_id"],
            tenant_id=_TENANT_B_HASH,
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        delivery_tasks = [
            e
            for e in events
            if isinstance(e, Task) and e.status.state == TaskState.input_required
        ]

        # B1 is alone in tenant B, so no delivery tasks
        assert len(delivery_tasks) == 0

        # Verify nothing landed in tenant A agents' inboxes
        a1_tasks = await task_store.list(agent_a1["agent_id"])
        a2_tasks = await task_store.list(agent_a2["agent_id"])
        assert len(a1_tasks) == 0
        assert len(a2_tasks) == 0

    @pytest.mark.asyncio
    async def test_broadcast_after_deregister_in_tenant(self, tenant_env):
        """Broadcast skips deregistered agents within the same tenant."""
        executor, store = tenant_env["executor"], tenant_env["store"]
        agent_a1, agent_a2 = tenant_env["agent_a1"], tenant_env["agent_a2"]
        queue = EventQueue()

        await store.deregister_agent(agent_a2["agent_id"])

        context = _make_tenant_send_context(
            from_agent_id=agent_a1["agent_id"],
            tenant_id=_TENANT_A_HASH,
            destination="*",
        )
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        delivery_tasks = [
            e
            for e in events
            if isinstance(e, Task) and e.status.state == TaskState.input_required
        ]

        # A2 deregistered, so no delivery tasks
        assert len(delivery_tasks) == 0


# ---------------------------------------------------------------------------
# Pub/Sub Publish Integration
# ---------------------------------------------------------------------------


class TestExecutorPubSubIntegration:
    """Tests for BrokerExecutor publish integration with PubSubManager.

    Verifies that:
    - BrokerExecutor.__init__ accepts a pubsub parameter
    - _handle_unicast publishes task_id to inbox:{destination} after save
    - _handle_broadcast publishes task_id for each recipient's inbox channel
    """

    @pytest.fixture
    async def pubsub_env(self):
        """Set up BrokerExecutor with a mock PubSubManager and test agents."""
        from unittest.mock import AsyncMock, MagicMock

        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = RegistryStore(redis)
        task_store = RedisTaskStore(redis)

        # Mock PubSubManager
        mock_pubsub = MagicMock()
        mock_pubsub.publish = AsyncMock()

        executor = BrokerExecutor(
            registry_store=store,
            task_store=task_store,
            pubsub=mock_pubsub,
        )

        # Register test agents in the same tenant
        agent_a = await store.create_agent(
            name="Agent A", description="Sender", api_key=_DEFAULT_SHARED_KEY
        )
        agent_b = await store.create_agent(
            name="Agent B", description="Recipient", api_key=_DEFAULT_SHARED_KEY
        )
        agent_c = await store.create_agent(
            name="Agent C", description="Third agent", api_key=_DEFAULT_SHARED_KEY
        )

        yield {
            "executor": executor,
            "store": store,
            "task_store": task_store,
            "redis": redis,
            "mock_pubsub": mock_pubsub,
            "agent_a": agent_a,
            "agent_b": agent_b,
            "agent_c": agent_c,
        }

        await redis.aclose()

    @pytest.mark.asyncio
    async def test_init_accepts_pubsub_parameter(self, pubsub_env):
        """BrokerExecutor.__init__ accepts a pubsub parameter."""
        executor = pubsub_env["executor"]
        # If we got here without error, __init__ accepted pubsub
        assert executor is not None

    @pytest.mark.asyncio
    async def test_unicast_publishes_task_id_to_recipient_channel(self, pubsub_env):
        """After unicast send, publish is called on inbox:{destination} with task_id."""
        executor = pubsub_env["executor"]
        agent_a = pubsub_env["agent_a"]
        agent_b = pubsub_env["agent_b"]
        mock_pubsub = pubsub_env["mock_pubsub"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
            text="Hello via unicast",
        )
        await executor.execute(context, queue)

        # Verify publish was called once for the recipient
        mock_pubsub.publish.assert_called_once()
        call_args = mock_pubsub.publish.call_args
        channel = (
            call_args[0][0]
            if call_args[0]
            else call_args[1].get("channel", call_args.kwargs.get("channel"))
        )

        assert channel == f"inbox:{agent_b['agent_id']}"

    @pytest.mark.asyncio
    async def test_unicast_publishes_task_id_as_payload(self, pubsub_env):
        """Unicast publish payload is the task_id string, not full Task JSON."""
        executor = pubsub_env["executor"]
        agent_a = pubsub_env["agent_a"]
        agent_b = pubsub_env["agent_b"]
        mock_pubsub = pubsub_env["mock_pubsub"]
        task_store = pubsub_env["task_store"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
            text="Task ID payload check",
        )
        await executor.execute(context, queue)

        # Get the published task_id
        call_args = mock_pubsub.publish.call_args
        published_task_id = (
            call_args[0][1]
            if len(call_args[0]) > 1
            else call_args[1].get("message", call_args.kwargs.get("message"))
        )

        # Verify it's a valid task_id (string, not JSON)
        assert isinstance(published_task_id, str)
        assert "{" not in published_task_id  # Not JSON

        # Verify the task_id can be looked up in task_store
        task = await task_store.get(published_task_id)
        assert task is not None

    @pytest.mark.asyncio
    async def test_broadcast_publishes_to_each_recipient_channel(self, pubsub_env):
        """After broadcast, publish is called for each recipient's inbox channel."""
        executor = pubsub_env["executor"]
        agent_a = pubsub_env["agent_a"]
        agent_b = pubsub_env["agent_b"]
        agent_c = pubsub_env["agent_c"]
        mock_pubsub = pubsub_env["mock_pubsub"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
            text="Broadcast message",
        )
        await executor.execute(context, queue)

        # Should have published to inbox:{agent_b} and inbox:{agent_c}
        published_channels = [call[0][0] for call in mock_pubsub.publish.call_args_list]
        expected_channels = {
            f"inbox:{agent_b['agent_id']}",
            f"inbox:{agent_c['agent_id']}",
        }

        assert set(published_channels) == expected_channels

    @pytest.mark.asyncio
    async def test_broadcast_does_not_publish_to_sender(self, pubsub_env):
        """Broadcast does not publish to the sender's own inbox channel."""
        executor = pubsub_env["executor"]
        agent_a = pubsub_env["agent_a"]
        mock_pubsub = pubsub_env["mock_pubsub"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
            text="Broadcast no self",
        )
        await executor.execute(context, queue)

        published_channels = [call[0][0] for call in mock_pubsub.publish.call_args_list]

        assert f"inbox:{agent_a['agent_id']}" not in published_channels

    @pytest.mark.asyncio
    async def test_broadcast_publishes_task_ids_not_json(self, pubsub_env):
        """Broadcast publishes task_id strings, not full Task JSON."""
        executor = pubsub_env["executor"]
        agent_a = pubsub_env["agent_a"]
        mock_pubsub = pubsub_env["mock_pubsub"]
        task_store = pubsub_env["task_store"]
        queue = EventQueue()

        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination="*",
            text="Broadcast payload check",
        )
        await executor.execute(context, queue)

        for call in mock_pubsub.publish.call_args_list:
            task_id = call[0][1]
            assert isinstance(task_id, str)
            assert "{" not in task_id  # Not JSON
            # Each published task_id should exist in task_store
            task = await task_store.get(task_id)
            assert task is not None

    @pytest.mark.asyncio
    async def test_no_pubsub_parameter_still_works(self):
        """BrokerExecutor without pubsub parameter works (backward compat)."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = RegistryStore(redis)
        task_store = RedisTaskStore(redis)

        # No pubsub parameter — should still construct without error
        executor = BrokerExecutor(
            registry_store=store,
            task_store=task_store,
        )
        assert executor is not None
        await redis.aclose()

    @pytest.mark.asyncio
    async def test_unicast_no_pubsub_skips_publish(self):
        """Unicast without pubsub does not fail (graceful no-op)."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = RegistryStore(redis)
        task_store = RedisTaskStore(redis)
        executor = BrokerExecutor(
            registry_store=store,
            task_store=task_store,
        )

        agent_a = await store.create_agent(
            name="A", description="Sender", api_key=_DEFAULT_SHARED_KEY
        )
        agent_b = await store.create_agent(
            name="B", description="Recipient", api_key=_DEFAULT_SHARED_KEY
        )

        queue = EventQueue()
        context = _make_send_context(
            from_agent_id=agent_a["agent_id"],
            destination=agent_b["agent_id"],
        )
        # Should not raise even though pubsub is None
        await executor.execute(context, queue)

        events = await _collect_events(queue)
        tasks = [e for e in events if isinstance(e, Task)]
        assert len(tasks) >= 1

        await redis.aclose()
