"""Tests for registry_store.py — Redis Agent Store operations.

Covers: create_agent, get_agent, list_active_agents, deregister_agent, lookup_by_api_key.
Verifies Redis key schema: agent:{agent_id}, apikey:{sha256}, agents:active set.
"""

import hashlib
import json
import re
from datetime import datetime

import pytest

from hikyaku_registry.registry_store import RegistryStore

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
        result = await store.create_agent(name="Test Agent", description="A test agent")
        assert "agent_id" in result
        assert "api_key" in result

    @pytest.mark.asyncio
    async def test_returns_name_and_registered_at(self, store):
        """Registration response includes name and registered_at."""
        result = await store.create_agent(name="Test Agent", description="A test agent")
        assert result["name"] == "Test Agent"
        assert "registered_at" in result

    @pytest.mark.asyncio
    async def test_agent_id_is_valid_uuid_v4(self, store):
        """agent_id must be a valid UUID v4."""
        result = await store.create_agent(name="Test Agent", description="A test agent")
        assert UUID_V4_PATTERN.match(result["agent_id"])

    @pytest.mark.asyncio
    async def test_api_key_format(self, store):
        """api_key must have 'hky_' prefix + 32 hex characters."""
        result = await store.create_agent(name="Test Agent", description="A test agent")
        assert API_KEY_PATTERN.match(result["api_key"])

    @pytest.mark.asyncio
    async def test_stores_agent_record_in_redis(self, store, redis_client):
        """Agent record is stored at agent:{agent_id} hash in Redis."""
        result = await store.create_agent(name="Test Agent", description="A test agent")
        agent_id = result["agent_id"]

        record = await redis_client.hgetall(f"agent:{agent_id}")
        assert record is not None
        assert len(record) > 0

    @pytest.mark.asyncio
    async def test_agent_record_has_required_fields(self, store, redis_client):
        """Agent hash contains all fields from design doc."""
        result = await store.create_agent(
            name="Test Agent", description="A test agent"
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
        result = await store.create_agent(name="Test Agent", description="A test agent")
        status = await redis_client.hget(f"agent:{result['agent_id']}", "status")
        assert status == "active"

    @pytest.mark.asyncio
    async def test_stores_sha256_hash_not_raw_key(self, store, redis_client):
        """The stored api_key_hash is SHA-256 of the raw api_key."""
        result = await store.create_agent(name="Test Agent", description="A test agent")
        expected_hash = hashlib.sha256(result["api_key"].encode()).hexdigest()

        stored_hash = await redis_client.hget(
            f"agent:{result['agent_id']}", "api_key_hash"
        )
        assert stored_hash == expected_hash

    @pytest.mark.asyncio
    async def test_creates_api_key_index(self, store, redis_client):
        """apikey:{sha256} -> agent_id mapping is created in Redis."""
        result = await store.create_agent(name="Test Agent", description="A test agent")
        api_key_hash = hashlib.sha256(result["api_key"].encode()).hexdigest()

        stored_id = await redis_client.get(f"apikey:{api_key_hash}")
        assert stored_id == result["agent_id"]

    @pytest.mark.asyncio
    async def test_adds_to_active_set(self, store, redis_client):
        """agent_id is added to agents:active set."""
        result = await store.create_agent(name="Test Agent", description="A test agent")
        assert await redis_client.sismember("agents:active", result["agent_id"])

    @pytest.mark.asyncio
    async def test_registered_at_is_iso8601(self, store):
        """registered_at is a valid ISO 8601 timestamp."""
        result = await store.create_agent(name="Test Agent", description="A test agent")
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
            name="Skilled Agent", description="Has skills", skills=skills
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
        result = await store.create_agent(name="No Skills", description="Plain agent")
        agent_card_json = await redis_client.hget(
            f"agent:{result['agent_id']}", "agent_card_json"
        )
        assert agent_card_json is not None
        card = json.loads(agent_card_json)
        assert isinstance(card, dict)

    @pytest.mark.asyncio
    async def test_multiple_agents_unique_ids(self, store):
        """Each registration produces a unique agent_id."""
        r1 = await store.create_agent(name="Agent 1", description="First")
        r2 = await store.create_agent(name="Agent 2", description="Second")
        assert r1["agent_id"] != r2["agent_id"]

    @pytest.mark.asyncio
    async def test_multiple_agents_unique_api_keys(self, store):
        """Each registration produces a unique api_key."""
        r1 = await store.create_agent(name="Agent 1", description="First")
        r2 = await store.create_agent(name="Agent 2", description="Second")
        assert r1["api_key"] != r2["api_key"]

    @pytest.mark.asyncio
    async def test_multiple_agents_all_in_active_set(self, store, redis_client):
        """All registered agents appear in agents:active set."""
        ids = []
        for i in range(3):
            r = await store.create_agent(name=f"Agent {i}", description=f"Agent {i}")
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
        created = await store.create_agent(name="Test Agent", description="A test agent")
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
        created = await store.create_agent(name="Test Agent", description="A test agent")
        await store.deregister_agent(created["agent_id"])

        agent = await store.get_agent(created["agent_id"])
        assert agent is not None
        assert agent["status"] == "deregistered"

    @pytest.mark.asyncio
    async def test_includes_registered_at(self, store):
        """Returned agent record includes registered_at timestamp."""
        created = await store.create_agent(name="Test Agent", description="A test agent")
        agent = await store.get_agent(created["agent_id"])
        assert "registered_at" in agent

    @pytest.mark.asyncio
    async def test_does_not_expose_raw_api_key(self, store):
        """get_agent must not return the raw api_key (only hash is stored)."""
        created = await store.create_agent(name="Test Agent", description="A test agent")
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
        await store.create_agent(name="Agent 1", description="First")
        await store.create_agent(name="Agent 2", description="Second")

        agents = await store.list_active_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"Agent 1", "Agent 2"}

    @pytest.mark.asyncio
    async def test_excludes_deregistered_agents(self, store):
        """Deregistered agents are not included in the list."""
        r1 = await store.create_agent(name="Agent 1", description="First")
        await store.create_agent(name="Agent 2", description="Second")
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
        r1 = await store.create_agent(name="Agent 1", description="First")
        r2 = await store.create_agent(name="Agent 2", description="Second")
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
        created = await store.create_agent(name="Test Agent", description="Test")
        await store.deregister_agent(created["agent_id"])

        status = await redis_client.hget(f"agent:{created['agent_id']}", "status")
        assert status == "deregistered"

    @pytest.mark.asyncio
    async def test_records_deregistered_at_timestamp(self, store, redis_client):
        """Deregistration sets a valid deregistered_at ISO 8601 timestamp."""
        created = await store.create_agent(name="Test Agent", description="Test")
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
        created = await store.create_agent(name="Test Agent", description="Test")
        agent_id = created["agent_id"]

        assert await redis_client.sismember("agents:active", agent_id)
        await store.deregister_agent(agent_id)
        assert not await redis_client.sismember("agents:active", agent_id)

    @pytest.mark.asyncio
    async def test_deletes_api_key_index(self, store, redis_client):
        """apikey:{hash} mapping is deleted on deregistration."""
        created = await store.create_agent(name="Test Agent", description="Test")
        api_key_hash = hashlib.sha256(created["api_key"].encode()).hexdigest()

        assert await redis_client.get(f"apikey:{api_key_hash}") is not None
        await store.deregister_agent(created["agent_id"])
        assert await redis_client.get(f"apikey:{api_key_hash}") is None

    @pytest.mark.asyncio
    async def test_retains_agent_record(self, store, redis_client):
        """Agent hash record remains in Redis after deregistration (for TTL cleanup)."""
        created = await store.create_agent(name="Test Agent", description="Test")
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
        r1 = await store.create_agent(name="Agent 1", description="First")
        r2 = await store.create_agent(name="Agent 2", description="Second")

        await store.deregister_agent(r1["agent_id"])

        # Agent 2 is still active
        assert await redis_client.sismember("agents:active", r2["agent_id"])
        status = await redis_client.hget(f"agent:{r2['agent_id']}", "status")
        assert status == "active"

        # Agent 2's API key still resolves
        api_key_hash = hashlib.sha256(r2["api_key"].encode()).hexdigest()
        assert await redis_client.get(f"apikey:{api_key_hash}") == r2["agent_id"]


