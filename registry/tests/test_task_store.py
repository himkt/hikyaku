"""Tests for task_store.py — RedisTaskStore (A2A SDK TaskStore for Redis).

Covers: save, get, delete, list.
Verifies Redis key schema: task:{task_id}, tasks:ctx:{context_id} sorted set,
tasks:sender:{agent_id} set.
Verifies sorted set scoring by status timestamp and re-scoring on state change.
"""

import json
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from a2a.types import (
    Artifact,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ts(dt: datetime) -> str:
    """Convert datetime to ISO 8601 string for TaskStatus.timestamp."""
    return dt.isoformat()


def _make_task(
    task_id: str | None = None,
    context_id: str | None = None,
    state: TaskState = TaskState.input_required,
    timestamp: str | None = None,
    from_agent_id: str = "sender-0000",
    to_agent_id: str = "recipient-0000",
    msg_type: str = "unicast",
    text: str = "Hello",
) -> Task:
    """Create a Task object with routing metadata for testing."""
    if task_id is None:
        task_id = str(uuid.uuid4())
    if context_id is None:
        context_id = to_agent_id
    if timestamp is None:
        timestamp = _ts(datetime.now(timezone.utc))

    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state, timestamp=timestamp),
        artifacts=[
            Artifact(
                artifact_id=str(uuid.uuid4()),
                name="message",
                parts=[Part(root=TextPart(text=text))],
                metadata={
                    "fromAgentId": from_agent_id,
                    "fromAgentName": "Test Sender",
                    "type": msg_type,
                },
            )
        ],
        metadata={
            "fromAgentId": from_agent_id,
            "toAgentId": to_agent_id,
            "type": msg_type,
        },
    )


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


class TestSave:
    """Tests for RedisTaskStore.save — stores Task + builds indexes."""

    @pytest.mark.asyncio
    async def test_stores_task_hash_in_redis(self, task_store, redis_client):
        """save creates a task:{task_id} hash in Redis."""
        task = _make_task(task_id="task-001")
        await task_store.save(task)

        exists = await redis_client.exists("task:task-001")
        assert exists

    @pytest.mark.asyncio
    async def test_stores_task_json_field(self, task_store, redis_client):
        """task:{task_id} hash contains a task_json field with valid JSON."""
        task = _make_task(task_id="task-001")
        await task_store.save(task)

        task_json = await redis_client.hget("task:task-001", "task_json")
        assert task_json is not None
        parsed = json.loads(task_json)
        assert isinstance(parsed, dict)

    @pytest.mark.asyncio
    async def test_stores_routing_metadata(self, task_store, redis_client):
        """task:{task_id} hash contains from_agent_id, to_agent_id, type fields."""
        task = _make_task(
            task_id="task-001",
            from_agent_id="sender-aaa",
            to_agent_id="recipient-bbb",
            msg_type="unicast",
        )
        await task_store.save(task)

        from_id = await redis_client.hget("task:task-001", "from_agent_id")
        to_id = await redis_client.hget("task:task-001", "to_agent_id")
        msg_type = await redis_client.hget("task:task-001", "type")

        assert from_id == "sender-aaa"
        assert to_id == "recipient-bbb"
        assert msg_type == "unicast"

    @pytest.mark.asyncio
    async def test_stores_created_at(self, task_store, redis_client):
        """task:{task_id} hash contains a created_at ISO 8601 timestamp."""
        task = _make_task(task_id="task-001")
        await task_store.save(task)

        created_at = await redis_client.hget("task:task-001", "created_at")
        assert created_at is not None
        dt = datetime.fromisoformat(created_at)
        assert isinstance(dt, datetime)

    @pytest.mark.asyncio
    async def test_adds_to_context_sorted_set(self, task_store, redis_client):
        """save adds task_id to tasks:ctx:{context_id} sorted set."""
        task = _make_task(task_id="task-001", context_id="ctx-abc")
        await task_store.save(task)

        score = await redis_client.zscore("tasks:ctx:ctx-abc", "task-001")
        assert score is not None

    @pytest.mark.asyncio
    async def test_adds_to_sender_set(self, task_store, redis_client):
        """save adds task_id to tasks:sender:{from_agent_id} set."""
        task = _make_task(task_id="task-001", from_agent_id="sender-xyz")
        await task_store.save(task)

        is_member = await redis_client.sismember("tasks:sender:sender-xyz", "task-001")
        assert is_member

    @pytest.mark.asyncio
    async def test_sorted_set_score_is_status_timestamp(self, task_store, redis_client):
        """Sorted set score corresponds to the task's status timestamp."""
        ts = datetime(2026, 3, 28, 12, 0, 0, tzinfo=timezone.utc)
        task = _make_task(task_id="task-001", context_id="ctx-1", timestamp=_ts(ts))
        await task_store.save(task)

        score = await redis_client.zscore("tasks:ctx:ctx-1", "task-001")
        assert score == pytest.approx(ts.timestamp(), abs=1)

    @pytest.mark.asyncio
    async def test_idempotent_save_updates_record(self, task_store, redis_client):
        """Re-saving a task updates the stored record."""
        task = _make_task(task_id="task-001", text="Original")
        await task_store.save(task)

        # Modify and re-save
        task.artifacts[0].parts[0].root.text = "Updated"
        await task_store.save(task)

        task_json = await redis_client.hget("task:task-001", "task_json")
        assert "Updated" in task_json

    @pytest.mark.asyncio
    async def test_resave_with_new_status_updates_sorted_set_score(
        self, task_store, redis_client
    ):
        """Re-saving with a new status timestamp re-scores the sorted set entry."""
        ts1 = datetime(2026, 3, 28, 12, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 3, 28, 13, 0, 0, tzinfo=timezone.utc)

        task = _make_task(task_id="task-001", context_id="ctx-1", timestamp=_ts(ts1))
        await task_store.save(task)

        score_before = await redis_client.zscore("tasks:ctx:ctx-1", "task-001")

        # Update status timestamp and re-save
        task.status = TaskStatus(state=TaskState.completed, timestamp=_ts(ts2))
        await task_store.save(task)

        score_after = await redis_client.zscore("tasks:ctx:ctx-1", "task-001")
        assert score_after > score_before
        assert score_after == pytest.approx(ts2.timestamp(), abs=1)

    @pytest.mark.asyncio
    async def test_multiple_tasks_same_context(self, task_store, redis_client):
        """Multiple tasks with the same context_id are all in the sorted set."""
        for i in range(3):
            task = _make_task(task_id=f"task-{i}", context_id="ctx-shared")
            await task_store.save(task)

        count = await redis_client.zcard("tasks:ctx:ctx-shared")
        assert count == 3

    @pytest.mark.asyncio
    async def test_multiple_tasks_same_sender(self, task_store, redis_client):
        """Multiple tasks from the same sender are all in the sender set."""
        for i in range(3):
            task = _make_task(task_id=f"task-{i}", from_agent_id="sender-multi")
            await task_store.save(task)

        count = await redis_client.scard("tasks:sender:sender-multi")
        assert count == 3

    @pytest.mark.asyncio
    async def test_broadcast_type_stored(self, task_store, redis_client):
        """Broadcast tasks store type='broadcast' in routing metadata."""
        task = _make_task(task_id="task-bc", msg_type="broadcast")
        await task_store.save(task)

        stored_type = await redis_client.hget("task:task-bc", "type")
        assert stored_type == "broadcast"


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


