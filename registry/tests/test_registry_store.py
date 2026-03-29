"""Tests for registry_store.py — Redis Agent Store operations.

Covers: create_agent, get_agent, list_active_agents, deregister_agent, lookup_by_api_key.
Verifies Redis key schema: agent:{agent_id}, apikey:{sha256}, agents:active set.
"""

import hashlib
import json
import re
from datetime import datetime

import pytest

# Default API key for tests (api_key is now required in create_agent)
_DEFAULT_API_KEY = "hky_00112233445566778899aabbccddeeff"

# UUID v4 pattern
UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# API key pattern: hky_ prefix + 32 hex characters
API_KEY_PATTERN = re.compile(r"^hky_[0-9a-f]{32}$")


# ---------------------------------------------------------------------------
# create_agent
# ---------------------------------------------------------------------------


class TestCreateAgent:
    """Tests for RegistryStore.create_agent."""

    @pytest.mark.asyncio
    async def test_returns_agent_id_and_api_key(self, store):
        """Agent registration returns agent_id and api_key."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        assert "agent_id" in result
        assert "api_key" in result

    @pytest.mark.asyncio
    async def test_returns_name_and_registered_at(self, store):
        """Registration response includes name and registered_at."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        assert result["name"] == "Test Agent"
        assert "registered_at" in result

    @pytest.mark.asyncio
    async def test_agent_id_is_valid_uuid_v4(self, store):
        """agent_id must be a valid UUID v4."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        assert UUID_V4_PATTERN.match(result["agent_id"])

    @pytest.mark.asyncio
    async def test_api_key_format(self, store):
        """api_key must have 'hky_' prefix + 32 hex characters."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        assert API_KEY_PATTERN.match(result["api_key"])

    @pytest.mark.asyncio
    async def test_stores_agent_record_in_redis(self, store, redis_client):
        """Agent record is stored at agent:{agent_id} hash in Redis."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        agent_id = result["agent_id"]

        record = await redis_client.hgetall(f"agent:{agent_id}")
        assert record is not None
        assert len(record) > 0

    @pytest.mark.asyncio
    async def test_agent_record_has_required_fields(self, store, redis_client):
        """Agent hash contains all fields from design doc."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        agent_id = result["agent_id"]
        record = await redis_client.hgetall(f"agent:{agent_id}")

        required_fields = {
            "agent_id",
            "api_key_hash",
            "name",
            "description",
            "agent_card_json",
            "status",
            "registered_at",
        }
        assert required_fields.issubset(set(record.keys()))

    @pytest.mark.asyncio
    async def test_status_is_active(self, store, redis_client):
        """New agent status is 'active'."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        status = await redis_client.hget(f"agent:{result['agent_id']}", "status")
        assert status == "active"

    @pytest.mark.asyncio
    async def test_stores_sha256_hash_not_raw_key(self, store, redis_client):
        """The stored api_key_hash is SHA-256 of the raw api_key."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        expected_hash = hashlib.sha256(result["api_key"].encode()).hexdigest()

        stored_hash = await redis_client.hget(
            f"agent:{result['agent_id']}", "api_key_hash"
        )
        assert stored_hash == expected_hash

    @pytest.mark.asyncio
    async def test_adds_to_active_set(self, store, redis_client):
        """agent_id is added to agents:active set."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        assert await redis_client.sismember("agents:active", result["agent_id"])

    @pytest.mark.asyncio
    async def test_registered_at_is_iso8601(self, store):
        """registered_at is a valid ISO 8601 timestamp."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        # Must parse without error
        dt = datetime.fromisoformat(result["registered_at"])
        assert isinstance(dt, datetime)

    @pytest.mark.asyncio
    async def test_with_skills(self, store, redis_client):
        """Agent created with skills stores them in agent_card_json."""
        skills = [
            {
                "id": "python-dev",
                "name": "Python Development",
                "description": "Writes Python code",
                "tags": ["python", "backend"],
            }
        ]
        result = await store.create_agent(
            name="Skilled Agent",
            description="Has skills",
            skills=skills,
            api_key=_DEFAULT_API_KEY,
        )
        agent_card_json = await redis_client.hget(
            f"agent:{result['agent_id']}", "agent_card_json"
        )
        assert agent_card_json is not None
        card = json.loads(agent_card_json)
        assert "skills" in card
        assert len(card["skills"]) == 1
        assert card["skills"][0]["id"] == "python-dev"

    @pytest.mark.asyncio
    async def test_without_skills(self, store, redis_client):
        """Agent created without skills still stores a valid agent_card_json."""
        result = await store.create_agent(
            name="No Skills", description="Plain agent", api_key=_DEFAULT_API_KEY
        )
        agent_card_json = await redis_client.hget(
            f"agent:{result['agent_id']}", "agent_card_json"
        )
        assert agent_card_json is not None
        card = json.loads(agent_card_json)
        assert isinstance(card, dict)

    @pytest.mark.asyncio
    async def test_multiple_agents_unique_ids(self, store):
        """Each registration produces a unique agent_id."""
        r1 = await store.create_agent(
            name="Agent 1", description="First", api_key=_DEFAULT_API_KEY
        )
        r2 = await store.create_agent(
            name="Agent 2", description="Second", api_key=_DEFAULT_API_KEY
        )
        assert r1["agent_id"] != r2["agent_id"]

    @pytest.mark.asyncio
    async def test_multiple_agents_all_in_active_set(self, store, redis_client):
        """All registered agents appear in agents:active set."""
        ids = []
        for i in range(3):
            r = await store.create_agent(
                name=f"Agent {i}", description=f"Agent {i}", api_key=_DEFAULT_API_KEY
            )
            ids.append(r["agent_id"])

        members = await redis_client.smembers("agents:active")
        for agent_id in ids:
            assert agent_id in members


