"""Tests for deregistered agent cleanup task.

Covers: cleanup_expired_agents function that scans for expired
deregistered agents and removes their tasks, indexes, and agent records.

Also covers tenant set cleanup (access-control feature).
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import fakeredis.aioredis

from hikyaku_registry.cleanup import cleanup_expired_agents
from hikyaku_registry.registry_store import RegistryStore
from hikyaku_registry.task_store import RedisTaskStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def cleanup_env():
    """Set up stores with fakeredis for cleanup testing."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RegistryStore(redis)
    task_store = RedisTaskStore(redis)

    yield {
        "redis": redis,
        "store": store,
        "task_store": task_store,
    }

    await redis.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register_and_deregister(store, redis, name="Expired Agent", days_ago=10):
    """Register an agent, deregister it, and backdate deregistered_at."""
    result = await store.create_agent(name=name, description="Will be cleaned up")
    agent_id = result["agent_id"]
    await store.deregister_agent(agent_id)

    # Backdate the deregistered_at timestamp
    past = datetime.now(timezone.utc) - timedelta(days=days_ago)
    await redis.hset(f"agent:{agent_id}", "deregistered_at", past.isoformat())

    return result


async def _create_task_for_agent(redis, task_store, agent_id, sender_id="sender-000"):
    """Create a task record addressed to agent_id."""
    task_id = str(uuid.uuid4())

    task_json = json.dumps({
        "id": task_id,
        "contextId": agent_id,
        "status": {"state": "input-required", "timestamp": datetime.now(timezone.utc).isoformat()},
        "artifacts": [],
        "metadata": {"fromAgentId": sender_id, "toAgentId": agent_id, "type": "unicast"},
    })

    # Store task hash
    await redis.hset(f"task:{task_id}", mapping={
        "task_json": task_json,
        "from_agent_id": sender_id,
        "to_agent_id": agent_id,
        "type": "unicast",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Add to context sorted set
    await redis.zadd(
        f"tasks:ctx:{agent_id}",
        {task_id: datetime.now(timezone.utc).timestamp()},
    )

    # Add to sender set
    await redis.sadd(f"tasks:sender:{sender_id}", task_id)

    return task_id


# ---------------------------------------------------------------------------
# Cleanup: expired agents
# ---------------------------------------------------------------------------


class TestCleanupExpiredAgents:
    """Tests for cleanup of agents deregistered beyond TTL."""

    @pytest.mark.asyncio
    async def test_deletes_expired_agent_record(self, cleanup_env):
        """Expired agent's agent:{id} hash is deleted."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        result = await _register_and_deregister(store, redis, days_ago=10)
        agent_id = result["agent_id"]

        await cleanup_expired_agents(redis, ttl_days=7)

        exists = await redis.exists(f"agent:{agent_id}")
        assert not exists

    @pytest.mark.asyncio
    async def test_deletes_task_hashes(self, cleanup_env):
        """All task:{task_id} hashes for the expired agent are deleted."""
        redis, store, task_store = (
            cleanup_env["redis"], cleanup_env["store"], cleanup_env["task_store"],
        )

        result = await _register_and_deregister(store, redis, days_ago=10)
        agent_id = result["agent_id"]

        task_id_1 = await _create_task_for_agent(redis, task_store, agent_id)
        task_id_2 = await _create_task_for_agent(redis, task_store, agent_id)

        await cleanup_expired_agents(redis, ttl_days=7)

        assert not await redis.exists(f"task:{task_id_1}")
        assert not await redis.exists(f"task:{task_id_2}")

    @pytest.mark.asyncio
    async def test_deletes_context_sorted_set(self, cleanup_env):
        """The tasks:ctx:{agent_id} sorted set is deleted."""
        redis, store, task_store = (
            cleanup_env["redis"], cleanup_env["store"], cleanup_env["task_store"],
        )

        result = await _register_and_deregister(store, redis, days_ago=10)
        agent_id = result["agent_id"]

        await _create_task_for_agent(redis, task_store, agent_id)

        await cleanup_expired_agents(redis, ttl_days=7)

        exists = await redis.exists(f"tasks:ctx:{agent_id}")
        assert not exists

    @pytest.mark.asyncio
    async def test_removes_task_ids_from_sender_sets(self, cleanup_env):
        """Task IDs are removed from relevant tasks:sender:* sets."""
        redis, store, task_store = (
            cleanup_env["redis"], cleanup_env["store"], cleanup_env["task_store"],
        )

        result = await _register_and_deregister(store, redis, days_ago=10)
        agent_id = result["agent_id"]
        sender_id = "sender-cleanup"

        task_id = await _create_task_for_agent(
            redis, task_store, agent_id, sender_id=sender_id
        )

        # Verify task is in sender set before cleanup
        assert await redis.sismember(f"tasks:sender:{sender_id}", task_id)

        await cleanup_expired_agents(redis, ttl_days=7)

        assert not await redis.sismember(f"tasks:sender:{sender_id}", task_id)

    @pytest.mark.asyncio
    async def test_returns_count_of_cleaned_agents(self, cleanup_env):
        """cleanup returns the number of agents cleaned up."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        await _register_and_deregister(store, redis, name="Expired 1", days_ago=10)
        await _register_and_deregister(store, redis, name="Expired 2", days_ago=15)

        count = await cleanup_expired_agents(redis, ttl_days=7)

        assert count == 2


# ---------------------------------------------------------------------------
# Cleanup: retention period
# ---------------------------------------------------------------------------


class TestCleanupRetentionPeriod:
    """Tests for TTL-based retention before cleanup."""

    @pytest.mark.asyncio
    async def test_does_not_delete_within_retention(self, cleanup_env):
        """Agents deregistered within TTL are NOT cleaned up."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        result = await _register_and_deregister(store, redis, days_ago=3)
        agent_id = result["agent_id"]

        await cleanup_expired_agents(redis, ttl_days=7)

        # Agent record should still exist
        exists = await redis.exists(f"agent:{agent_id}")
        assert exists

    @pytest.mark.asyncio
    async def test_does_not_delete_active_agents(self, cleanup_env):
        """Active (not deregistered) agents are never cleaned up."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        result = await store.create_agent(name="Active Agent", description="Still active")
        agent_id = result["agent_id"]

        await cleanup_expired_agents(redis, ttl_days=7)

        exists = await redis.exists(f"agent:{agent_id}")
        assert exists

    @pytest.mark.asyncio
    async def test_mixed_expired_and_retained(self, cleanup_env):
        """Only expired agents are cleaned; recently deregistered are retained."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        expired = await _register_and_deregister(store, redis, name="Old", days_ago=10)
        recent = await _register_and_deregister(store, redis, name="Recent", days_ago=3)

        await cleanup_expired_agents(redis, ttl_days=7)

        assert not await redis.exists(f"agent:{expired['agent_id']}")
        assert await redis.exists(f"agent:{recent['agent_id']}")

    @pytest.mark.asyncio
    async def test_respects_custom_ttl(self, cleanup_env):
        """Cleanup respects the configured TTL value."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        # Deregistered 5 days ago
        result = await _register_and_deregister(store, redis, days_ago=5)
        agent_id = result["agent_id"]

        # With 7-day TTL: should NOT be cleaned
        await cleanup_expired_agents(redis, ttl_days=7)
        assert await redis.exists(f"agent:{agent_id}")

        # With 3-day TTL: should be cleaned
        await cleanup_expired_agents(redis, ttl_days=3)
        assert not await redis.exists(f"agent:{agent_id}")


# ---------------------------------------------------------------------------
# Cleanup: isolation
# ---------------------------------------------------------------------------


class TestCleanupIsolation:
    """Tests that cleanup does not affect unrelated data."""

    @pytest.mark.asyncio
    async def test_does_not_affect_other_agents_tasks(self, cleanup_env):
        """Cleanup of one agent does not affect tasks for other agents."""
        redis, store, task_store = (
            cleanup_env["redis"], cleanup_env["store"], cleanup_env["task_store"],
        )

        # Agent A: expired, will be cleaned
        _expired = await _register_and_deregister(store, redis, name="Expired", days_ago=10)

        # Agent B: active, has tasks
        active = await store.create_agent(name="Active Agent", description="Active")
        active_task_id = await _create_task_for_agent(
            redis, task_store, active["agent_id"]
        )

        await cleanup_expired_agents(redis, ttl_days=7)

        # Active agent's task should still exist
        assert await redis.exists(f"task:{active_task_id}")
        score = await redis.zscore(
            f"tasks:ctx:{active['agent_id']}", active_task_id
        )
        assert score is not None

    @pytest.mark.asyncio
    async def test_does_not_affect_sender_sets_of_other_tasks(self, cleanup_env):
        """Sender set entries for non-expired tasks are preserved."""
        redis, store, task_store = (
            cleanup_env["redis"], cleanup_env["store"], cleanup_env["task_store"],
        )

        sender = await store.create_agent(name="Sender", description="Sends msgs")
        sender_id = sender["agent_id"]

        # Create task for an active agent from this sender
        active = await store.create_agent(name="Active Recv", description="Active")
        active_task = await _create_task_for_agent(
            redis, task_store, active["agent_id"], sender_id=sender_id
        )

        # Create task for an expired agent from the same sender
        expired = await _register_and_deregister(store, redis, name="Expired Recv", days_ago=10)
        expired_task = await _create_task_for_agent(
            redis, task_store, expired["agent_id"], sender_id=sender_id
        )

        await cleanup_expired_agents(redis, ttl_days=7)

        # Active task still in sender set
        assert await redis.sismember(f"tasks:sender:{sender_id}", active_task)
        # Expired task removed from sender set
        assert not await redis.sismember(f"tasks:sender:{sender_id}", expired_task)


# ---------------------------------------------------------------------------
# Cleanup: no-op
# ---------------------------------------------------------------------------


class TestCleanupNoOp:
    """Tests for cleanup when there is nothing to clean."""

    @pytest.mark.asyncio
    async def test_no_agents_returns_zero(self, cleanup_env):
        """Cleanup with no agents in Redis returns 0."""
        redis = cleanup_env["redis"]

        count = await cleanup_expired_agents(redis, ttl_days=7)
        assert count == 0

    @pytest.mark.asyncio
    async def test_only_active_agents_returns_zero(self, cleanup_env):
        """Cleanup with only active agents returns 0."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        await store.create_agent(name="Active 1", description="Active")
        await store.create_agent(name="Active 2", description="Active")

        count = await cleanup_expired_agents(redis, ttl_days=7)
        assert count == 0

    @pytest.mark.asyncio
    async def test_expired_agent_with_no_tasks(self, cleanup_env):
        """Expired agent with no tasks is still cleaned up (agent record deleted)."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        result = await _register_and_deregister(store, redis, days_ago=10)
        agent_id = result["agent_id"]

        count = await cleanup_expired_agents(redis, ttl_days=7)

        assert count == 1
        assert not await redis.exists(f"agent:{agent_id}")

    @pytest.mark.asyncio
    async def test_idempotent_second_run_returns_zero(self, cleanup_env):
        """Running cleanup twice: second run returns 0 (already cleaned)."""
        redis, store, task_store = (
            cleanup_env["redis"], cleanup_env["store"], cleanup_env["task_store"],
        )

        result = await _register_and_deregister(store, redis, days_ago=10)
        await _create_task_for_agent(redis, task_store, result["agent_id"])

        first_count = await cleanup_expired_agents(redis, ttl_days=7)
        assert first_count == 1

        second_count = await cleanup_expired_agents(redis, ttl_days=7)
        assert second_count == 0


# ===========================================================================
# Cleanup: tenant set removal (access-control feature)
# ===========================================================================

_CLEANUP_TENANT_KEY = "hky_cleanupTenantKEYKEYKEYKEYKEYK"
_CLEANUP_TENANT_HASH = hashlib.sha256(_CLEANUP_TENANT_KEY.encode()).hexdigest()


async def _register_tenant_agent_and_expire(store, redis, api_key, name="Expired", days_ago=10):
    """Register an agent in a tenant, then mark as expired without full deregistration.

    Simulates an agent that needs cleanup — the agent is deregistered
    but still present in the tenant set (defensive cleanup scenario).
    """
    result = await store.create_agent(name=name, description="Will expire", api_key=api_key)
    agent_id = result["agent_id"]
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    # Manually mark as deregistered + backdate (without calling deregister_agent,
    # so the tenant set entry remains — testing cleanup's own SREM logic)
    past = datetime.now(timezone.utc) - timedelta(days=days_ago)
    await redis.hset(f"agent:{agent_id}", "status", "deregistered")
    await redis.hset(f"agent:{agent_id}", "deregistered_at", past.isoformat())
    await redis.srem("agents:active", agent_id)

    return result, api_key_hash


class TestCleanupTenantSet:
    """Tests for cleanup_expired_agents tenant set removal.

    Cleanup must also remove expired agents from their tenant:{hash}:agents set.
    """

    @pytest.mark.asyncio
    async def test_cleanup_removes_from_tenant_set(self, cleanup_env):
        """Expired agent is removed from its tenant:{hash}:agents set."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        result, tenant_hash = await _register_tenant_agent_and_expire(
            store, redis, _CLEANUP_TENANT_KEY, days_ago=10
        )
        agent_id = result["agent_id"]

        # Verify agent is still in tenant set before cleanup
        assert await redis.sismember(
            f"tenant:{tenant_hash}:agents", agent_id
        )

        await cleanup_expired_agents(redis, ttl_days=7)

        # Verify agent is removed from tenant set after cleanup
        assert not await redis.sismember(
            f"tenant:{tenant_hash}:agents", agent_id
        )

    @pytest.mark.asyncio
    async def test_cleanup_does_not_affect_other_tenant_members(
        self, cleanup_env
    ):
        """Cleanup of one agent doesn't remove other agents from the tenant set."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        # Expired agent
        expired, tenant_hash = await _register_tenant_agent_and_expire(
            store, redis, _CLEANUP_TENANT_KEY, name="Expired", days_ago=10
        )

        # Active agent in the same tenant
        active = await store.create_agent(
            name="Active", description="Still here", api_key=_CLEANUP_TENANT_KEY
        )

        await cleanup_expired_agents(redis, ttl_days=7)

        # Active agent still in tenant set
        assert await redis.sismember(
            f"tenant:{tenant_hash}:agents", active["agent_id"]
        )
        # Expired agent removed
        assert not await redis.sismember(
            f"tenant:{tenant_hash}:agents", expired["agent_id"]
        )

    @pytest.mark.asyncio
    async def test_cleanup_does_not_affect_other_tenant_sets(
        self, cleanup_env
    ):
        """Cleanup in one tenant doesn't affect a different tenant's set."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]
        other_key = "hky_otherCleanupTenantKEYKEYKEYKE"
        other_hash = hashlib.sha256(other_key.encode()).hexdigest()

        # Expired agent in tenant A
        await _register_tenant_agent_and_expire(
            store, redis, _CLEANUP_TENANT_KEY, name="Expired A", days_ago=10
        )

        # Active agent in tenant B
        active_b = await store.create_agent(
            name="Active B", description="Other tenant", api_key=other_key
        )

        await cleanup_expired_agents(redis, ttl_days=7)

        # Tenant B agent unaffected
        assert await redis.sismember(
            f"tenant:{other_hash}:agents", active_b["agent_id"]
        )

    @pytest.mark.asyncio
    async def test_cleanup_within_retention_preserves_tenant_set(
        self, cleanup_env
    ):
        """Agent within retention period is NOT removed from tenant set."""
        redis, store = cleanup_env["redis"], cleanup_env["store"]

        result, tenant_hash = await _register_tenant_agent_and_expire(
            store, redis, _CLEANUP_TENANT_KEY, name="Recent", days_ago=3
        )

        await cleanup_expired_agents(redis, ttl_days=7)

        # Still in tenant set (within retention)
        assert await redis.sismember(
            f"tenant:{tenant_hash}:agents", result["agent_id"]
        )