# ---------------------------------------------------------------------------
# lookup_by_api_key
# ---------------------------------------------------------------------------


class TestLookupByApiKey:
    """Tests for RegistryStore.lookup_by_api_key."""

    @pytest.mark.asyncio
    async def test_valid_key_returns_agent_id(self, store):
        """Returns agent_id for a valid, active API key."""
        created = await store.create_agent(name="Test Agent", description="Test")
        agent_id = await store.lookup_by_api_key(created["api_key"])
        assert agent_id == created["agent_id"]

    @pytest.mark.asyncio
    async def test_invalid_key_returns_none(self, store):
        """Returns None for an unknown API key."""
        agent_id = await store.lookup_by_api_key("hky_00000000000000000000000000000000")
        assert agent_id is None

    @pytest.mark.asyncio
    async def test_deregistered_agent_returns_none(self, store):
        """Returns None after agent deregistration (API key index is deleted)."""
        created = await store.create_agent(name="Test Agent", description="Test")
        await store.deregister_agent(created["agent_id"])

        agent_id = await store.lookup_by_api_key(created["api_key"])
        assert agent_id is None

    @pytest.mark.asyncio
    async def test_correct_agent_among_multiple(self, store):
        """Returns the correct agent_id when multiple agents are registered."""
        r1 = await store.create_agent(name="Agent 1", description="First")
        r2 = await store.create_agent(name="Agent 2", description="Second")
        r3 = await store.create_agent(name="Agent 3", description="Third")

        assert await store.lookup_by_api_key(r1["api_key"]) == r1["agent_id"]
        assert await store.lookup_by_api_key(r2["api_key"]) == r2["agent_id"]
        assert await store.lookup_by_api_key(r3["api_key"]) == r3["agent_id"]

    @pytest.mark.asyncio
    async def test_uses_sha256_hashing(self, store, redis_client):
        """The lookup correctly uses SHA-256 to hash the key before Redis lookup."""
        created = await store.create_agent(name="Test Agent", description="Test")
        api_key_hash = hashlib.sha256(created["api_key"].encode()).hexdigest()

        # Verify the key exists at the expected Redis path
        stored_id = await redis_client.get(f"apikey:{api_key_hash}")
        assert stored_id == created["agent_id"]