# ---------------------------------------------------------------------------
# get_agent
# ---------------------------------------------------------------------------


class TestGetAgent:
    """Tests for RegistryStore.get_agent."""

    @pytest.mark.asyncio
    async def test_returns_existing_agent(self, store):
        """get_agent returns the agent record for an existing agent."""
        created = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        agent = await store.get_agent(created["agent_id"])

        assert agent is not None
        assert agent["agent_id"] == created["agent_id"]
        assert agent["name"] == "Test Agent"
        assert agent["description"] == "A test agent"
        assert agent["status"] == "active"

    @pytest.mark.asyncio
    async def test_nonexistent_returns_none(self, store):
        """get_agent returns None for a non-existent agent_id."""
        agent = await store.get_agent("00000000-0000-4000-8000-000000000000")
        assert agent is None

    @pytest.mark.asyncio
    async def test_deregistered_agent_still_returns_record(self, store):
        """get_agent returns the record for deregistered agents (store-level, not API)."""
        created = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        await store.deregister_agent(created["agent_id"])

        agent = await store.get_agent(created["agent_id"])
        assert agent is not None
        assert agent["status"] == "deregistered"

    @pytest.mark.asyncio
    async def test_includes_registered_at(self, store):
        """Returned agent record includes registered_at timestamp."""
        created = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        agent = await store.get_agent(created["agent_id"])
        assert "registered_at" in agent

    @pytest.mark.asyncio
    async def test_does_not_expose_raw_api_key(self, store):
        """get_agent must not return the raw api_key (only hash is stored)."""
        created = await store.create_agent(
            name="Test Agent", description="A test agent", api_key=_DEFAULT_API_KEY
        )
        agent = await store.get_agent(created["agent_id"])
        assert "api_key" not in agent


# ---------------------------------------------------------------------------
# list_active_agents
# ---------------------------------------------------------------------------


