"""Tests for auth.py — API key authentication dependency.

Covers: get_authenticated_agent FastAPI dependency.
Verifies Bearer token extraction, SHA-256 lookup via RegistryStore,
and HTTP 401 for all invalid/missing auth scenarios.

Also covers multi-tenant auth (get_authenticated_agent returning tuple)
and get_registration_tenant helper for join-tenant registration flow.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from hikyaku_registry.auth import get_authenticated_agent, get_registration_tenant


def _make_request(authorization: str | None = None) -> MagicMock:
    """Create a mock Request with the given Authorization header."""
    request = MagicMock()
    headers = {}
    if authorization is not None:
        headers["authorization"] = authorization
    request.headers = headers
    return request


def _make_store(lookup_result: str | None = None) -> AsyncMock:
    """Create a mock RegistryStore with lookup_by_api_key returning the given result."""
    store = AsyncMock()
    store.lookup_by_api_key = AsyncMock(return_value=lookup_result)
    return store


# ---------------------------------------------------------------------------
# Valid authentication
# ---------------------------------------------------------------------------


class TestValidAuth:
    """Tests for successful authentication."""

    @pytest.mark.asyncio
    async def test_valid_bearer_token_returns_agent_id(self):
        """Valid Bearer token resolves to the correct agent_id."""
        request = _make_request("Bearer hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
        store = _make_store(lookup_result="agent-uuid-001")

        agent_id = await get_authenticated_agent(request, store)

        assert agent_id == "agent-uuid-001"

    @pytest.mark.asyncio
    async def test_calls_lookup_with_raw_api_key(self):
        """The raw API key (not the hash) is passed to lookup_by_api_key."""
        api_key = "hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        request = _make_request(f"Bearer {api_key}")
        store = _make_store(lookup_result="agent-uuid-001")

        await get_authenticated_agent(request, store)

        store.lookup_by_api_key.assert_called_once_with(api_key)

    @pytest.mark.asyncio
    async def test_multiple_agents_correct_resolution(self):
        """Each agent's API key resolves to the correct agent_id."""
        store = AsyncMock()

        async def mock_lookup(key):
            mapping = {
                "hky_aaaa0000000000000000000000000000": "agent-aaa",
                "hky_bbbb0000000000000000000000000000": "agent-bbb",
                "hky_cccc0000000000000000000000000000": "agent-ccc",
            }
            return mapping.get(key)

        store.lookup_by_api_key = AsyncMock(side_effect=mock_lookup)

        for key_suffix, expected_id in [
            ("aaaa0000000000000000000000000000", "agent-aaa"),
            ("bbbb0000000000000000000000000000", "agent-bbb"),
            ("cccc0000000000000000000000000000", "agent-ccc"),
        ]:
            request = _make_request(f"Bearer hky_{key_suffix}")
            agent_id = await get_authenticated_agent(request, store)
            assert agent_id == expected_id


# ---------------------------------------------------------------------------
# Missing Authorization header
# ---------------------------------------------------------------------------


class TestMissingAuth:
    """Tests for missing Authorization header."""

    @pytest.mark.asyncio
    async def test_no_authorization_header_raises_401(self):
        """Missing Authorization header raises HTTP 401."""
        request = _make_request(authorization=None)
        store = _make_store()

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Malformed Authorization header
# ---------------------------------------------------------------------------