class TestGet:
    """Tests for RedisTaskStore.get — retrieves Task by ID."""

    @pytest.mark.asyncio
    async def test_returns_saved_task(self, task_store):
        """get returns the Task that was previously saved."""
        task = _make_task(task_id="task-001", text="Test message")
        await task_store.save(task)

        retrieved = await task_store.get("task-001")
        assert retrieved is not None
        assert retrieved.id == "task-001"

    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent(self, task_store):
        """get returns None for a task_id that does not exist."""
        result = await task_store.get("nonexistent-task-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_preserves_task_fields(self, task_store):
        """get returns a Task with all original fields preserved."""
        task = _make_task(
            task_id="task-001",
            context_id="ctx-123",
            state=TaskState.input_required,
            from_agent_id="sender-aaa",
            to_agent_id="recipient-bbb",
            text="Important message",
        )
        await task_store.save(task)

        retrieved = await task_store.get("task-001")
        assert retrieved.context_id == "ctx-123"
        assert retrieved.status.state == TaskState.input_required
        assert retrieved.metadata["fromAgentId"] == "sender-aaa"
        assert retrieved.metadata["toAgentId"] == "recipient-bbb"

    @pytest.mark.asyncio
    async def test_preserves_artifacts(self, task_store):
        """get returns a Task with artifacts intact."""
        task = _make_task(task_id="task-001", text="Check artifacts")
        await task_store.save(task)

        retrieved = await task_store.get("task-001")
        assert retrieved.artifacts is not None
        assert len(retrieved.artifacts) == 1
        assert retrieved.artifacts[0].name == "message"

    @pytest.mark.asyncio
    async def test_returns_updated_task_after_resave(self, task_store):
        """get returns the latest version after a task is re-saved."""
        task = _make_task(task_id="task-001", state=TaskState.input_required)
        await task_store.save(task)

        task.status = TaskStatus(
            state=TaskState.completed,
            timestamp=_ts(datetime.now(timezone.utc)),
        )
        await task_store.save(task)

        retrieved = await task_store.get("task-001")
        assert retrieved.status.state == TaskState.completed

    @pytest.mark.asyncio
    async def test_returns_task_type(self, task_store):
        """Retrieved task is an instance of a2a.types.Task."""
        task = _make_task(task_id="task-001")
        await task_store.save(task)

        retrieved = await task_store.get("task-001")
        assert isinstance(retrieved, Task)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    """Tests for RedisTaskStore.delete — removes Task and cleans up indexes."""

    @pytest.mark.asyncio
    async def test_removes_task_hash(self, task_store, redis_client):
        """delete removes the task:{task_id} hash from Redis."""
        task = _make_task(task_id="task-001")
        await task_store.save(task)

        await task_store.delete("task-001")

        exists = await redis_client.exists("task:task-001")
        assert not exists

    @pytest.mark.asyncio
    async def test_removes_from_context_sorted_set(self, task_store, redis_client):
        """delete removes the task_id from tasks:ctx:{context_id} sorted set."""
        task = _make_task(task_id="task-001", context_id="ctx-del")
        await task_store.save(task)

        await task_store.delete("task-001")

        score = await redis_client.zscore("tasks:ctx:ctx-del", "task-001")
        assert score is None

    @pytest.mark.asyncio
    async def test_removes_from_sender_set(self, task_store, redis_client):
        """delete removes the task_id from tasks:sender:{agent_id} set."""
        task = _make_task(task_id="task-001", from_agent_id="sender-del")
        await task_store.save(task)

        await task_store.delete("task-001")

        is_member = await redis_client.sismember("tasks:sender:sender-del", "task-001")
        assert not is_member

    @pytest.mark.asyncio
    async def test_get_returns_none_after_delete(self, task_store):
        """get returns None after a task is deleted."""
        task = _make_task(task_id="task-001")
        await task_store.save(task)
        await task_store.delete("task-001")

        result = await task_store.get("task-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_graceful(self, task_store):
        """Deleting a non-existent task does not raise an error."""
        await task_store.delete("nonexistent-task-id")
        # No exception = pass

    @pytest.mark.asyncio
    async def test_delete_does_not_affect_other_tasks(self, task_store, redis_client):
        """Deleting one task does not affect other tasks in the same context."""
        task1 = _make_task(task_id="task-001", context_id="ctx-shared")
        task2 = _make_task(task_id="task-002", context_id="ctx-shared")
        await task_store.save(task1)
        await task_store.save(task2)

        await task_store.delete("task-001")

        # task-002 still exists
        result = await task_store.get("task-002")
        assert result is not None
        score = await redis_client.zscore("tasks:ctx:ctx-shared", "task-002")
        assert score is not None


# ---------------------------------------------------------------------------
# Sorted set indexing (tasks:ctx:{context_id})
# ---------------------------------------------------------------------------


class TestContextSortedSetIndexing:
    """Tests for tasks:ctx:{context_id} sorted set behavior."""

    @pytest.mark.asyncio
    async def test_descending_timestamp_order(self, task_store, redis_client):
        """Tasks are retrievable in descending status timestamp order."""
        base = datetime(2026, 3, 28, 10, 0, 0, tzinfo=timezone.utc)

        for i in range(5):
            ts = base + timedelta(minutes=i)
            task = _make_task(
                task_id=f"task-{i:03d}",
                context_id="ctx-order",
                timestamp=_ts(ts),
            )
            await task_store.save(task)

        # ZREVRANGE returns highest scores first (descending)
        members = await redis_client.zrevrange("tasks:ctx:ctx-order", 0, -1)
        assert members == ["task-004", "task-003", "task-002", "task-001", "task-000"]

    @pytest.mark.asyncio
    async def test_score_updates_on_state_change(self, task_store, redis_client):
        """Score is updated when the task state changes (re-save)."""
        ts_initial = datetime(2026, 3, 28, 10, 0, 0, tzinfo=timezone.utc)
        ts_completed = datetime(2026, 3, 28, 11, 30, 0, tzinfo=timezone.utc)

        task = _make_task(
            task_id="task-001",
            context_id="ctx-rescore",
            state=TaskState.input_required,
            timestamp=_ts(ts_initial),
        )
        await task_store.save(task)

        task.status = TaskStatus(state=TaskState.completed, timestamp=_ts(ts_completed))
        await task_store.save(task)

        score = await redis_client.zscore("tasks:ctx:ctx-rescore", "task-001")
        assert score == pytest.approx(ts_completed.timestamp(), abs=1)

    @pytest.mark.asyncio
    async def test_different_contexts_are_independent(self, task_store, redis_client):
        """Tasks in different contexts use separate sorted sets."""
        task_a = _make_task(task_id="task-a", context_id="ctx-alpha")
        task_b = _make_task(task_id="task-b", context_id="ctx-beta")
        await task_store.save(task_a)
        await task_store.save(task_b)

        count_alpha = await redis_client.zcard("tasks:ctx:ctx-alpha")
        count_beta = await redis_client.zcard("tasks:ctx:ctx-beta")
        assert count_alpha == 1
        assert count_beta == 1


# ---------------------------------------------------------------------------
# Sender set indexing (tasks:sender:{agent_id})
# ---------------------------------------------------------------------------


class TestSenderSetIndexing:
    """Tests for tasks:sender:{agent_id} set behavior."""

    @pytest.mark.asyncio
    async def test_sender_set_tracks_all_tasks(self, task_store, redis_client):
        """All tasks from a sender are tracked in tasks:sender:{agent_id}."""
        for i in range(4):
            task = _make_task(
                task_id=f"task-{i}",
                from_agent_id="sender-track",
                to_agent_id=f"recipient-{i}",
            )
            await task_store.save(task)

        members = await redis_client.smembers("tasks:sender:sender-track")
        assert len(members) == 4
        for i in range(4):
            assert f"task-{i}" in members

    @pytest.mark.asyncio
    async def test_different_senders_are_independent(self, task_store, redis_client):
        """Each sender has their own independent set."""
        task1 = _make_task(task_id="task-1", from_agent_id="sender-one")
        task2 = _make_task(task_id="task-2", from_agent_id="sender-two")
        await task_store.save(task1)
        await task_store.save(task2)

        set_one = await redis_client.smembers("tasks:sender:sender-one")
        set_two = await redis_client.smembers("tasks:sender:sender-two")
        assert set_one == {"task-1"}
        assert set_two == {"task-2"}


# ---------------------------------------------------------------------------
# list (custom method for ListTasks queries)
# ---------------------------------------------------------------------------


class TestList:
    """Tests for RedisTaskStore.list — query tasks by context_id."""

    @pytest.mark.asyncio
    async def test_returns_tasks_for_context(self, task_store):
        """list returns all tasks matching the given context_id."""
        for i in range(3):
            task = _make_task(task_id=f"task-{i}", context_id="ctx-list")
            await task_store.save(task)

        # Also save a task with different context
        other = _make_task(task_id="task-other", context_id="ctx-other")
        await task_store.save(other)

        tasks = await task_store.list(context_id="ctx-list")
        assert len(tasks) == 3
        task_ids = {t.id for t in tasks}
        assert task_ids == {"task-0", "task-1", "task-2"}

    @pytest.mark.asyncio
    async def test_returns_empty_for_nonexistent_context(self, task_store):
        """list returns empty list for a context_id with no tasks."""
        tasks = await task_store.list(context_id="nonexistent-ctx")
        assert tasks == []

    @pytest.mark.asyncio
    async def test_descending_timestamp_order(self, task_store):
        """list returns tasks in descending status timestamp order."""
        base = datetime(2026, 3, 28, 10, 0, 0, tzinfo=timezone.utc)

        for i in range(5):
            ts = base + timedelta(minutes=i)
            task = _make_task(
                task_id=f"task-{i:03d}",
                context_id="ctx-ordered",
                timestamp=_ts(ts),
            )
            await task_store.save(task)

        tasks = await task_store.list(context_id="ctx-ordered")
        task_ids = [t.id for t in tasks]
        # Most recent first
        assert task_ids == [
            "task-004",
            "task-003",
            "task-002",
            "task-001",
            "task-000",
        ]

    @pytest.mark.asyncio
    async def test_returns_full_task_objects(self, task_store):
        """Each item returned by list is a fully deserialized Task."""
        task = _make_task(
            task_id="task-full",
            context_id="ctx-full",
            text="Full task test",
        )
        await task_store.save(task)

        tasks = await task_store.list(context_id="ctx-full")
        assert len(tasks) == 1
        t = tasks[0]
        assert isinstance(t, Task)
        assert t.id == "task-full"
        assert t.context_id == "ctx-full"
        assert t.artifacts is not None

    @pytest.mark.asyncio
    async def test_reflects_state_changes(self, task_store):
        """list returns tasks with their latest state after updates."""
        task = _make_task(
            task_id="task-update",
            context_id="ctx-state",
            state=TaskState.input_required,
        )
        await task_store.save(task)

        task.status = TaskStatus(
            state=TaskState.completed,
            timestamp=_ts(datetime.now(timezone.utc)),
        )
        await task_store.save(task)

        tasks = await task_store.list(context_id="ctx-state")
        assert len(tasks) == 1
        assert tasks[0].status.state == TaskState.completed