class TestListActiveAgents:
    """Tests for RegistryStore.list_active_agents."""

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self, store):
        """Returns empty list when no agents are registered."""
        agents = await store.list_active_agents()
        assert agents == []

    @pytest.mark.asyncio
    async def test_returns_all_active_agents(self, store):
        """Returns all agents with active status."""
        await store.create_agent(
            name="Agent 1", description="First", api_key=_DEFAULT_API_KEY
        )
        await store.create_agent(
            name="Agent 2", description="Second", api_key=_DEFAULT_API_KEY
        )

        agents = await store.list_active_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"Agent 1", "Agent 2"}

    @pytest.mark.asyncio
    async def test_excludes_deregistered_agents(self, store):
        """Deregistered agents are not included in the list."""
        r1 = await store.create_agent(
            name="Agent 1", description="First", api_key=_DEFAULT_API_KEY
        )
        await store.create_agent(
            name="Agent 2", description="Second", api_key=_DEFAULT_API_KEY
        )
        await store.deregister_agent(r1["agent_id"])

        agents = await store.list_active_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "Agent 2"

    @pytest.mark.asyncio
    async def test_each_record_has_required_fields(self, store):
        """Each agent in the list has the expected summary fields."""
        await store.create_agent(
            name="Agent",
            description="Test",
            skills=[{"id": "s1", "name": "Skill 1", "description": "s", "tags": []}],
            api_key=_DEFAULT_API_KEY,
        )
        agents = await store.list_active_agents()
        assert len(agents) == 1
        agent = agents[0]
        assert "agent_id" in agent
        assert "name" in agent
        assert "description" in agent
        assert "registered_at" in agent

    @pytest.mark.asyncio
    async def test_all_deregistered_returns_empty(self, store):
        """If all agents are deregistered, returns empty list."""
        r1 = await store.create_agent(
            name="Agent 1", description="First", api_key=_DEFAULT_API_KEY
        )
        r2 = await store.create_agent(
            name="Agent 2", description="Second", api_key=_DEFAULT_API_KEY
        )
        await store.deregister_agent(r1["agent_id"])
        await store.deregister_agent(r2["agent_id"])

        agents = await store.list_active_agents()
        assert agents == []


# ---------------------------------------------------------------------------
# deregister_agent
# ---------------------------------------------------------------------------


class TestDeregisterAgent:
    """Tests for RegistryStore.deregister_agent."""

    @pytest.mark.asyncio
    async def test_sets_status_to_deregistered(self, store, redis_client):
        """Deregistration sets agent status to 'deregistered'."""
        created = await store.create_agent(
            name="Test Agent", description="Test", api_key=_DEFAULT_API_KEY
        )
        await store.deregister_agent(created["agent_id"])

        status = await redis_client.hget(f"agent:{created['agent_id']}", "status")
        assert status == "deregistered"

    @pytest.mark.asyncio
    async def test_records_deregistered_at_timestamp(self, store, redis_client):
        """Deregistration sets a valid deregistered_at ISO 8601 timestamp."""
        created = await store.create_agent(
            name="Test Agent", description="Test", api_key=_DEFAULT_API_KEY
        )
        await store.deregister_agent(created["agent_id"])

        deregistered_at = await redis_client.hget(
            f"agent:{created['agent_id']}", "deregistered_at"
        )
        assert deregistered_at is not None
        dt = datetime.fromisoformat(deregistered_at)
        assert isinstance(dt, datetime)

    @pytest.mark.asyncio
    async def test_removes_from_active_set(self, store, redis_client):
        """agent_id is removed from agents:active set."""
        created = await store.create_agent(
            name="Test Agent", description="Test", api_key=_DEFAULT_API_KEY
        )
        agent_id = created["agent_id"]

        assert await redis_client.sismember("agents:active", agent_id)
        await store.deregister_agent(agent_id)
        assert not await redis_client.sismember("agents:active", agent_id)

    @pytest.mark.asyncio
    async def test_retains_agent_record(self, store, redis_client):
        """Agent hash record remains in Redis after deregistration (for TTL cleanup)."""
        created = await store.create_agent(
            name="Test Agent", description="Test", api_key=_DEFAULT_API_KEY
        )
        agent_id = created["agent_id"]

        await store.deregister_agent(agent_id)

        record = await redis_client.hgetall(f"agent:{agent_id}")
        assert len(record) > 0

    @pytest.mark.asyncio
    async def test_nonexistent_agent_returns_false(self, store):
        """Deregistering a non-existent agent returns False."""
        result = await store.deregister_agent("00000000-0000-4000-8000-000000000000")
        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_affect_other_agents(self, store, redis_client):
        """Deregistering one agent does not affect other agents."""
        r1 = await store.create_agent(
            name="Agent 1", description="First", api_key=_DEFAULT_API_KEY
        )
        r2 = await store.create_agent(
            name="Agent 2", description="Second", api_key=_DEFAULT_API_KEY
        )

        await store.deregister_agent(r1["agent_id"])

        # Agent 2 is still active
        assert await redis_client.sismember("agents:active", r2["agent_id"])
        status = await redis_client.hget(f"agent:{r2['agent_id']}", "status")
        assert status == "active"


# ===========================================================================
# Multi-tenant registry store tests (access-control feature)
# ===========================================================================

