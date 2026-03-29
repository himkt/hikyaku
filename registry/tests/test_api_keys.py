"""Tests for API key CRUD operations on RegistryStore.

Covers: create_api_key, list_api_keys, revoke_api_key, get_api_key_status.
Verifies Redis key schema: apikey:{hash} records, account:{sub}:keys sets,
and interaction with tenant:{hash}:agents for agent_count and revocation.

Design doc reference: Step 2 — Redis Schema for API Key Records.
"""

import hashlib
import re
from datetime import datetime

import pytest


# API key pattern: hky_ prefix + 32 hex characters
API_KEY_PATTERN = re.compile(r"^hky_[0-9a-f]{32}$")

# Test Auth0 sub claims
_OWNER_SUB_A = "auth0|owner-aaa"
_OWNER_SUB_B = "auth0|owner-bbb"


# ===========================================================================
# create_api_key tests
# ===========================================================================


class TestCreateApiKey:
    """Tests for RegistryStore.create_api_key(owner_sub).

    Generates a new API key, stores apikey:{hash} record in Redis,
    and adds the hash to account:{sub}:keys set.
    """

    @pytest.mark.asyncio
    async def test_returns_api_key_hash_and_created_at(self, store):
        """Returns a tuple of (api_key, api_key_hash, created_at)."""
        result = await store.create_api_key(_OWNER_SUB_A)

        assert len(result) == 3
        api_key, api_key_hash, created_at = result
        assert api_key is not None
        assert api_key_hash is not None
        assert created_at is not None

    @pytest.mark.asyncio
    async def test_api_key_format(self, store):
        """api_key has 'hky_' prefix + 32 hex characters."""
        api_key, _, _ = await store.create_api_key(_OWNER_SUB_A)

        assert API_KEY_PATTERN.match(api_key)

    @pytest.mark.asyncio
    async def test_api_key_hash_is_sha256(self, store):
        """api_key_hash is SHA-256 hex digest of the raw api_key."""
        api_key, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        expected = hashlib.sha256(api_key.encode()).hexdigest()
        assert api_key_hash == expected

    @pytest.mark.asyncio
    async def test_created_at_is_iso8601(self, store):
        """created_at is a valid ISO 8601 timestamp."""
        _, _, created_at = await store.create_api_key(_OWNER_SUB_A)

        dt = datetime.fromisoformat(created_at)
        assert isinstance(dt, datetime)

    @pytest.mark.asyncio
    async def test_stores_apikey_record_in_redis(self, store, redis_client):
        """apikey:{hash} Redis Hash is created with correct fields."""
        api_key, api_key_hash, created_at = await store.create_api_key(_OWNER_SUB_A)

        record = await redis_client.hgetall(f"apikey:{api_key_hash}")
        assert record["owner_sub"] == _OWNER_SUB_A
        assert record["created_at"] == created_at
        assert record["status"] == "active"
        assert record["key_prefix"] == api_key[:8]

    @pytest.mark.asyncio
    async def test_key_prefix_is_first_8_chars(self, store, redis_client):
        """key_prefix stored in Redis is the first 8 characters of raw key."""
        api_key, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        key_prefix = await redis_client.hget(f"apikey:{api_key_hash}", "key_prefix")
        assert key_prefix == api_key[:8]
        assert key_prefix.startswith("hky_")
        assert len(key_prefix) == 8

    @pytest.mark.asyncio
    async def test_status_is_active(self, store, redis_client):
        """New API key has status 'active'."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        status = await redis_client.hget(f"apikey:{api_key_hash}", "status")
        assert status == "active"

    @pytest.mark.asyncio
    async def test_adds_hash_to_account_keys_set(self, store, redis_client):
        """api_key_hash is added to account:{sub}:keys set."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        is_member = await redis_client.sismember(
            f"account:{_OWNER_SUB_A}:keys", api_key_hash
        )
        assert is_member

    @pytest.mark.asyncio
    async def test_multiple_keys_same_owner_all_in_set(self, store, redis_client):
        """Multiple keys for the same owner all appear in account set."""
        hashes = []
        for _ in range(3):
            _, h, _ = await store.create_api_key(_OWNER_SUB_A)
            hashes.append(h)

        members = await redis_client.smembers(f"account:{_OWNER_SUB_A}:keys")
        for h in hashes:
            assert h in members
        assert len(members) == 3

    @pytest.mark.asyncio
    async def test_multiple_keys_unique_values(self, store):
        """Each call generates a unique api_key and api_key_hash."""
        results = [await store.create_api_key(_OWNER_SUB_A) for _ in range(3)]
        keys = [r[0] for r in results]
        hashes = [r[1] for r in results]

        assert len(set(keys)) == 3
        assert len(set(hashes)) == 3

    @pytest.mark.asyncio
    async def test_different_owners_separate_sets(self, store, redis_client):
        """Keys from different owners go into separate account sets."""
        _, hash_a, _ = await store.create_api_key(_OWNER_SUB_A)
        _, hash_b, _ = await store.create_api_key(_OWNER_SUB_B)

        members_a = await redis_client.smembers(f"account:{_OWNER_SUB_A}:keys")
        members_b = await redis_client.smembers(f"account:{_OWNER_SUB_B}:keys")

        assert hash_a in members_a
        assert hash_a not in members_b
        assert hash_b in members_b
        assert hash_b not in members_a