class TestMalformedAuth:
    """Tests for malformed Authorization header values."""

    @pytest.mark.asyncio
    async def test_basic_auth_instead_of_bearer_raises_401(self):
        """'Basic' scheme instead of 'Bearer' raises HTTP 401."""
        request = _make_request("Basic dXNlcjpwYXNz")
        store = _make_store()

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_scheme_raises_401(self):
        """Token without scheme prefix raises HTTP 401."""
        request = _make_request("hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
        store = _make_store()

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_bearer_token_raises_401(self):
        """'Bearer ' with empty token raises HTTP 401."""
        request = _make_request("Bearer ")
        store = _make_store()

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_bearer_lowercase_raises_401(self):
        """'bearer' (lowercase) may be rejected depending on implementation."""
        request = _make_request("bearer hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
        store = _make_store(lookup_result="agent-uuid-001")

        # Either succeeds (case-insensitive) or raises 401 (strict)
        # Test that it doesn't crash; behavior depends on implementation
        try:
            result = await get_authenticated_agent(request, store)
            # If case-insensitive, should return agent_id
            assert result == "agent-uuid-001"
        except HTTPException as exc:
            assert exc.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_authorization_header_raises_401(self):
        """Empty Authorization header value raises HTTP 401."""
        request = _make_request("")
        store = _make_store()

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Invalid API key
# ---------------------------------------------------------------------------


class TestInvalidApiKey:
    """Tests for API keys not found in Redis."""

    @pytest.mark.asyncio
    async def test_unknown_api_key_raises_401(self):
        """API key not present in Redis raises HTTP 401."""
        request = _make_request("Bearer hky_00000000000000000000000000000000")
        store = _make_store(lookup_result=None)

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_deregistered_agent_raises_401(self):
        """Deregistered agent's API key (deleted from index) raises HTTP 401."""
        request = _make_request("Bearer hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
        # lookup returns None because apikey:{hash} was deleted on deregistration
        store = _make_store(lookup_result=None)

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_store_not_called_on_malformed_header(self):
        """Store lookup is not called when the header is malformed."""
        request = _make_request("Basic bad-token")
        store = _make_store()

        with pytest.raises(HTTPException):
            await get_authenticated_agent(request, store)

        store.lookup_by_api_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_store_not_called_on_missing_header(self):
        """Store lookup is not called when the header is missing."""
        request = _make_request(authorization=None)
        store = _make_store()

        with pytest.raises(HTTPException):
            await get_authenticated_agent(request, store)

        store.lookup_by_api_key.assert_not_called()


# ===========================================================================
# Multi-tenant auth tests (access-control feature)
# ===========================================================================

# Fixed test data for deterministic tests
_TEST_API_KEY = "hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
_TEST_API_KEY_HASH = hashlib.sha256(_TEST_API_KEY.encode()).hexdigest()

_OTHER_API_KEY = "hky_ffffffffffffffffffffffffffffffff"
_OTHER_API_KEY_HASH = hashlib.sha256(_OTHER_API_KEY.encode()).hexdigest()


def _make_tenant_request(
    authorization: str | None = None,
    x_agent_id: str | None = None,
) -> MagicMock:
    """Create a mock Request with Authorization and X-Agent-Id headers."""
    request = MagicMock()
    headers = {}
    if authorization is not None:
        headers["authorization"] = authorization
    if x_agent_id is not None:
        headers["x-agent-id"] = x_agent_id
    request.headers = headers
    return request


async def _setup_tenant_agent(
    redis,
    agent_id: str,
    api_key: str,
    name: str = "test-agent",
    status: str = "active",
) -> str:
    """Set up an agent record and tenant set entry in Redis.

    Returns the api_key_hash (tenant_id).
    """
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    record = {
        "agent_id": agent_id,
        "api_key_hash": api_key_hash,
        "name": name,
        "description": "test agent",
        "agent_card_json": "{}",
        "status": status,
        "registered_at": "2026-03-28T00:00:00+00:00",
    }
    await redis.hset(f"agent:{agent_id}", mapping=record)
    await redis.sadd(f"tenant:{api_key_hash}:agents", agent_id)
    if status == "active":
        await redis.sadd("agents:active", agent_id)
    return api_key_hash


class TestMultiTenantAuth:
    """Tests for get_authenticated_agent with multi-tenant support.

    The updated function requires both Authorization and X-Agent-Id headers
    and returns a (agent_id, tenant_id) tuple.
    """

    @pytest.mark.asyncio
    async def test_valid_auth_returns_agent_id_and_tenant_id_tuple(
        self, redis_client, store
    ):
        """Valid Bearer + X-Agent-Id with matching tenant returns (agent_id, tenant_id)."""
        agent_id = "agent-tenant-001"
        tenant_id = await _setup_tenant_agent(
            redis_client, agent_id, _TEST_API_KEY
        )
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id=agent_id,
        )

        result = await get_authenticated_agent(request, store)

        assert result == (agent_id, tenant_id)

    @pytest.mark.asyncio
    async def test_tenant_id_equals_sha256_of_api_key(
        self, redis_client, store
    ):
        """The tenant_id in the returned tuple equals SHA256(api_key)."""
        agent_id = "agent-hash-check"
        await _setup_tenant_agent(redis_client, agent_id, _TEST_API_KEY)
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id=agent_id,
        )

        _, tenant_id = await get_authenticated_agent(request, store)

        assert tenant_id == _TEST_API_KEY_HASH

    @pytest.mark.asyncio
    async def test_missing_authorization_header_raises_401(self, store):
        """Missing Authorization header raises HTTP 401."""
        request = _make_tenant_request(
            authorization=None, x_agent_id="agent-001"
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_x_agent_id_header_raises_401(
        self, redis_client, store
    ):
        """Missing X-Agent-Id header raises HTTP 401."""
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id=None,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_api_key_no_matching_agent_raises_401(
        self, redis_client, store
    ):
        """API key with no matching agent record raises HTTP 401."""
        # Agent exists under a different key
        await _setup_tenant_agent(
            redis_client, "agent-other", _OTHER_API_KEY
        )
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id="agent-other",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_agent_not_in_tenant_raises_401(self, redis_client, store):
        """X-Agent-Id belongs to a different tenant than the API key → 401.

        The agent's api_key_hash doesn't match SHA256(provided_api_key).
        """
        # Agent registered under OTHER_API_KEY (different tenant)
        await _setup_tenant_agent(
            redis_client, "agent-wrong-tenant", _OTHER_API_KEY
        )
        # Request uses TEST_API_KEY but references agent in OTHER tenant
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id="agent-wrong-tenant",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_nonexistent_agent_id_raises_401(self, redis_client, store):
        """X-Agent-Id that doesn't exist at all raises HTTP 401."""
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id="agent-does-not-exist",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_authorization_basic_raises_401(self, store):
        """'Basic' scheme instead of 'Bearer' raises HTTP 401."""
        request = _make_tenant_request(
            authorization="Basic dXNlcjpwYXNz",
            x_agent_id="agent-001",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_bearer_token_raises_401(self, store):
        """'Bearer ' with empty token raises HTTP 401."""
        request = _make_tenant_request(
            authorization="Bearer ",
            x_agent_id="agent-001",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_multiple_agents_same_tenant_each_resolves(
        self, redis_client, store
    ):
        """Multiple agents sharing the same API key each authenticate correctly."""
        agent_ids = ["agent-mt-aaa", "agent-mt-bbb", "agent-mt-ccc"]
        for aid in agent_ids:
            await _setup_tenant_agent(redis_client, aid, _TEST_API_KEY)

        for aid in agent_ids:
            request = _make_tenant_request(
                authorization=f"Bearer {_TEST_API_KEY}",
                x_agent_id=aid,
            )
            result = await get_authenticated_agent(request, store)
            assert result == (aid, _TEST_API_KEY_HASH)

    @pytest.mark.asyncio
    async def test_agents_in_different_tenants_isolated(
        self, redis_client, store
    ):
        """Agents in different tenants cannot authenticate with each other's API key."""
        await _setup_tenant_agent(
            redis_client, "agent-tenant-a", _TEST_API_KEY
        )
        await _setup_tenant_agent(
            redis_client, "agent-tenant-b", _OTHER_API_KEY
        )

        # Agent A with its own key → OK
        req_a = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id="agent-tenant-a",
        )
        result = await get_authenticated_agent(req_a, store)
        assert result == ("agent-tenant-a", _TEST_API_KEY_HASH)

        # Agent B with Agent A's key → 401
        req_cross = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id="agent-tenant-b",
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(req_cross, store)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# get_registration_tenant tests
# ---------------------------------------------------------------------------


class TestRegistrationTenant:
    """Tests for get_registration_tenant helper.

    This helper extracts the optional Authorization header during registration.
    - No auth → None (create new tenant)
    - Valid auth with existing tenant → (api_key, api_key_hash)
    - Valid auth with dead/empty tenant → 401
    - Malformed auth → 401
    """

    @pytest.mark.asyncio
    async def test_no_auth_header_returns_none(self, redis_client, store):
        """No Authorization header → returns None (new tenant flow)."""
        request = _make_tenant_request(authorization=None)

        result = await get_registration_tenant(request, store)

        assert result is None

    @pytest.mark.asyncio
    async def test_valid_token_existing_tenant_returns_tuple(
        self, redis_client, store
    ):
        """Valid Bearer with existing tenant returns (api_key, api_key_hash)."""
        # Set up a tenant with at least one agent
        await _setup_tenant_agent(
            redis_client, "existing-agent", _TEST_API_KEY
        )
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}"
        )

        result = await get_registration_tenant(request, store)

        assert result == (_TEST_API_KEY, _TEST_API_KEY_HASH)

    @pytest.mark.asyncio
    async def test_valid_token_empty_tenant_raises_401(
        self, redis_client, store
    ):
        """Valid Bearer but tenant set is empty (all agents left) → 401.

        When all agents deregister, the tenant dies. The API key cannot
        be reused to join a dead tenant.
        """
        # Tenant set doesn't exist in Redis (no agents ever or all removed)
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}"
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_nonexistent_tenant_raises_401(
        self, redis_client, store
    ):
        """Valid Bearer but tenant key doesn't exist at all → 401."""
        # Use a fresh API key that was never used
        fresh_key = "hky_00000000000000000000000000000000"
        request = _make_tenant_request(
            authorization=f"Bearer {fresh_key}"
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_auth_basic_raises_401(self, redis_client, store):
        """'Basic' scheme instead of 'Bearer' raises HTTP 401."""
        request = _make_tenant_request(authorization="Basic dXNlcjpwYXNz")

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_auth_empty_token_raises_401(
        self, redis_client, store
    ):
        """'Bearer ' with empty token raises HTTP 401."""
        request = _make_tenant_request(authorization="Bearer ")

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_auth_no_scheme_raises_401(
        self, redis_client, store
    ):
        """Token without scheme prefix raises HTTP 401."""
        request = _make_tenant_request(
            authorization="hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_correct_hash_for_api_key(
        self, redis_client, store
    ):
        """The returned api_key_hash is the SHA256 hex digest of the api_key."""
        await _setup_tenant_agent(
            redis_client, "hash-check-agent", _TEST_API_KEY
        )
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}"
        )

        api_key, api_key_hash = await get_registration_tenant(request, store)

        assert api_key == _TEST_API_KEY
        expected_hash = hashlib.sha256(_TEST_API_KEY.encode()).hexdigest()
        assert api_key_hash == expected_hash