# Fixed test API keys for deterministic multi-tenant tests
_TENANT_A_KEY = "hky_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"
_TENANT_A_HASH = hashlib.sha256(_TENANT_A_KEY.encode()).hexdigest()

_TENANT_B_KEY = "hky_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1"
_TENANT_B_HASH = hashlib.sha256(_TENANT_B_KEY.encode()).hexdigest()


class TestCreateAgentTenant:
    """Tests for create_agent with tenant support.

    create_agent requires an api_key parameter.
    The agent is placed in the tenant set for SHA256(api_key).
    """

    @pytest.mark.asyncio
    async def test_join_tenant_reuses_provided_api_key(self, store):
        """Creating agent with api_key echoes back the provided key."""
        result = await store.create_agent(
            name="Joining Agent", description="Test", api_key=_TENANT_A_KEY
        )
        assert result["api_key"] == _TENANT_A_KEY

    @pytest.mark.asyncio
    async def test_join_tenant_adds_to_existing_tenant_set(self, store, redis_client):
        """Joining a tenant adds the new agent to the existing tenant set."""
        r1 = await store.create_agent(
            name="Agent 1", description="First", api_key=_TENANT_A_KEY
        )
        r2 = await store.create_agent(
            name="Agent 2", description="Second", api_key=_TENANT_A_KEY
        )

        members = await redis_client.smembers(f"tenant:{_TENANT_A_HASH}:agents")
        assert r1["agent_id"] in members
        assert r2["agent_id"] in members

    @pytest.mark.asyncio
    async def test_join_tenant_stores_same_api_key_hash(self, store, redis_client):
        """Agents joining the same tenant have matching api_key_hash in their records."""
        r1 = await store.create_agent(
            name="Agent 1", description="First", api_key=_TENANT_A_KEY
        )
        r2 = await store.create_agent(
            name="Agent 2", description="Second", api_key=_TENANT_A_KEY
        )

        hash1 = await redis_client.hget(f"agent:{r1['agent_id']}", "api_key_hash")
        hash2 = await redis_client.hget(f"agent:{r2['agent_id']}", "api_key_hash")
        assert hash1 == hash2 == _TENANT_A_HASH

    @pytest.mark.asyncio
    async def test_join_tenant_generates_unique_agent_id(self, store):
        """Each agent joining a tenant gets a unique agent_id."""
        r1 = await store.create_agent(
            name="Agent 1", description="First", api_key=_TENANT_A_KEY
        )
        r2 = await store.create_agent(
            name="Agent 2", description="Second", api_key=_TENANT_A_KEY
        )
        assert r1["agent_id"] != r2["agent_id"]

    @pytest.mark.asyncio
    async def test_multiple_agents_same_tenant_all_in_set(self, store, redis_client):
        """Three agents with the same api_key all appear in the tenant set."""
        ids = []
        for i in range(3):
            r = await store.create_agent(
                name=f"Agent {i}",
                description=f"Agent {i}",
                api_key=_TENANT_A_KEY,
            )
            ids.append(r["agent_id"])

        members = await redis_client.smembers(f"tenant:{_TENANT_A_HASH}:agents")
        for agent_id in ids:
            assert agent_id in members
        assert len(members) == 3

    @pytest.mark.asyncio
    async def test_different_tenants_separate_sets(self, store, redis_client):
        """Agents in different tenants have separate tenant sets."""
        r_a = await store.create_agent(
            name="Tenant A Agent", description="A", api_key=_TENANT_A_KEY
        )
        r_b = await store.create_agent(
            name="Tenant B Agent", description="B", api_key=_TENANT_B_KEY
        )

        members_a = await redis_client.smembers(f"tenant:{_TENANT_A_HASH}:agents")
        members_b = await redis_client.smembers(f"tenant:{_TENANT_B_HASH}:agents")

        assert r_a["agent_id"] in members_a
        assert r_a["agent_id"] not in members_b
        assert r_b["agent_id"] in members_b
        assert r_b["agent_id"] not in members_a

    @pytest.mark.asyncio
    async def test_join_tenant_still_adds_to_active_set(self, store, redis_client):
        """Agents joining a tenant are still added to the global agents:active set."""
        result = await store.create_agent(
            name="Agent", description="Test", api_key=_TENANT_A_KEY
        )
        assert await redis_client.sismember("agents:active", result["agent_id"])

    @pytest.mark.asyncio
    async def test_join_tenant_returns_required_fields(self, store):
        """Join-tenant response includes agent_id, api_key, name, registered_at."""
        result = await store.create_agent(
            name="Join Agent", description="Test", api_key=_TENANT_A_KEY
        )
        assert "agent_id" in result
        assert "api_key" in result
        assert "name" in result
        assert "registered_at" in result
        assert result["name"] == "Join Agent"