# ===========================================================================
# list_api_keys tests
# ===========================================================================


class TestListApiKeys:
    """Tests for RegistryStore.list_api_keys(owner_sub).

    Returns list of {tenant_id, key_prefix, created_at, status, agent_count}
    for all keys owned by the given Auth0 sub.
    """

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_unknown_user(self, store):
        """User with no keys returns empty list."""
        result = await store.list_api_keys("auth0|nonexistent")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_single_key(self, store):
        """Single key returned with correct fields."""
        api_key, api_key_hash, created_at = await store.create_api_key(_OWNER_SUB_A)

        keys = await store.list_api_keys(_OWNER_SUB_A)

        assert len(keys) == 1
        key = keys[0]
        assert key["tenant_id"] == api_key_hash
        assert key["key_prefix"] == api_key[:8]
        assert key["created_at"] == created_at
        assert key["status"] == "active"
        assert key["agent_count"] == 0

    @pytest.mark.asyncio
    async def test_returns_multiple_keys(self, store):
        """All keys for the owner are returned."""
        for _ in range(3):
            await store.create_api_key(_OWNER_SUB_A)

        keys = await store.list_api_keys(_OWNER_SUB_A)

        assert len(keys) == 3

    @pytest.mark.asyncio
    async def test_does_not_return_other_owners_keys(self, store):
        """Keys from other owners are not included."""
        await store.create_api_key(_OWNER_SUB_A)
        await store.create_api_key(_OWNER_SUB_B)

        keys_a = await store.list_api_keys(_OWNER_SUB_A)
        keys_b = await store.list_api_keys(_OWNER_SUB_B)

        assert len(keys_a) == 1
        assert len(keys_b) == 1
        assert keys_a[0]["tenant_id"] != keys_b[0]["tenant_id"]

    @pytest.mark.asyncio
    async def test_agent_count_zero_when_no_agents(self, store):
        """agent_count is 0 when no agents registered under the key."""
        await store.create_api_key(_OWNER_SUB_A)

        keys = await store.list_api_keys(_OWNER_SUB_A)

        assert keys[0]["agent_count"] == 0

    @pytest.mark.asyncio
    async def test_agent_count_reflects_registered_agents(self, store):
        """agent_count reflects the number of agents in tenant set."""
        api_key, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        # Register agents under this key's tenant
        await store.create_agent(name="Agent 1", description="Test", api_key=api_key)
        await store.create_agent(name="Agent 2", description="Test", api_key=api_key)

        keys = await store.list_api_keys(_OWNER_SUB_A)

        assert keys[0]["agent_count"] == 2

    @pytest.mark.asyncio
    async def test_each_key_has_required_fields(self, store):
        """Each key record has all required fields."""
        await store.create_api_key(_OWNER_SUB_A)

        keys = await store.list_api_keys(_OWNER_SUB_A)

        required_fields = {
            "tenant_id",
            "key_prefix",
            "created_at",
            "status",
            "agent_count",
        }
        assert required_fields.issubset(set(keys[0].keys()))

    @pytest.mark.asyncio
    async def test_includes_revoked_keys(self, store):
        """Revoked keys still appear in the list (with status 'revoked')."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)
        await store.revoke_api_key(api_key_hash, _OWNER_SUB_A)

        keys = await store.list_api_keys(_OWNER_SUB_A)

        assert len(keys) == 1
        assert keys[0]["status"] == "revoked"


# ===========================================================================
# revoke_api_key tests
# ===========================================================================


class TestRevokeApiKey:
    """Tests for RegistryStore.revoke_api_key(tenant_id, owner_sub).

    Verifies ownership, sets status to 'revoked', deregisters all agents
    under the tenant.
    """

    @pytest.mark.asyncio
    async def test_sets_status_to_revoked(self, store, redis_client):
        """Revoking a key sets apikey:{hash} status to 'revoked'."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        await store.revoke_api_key(api_key_hash, _OWNER_SUB_A)

        status = await redis_client.hget(f"apikey:{api_key_hash}", "status")
        assert status == "revoked"

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, store):
        """Successful revocation returns True."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        result = await store.revoke_api_key(api_key_hash, _OWNER_SUB_A)

        assert result is True

    @pytest.mark.asyncio
    async def test_deregisters_all_tenant_agents(self, store, redis_client):
        """All agents under the tenant are deregistered."""
        api_key, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        r1 = await store.create_agent(
            name="Agent 1", description="Test", api_key=api_key
        )
        r2 = await store.create_agent(
            name="Agent 2", description="Test", api_key=api_key
        )

        await store.revoke_api_key(api_key_hash, _OWNER_SUB_A)

        # Agents should be deregistered
        status1 = await redis_client.hget(f"agent:{r1['agent_id']}", "status")
        status2 = await redis_client.hget(f"agent:{r2['agent_id']}", "status")
        assert status1 == "deregistered"
        assert status2 == "deregistered"

    @pytest.mark.asyncio
    async def test_deregistered_agents_removed_from_active_set(
        self, store, redis_client
    ):
        """Deregistered agents are removed from agents:active set."""
        api_key, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        r1 = await store.create_agent(
            name="Agent 1", description="Test", api_key=api_key
        )

        await store.revoke_api_key(api_key_hash, _OWNER_SUB_A)

        is_active = await redis_client.sismember("agents:active", r1["agent_id"])
        assert not is_active

    @pytest.mark.asyncio
    async def test_non_owned_key_fails(self, store):
        """Revoking a key not owned by the caller fails."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        result = await store.revoke_api_key(api_key_hash, _OWNER_SUB_B)

        assert result is False

    @pytest.mark.asyncio
    async def test_nonexistent_key_fails(self, store):
        """Revoking a non-existent key fails."""
        result = await store.revoke_api_key("nonexistent_hash_value", _OWNER_SUB_A)

        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_affect_other_tenants(self, store, redis_client):
        """Revoking one key does not affect agents in other tenants."""
        api_key_a, hash_a, _ = await store.create_api_key(_OWNER_SUB_A)
        api_key_b, hash_b, _ = await store.create_api_key(_OWNER_SUB_A)

        await store.create_agent(name="Agent A", description="Test", api_key=api_key_a)
        r_b = await store.create_agent(
            name="Agent B", description="Test", api_key=api_key_b
        )

        await store.revoke_api_key(hash_a, _OWNER_SUB_A)

        # Agent B should still be active
        status = await redis_client.hget(f"agent:{r_b['agent_id']}", "status")
        assert status == "active"

    @pytest.mark.asyncio
    async def test_revoke_with_no_agents_succeeds(self, store, redis_client):
        """Revoking a key with no registered agents still succeeds."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        result = await store.revoke_api_key(api_key_hash, _OWNER_SUB_A)

        assert result is True
        status = await redis_client.hget(f"apikey:{api_key_hash}", "status")
        assert status == "revoked"

    @pytest.mark.asyncio
    async def test_key_remains_in_account_set_after_revoke(self, store, redis_client):
        """Revoked key hash stays in account:{sub}:keys (for listing)."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        await store.revoke_api_key(api_key_hash, _OWNER_SUB_A)

        is_member = await redis_client.sismember(
            f"account:{_OWNER_SUB_A}:keys", api_key_hash
        )
        assert is_member


# ===========================================================================
# get_api_key_status tests
# ===========================================================================


class TestGetApiKeyStatus:
    """Tests for RegistryStore.get_api_key_status(tenant_id).

    Returns the status string from apikey:{hash}, or None if not found.
    """

    @pytest.mark.asyncio
    async def test_active_key_returns_active(self, store):
        """Active API key returns 'active'."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)

        status = await store.get_api_key_status(api_key_hash)

        assert status == "active"

    @pytest.mark.asyncio
    async def test_revoked_key_returns_revoked(self, store):
        """Revoked API key returns 'revoked'."""
        _, api_key_hash, _ = await store.create_api_key(_OWNER_SUB_A)
        await store.revoke_api_key(api_key_hash, _OWNER_SUB_A)

        status = await store.get_api_key_status(api_key_hash)

        assert status == "revoked"

    @pytest.mark.asyncio
    async def test_nonexistent_key_returns_none(self, store):
        """Non-existent key returns None."""
        status = await store.get_api_key_status("nonexistent_hash")

        assert status is None