class TestListActiveAgentsTenant:
    """Tests for list_active_agents with tenant_id parameter.

    list_active_agents(tenant_id) queries tenant:{tenant_id}:agents
    instead of the global agents:active set.
    """

    @pytest.mark.asyncio
    async def test_returns_only_tenant_agents(self, store):
        """Returns only agents belonging to the specified tenant."""
        await store.create_agent(
            name="A1", description="Tenant A", api_key=_TENANT_A_KEY
        )
        await store.create_agent(
            name="A2", description="Tenant A", api_key=_TENANT_A_KEY
        )
        await store.create_agent(
            name="B1", description="Tenant B", api_key=_TENANT_B_KEY
        )

        agents = await store.list_active_agents(tenant_id=_TENANT_A_HASH)
        names = {a["name"] for a in agents}
        assert names == {"A1", "A2"}

    @pytest.mark.asyncio
    async def test_does_not_return_other_tenant_agents(self, store):
        """Agents from other tenants are excluded."""
        await store.create_agent(
            name="A1", description="Tenant A", api_key=_TENANT_A_KEY
        )
        await store.create_agent(
            name="B1", description="Tenant B", api_key=_TENANT_B_KEY
        )

        agents = await store.list_active_agents(tenant_id=_TENANT_B_HASH)
        assert len(agents) == 1
        assert agents[0]["name"] == "B1"

    @pytest.mark.asyncio
    async def test_empty_tenant_returns_empty_list(self, store):
        """Tenant with no agents returns empty list."""
        agents = await store.list_active_agents(tenant_id="nonexistent_tenant_hash")
        assert agents == []

    @pytest.mark.asyncio
    async def test_excludes_deregistered_agents_in_tenant(self, store):
        """Deregistered agents within a tenant are not returned."""
        r1 = await store.create_agent(
            name="A1", description="Tenant A", api_key=_TENANT_A_KEY
        )
        await store.create_agent(
            name="A2", description="Tenant A", api_key=_TENANT_A_KEY
        )
        await store.deregister_agent(r1["agent_id"])

        agents = await store.list_active_agents(tenant_id=_TENANT_A_HASH)
        assert len(agents) == 1
        assert agents[0]["name"] == "A2"

    @pytest.mark.asyncio
    async def test_each_record_has_required_fields(self, store):
        """Each agent record in tenant list has expected summary fields."""
        await store.create_agent(
            name="Agent", description="Test", api_key=_TENANT_A_KEY
        )
        agents = await store.list_active_agents(tenant_id=_TENANT_A_HASH)
        assert len(agents) == 1
        agent = agents[0]
        assert "agent_id" in agent
        assert "name" in agent
        assert "description" in agent
        assert "registered_at" in agent


class TestDeregisterAgentTenant:
    """Tests for deregister_agent tenant set cleanup.

    Deregistration must also remove the agent from tenant:{hash}:agents set.
    """

    @pytest.mark.asyncio
    async def test_removes_from_tenant_set(self, store, redis_client):
        """Deregistered agent is removed from its tenant set."""
        result = await store.create_agent(
            name="Agent", description="Test", api_key=_TENANT_A_KEY
        )
        agent_id = result["agent_id"]

        assert await redis_client.sismember(f"tenant:{_TENANT_A_HASH}:agents", agent_id)

        await store.deregister_agent(agent_id)

        assert not await redis_client.sismember(
            f"tenant:{_TENANT_A_HASH}:agents", agent_id
        )

    @pytest.mark.asyncio
    async def test_does_not_affect_other_agents_in_tenant(self, store, redis_client):
        """Deregistering one agent doesn't remove others from the tenant set."""
        r1 = await store.create_agent(
            name="Agent 1", description="First", api_key=_TENANT_A_KEY
        )
        r2 = await store.create_agent(
            name="Agent 2", description="Second", api_key=_TENANT_A_KEY
        )

        await store.deregister_agent(r1["agent_id"])

        # Agent 2 still in tenant set
        assert await redis_client.sismember(
            f"tenant:{_TENANT_A_HASH}:agents", r2["agent_id"]
        )
        # Agent 1 removed
        assert not await redis_client.sismember(
            f"tenant:{_TENANT_A_HASH}:agents", r1["agent_id"]
        )

    @pytest.mark.asyncio
    async def test_last_agent_leaves_empty_tenant_set(self, store, redis_client):
        """When the last agent deregisters, the tenant set becomes empty."""
        result = await store.create_agent(
            name="Solo Agent", description="Only one", api_key=_TENANT_A_KEY
        )
        await store.deregister_agent(result["agent_id"])

        count = await redis_client.scard(f"tenant:{_TENANT_A_HASH}:agents")
        assert count == 0

    @pytest.mark.asyncio
    async def test_does_not_affect_other_tenant_sets(self, store, redis_client):
        """Deregistering agent in tenant A doesn't affect tenant B."""
        r_a = await store.create_agent(
            name="Tenant A", description="A", api_key=_TENANT_A_KEY
        )
        r_b = await store.create_agent(
            name="Tenant B", description="B", api_key=_TENANT_B_KEY
        )

        await store.deregister_agent(r_a["agent_id"])

        assert await redis_client.sismember(
            f"tenant:{_TENANT_B_HASH}:agents", r_b["agent_id"]
        )


class TestLookupByApiKeyRemoved:
    """Tests that lookup_by_api_key method has been removed.

    The apikey:{hash} → agent_id mapping is replaced by tenant set
    membership. The method should no longer exist on RegistryStore.
    """

    @pytest.mark.asyncio
    async def test_method_does_not_exist(self, store):
        """lookup_by_api_key should not exist on RegistryStore."""
        assert not hasattr(store, "lookup_by_api_key")


class TestVerifyAgentTenant:
    """Tests for verify_agent_tenant(agent_id, tenant_id) — new method.

    Returns True if the agent's api_key_hash matches the given tenant_id.
    Returns False otherwise (agent doesn't exist or hash mismatch).
    """

    @pytest.mark.asyncio
    async def test_matching_tenant_returns_true(self, store):
        """Agent whose api_key_hash matches tenant_id → True."""
        result = await store.create_agent(
            name="Agent", description="Test", api_key=_TENANT_A_KEY
        )
        is_member = await store.verify_agent_tenant(result["agent_id"], _TENANT_A_HASH)
        assert is_member is True

    @pytest.mark.asyncio
    async def test_nonexistent_agent_returns_false(self, store):
        """Non-existent agent_id → False."""
        is_member = await store.verify_agent_tenant(
            "00000000-0000-4000-8000-000000000000", _TENANT_A_HASH
        )
        assert is_member is False

    @pytest.mark.asyncio
    async def test_wrong_tenant_returns_false(self, store):
        """Agent in a different tenant → False."""
        result = await store.create_agent(
            name="Agent", description="Test", api_key=_TENANT_A_KEY
        )
        is_member = await store.verify_agent_tenant(result["agent_id"], _TENANT_B_HASH)
        assert is_member is False

    @pytest.mark.asyncio
    async def test_deregistered_agent_still_verifiable(self, store):
        """Deregistered agent's record still has api_key_hash for verification."""
        result = await store.create_agent(
            name="Agent", description="Test", api_key=_TENANT_A_KEY
        )
        await store.deregister_agent(result["agent_id"])

        # Agent record still exists with api_key_hash, even though deregistered
        is_member = await store.verify_agent_tenant(result["agent_id"], _TENANT_A_HASH)
        assert is_member is True

    @pytest.mark.asyncio
    async def test_multiple_agents_same_tenant_all_verify(self, store):
        """All agents in a tenant verify correctly."""
        ids = []
        for i in range(3):
            r = await store.create_agent(
                name=f"Agent {i}",
                description=f"Agent {i}",
                api_key=_TENANT_A_KEY,
            )
            ids.append(r["agent_id"])

        for agent_id in ids:
            assert await store.verify_agent_tenant(agent_id, _TENANT_A_HASH) is True
            assert await store.verify_agent_tenant(agent_id, _TENANT_B_HASH) is False
