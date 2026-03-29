"""Tests for auth.py — API key authentication dependency.

Covers: get_authenticated_agent FastAPI dependency.
Verifies Bearer token extraction, SHA-256 lookup via RegistryStore,
and HTTP 401 for all invalid/missing auth scenarios.

Also covers multi-tenant auth (get_authenticated_agent returning tuple)
and get_registration_tenant helper for registration flow (always requires auth).
"""

import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from hikyaku_registry.auth import get_authenticated_agent, get_registration_tenant


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


async def _setup_api_key(
    redis,
    api_key: str,
    owner_sub: str = "auth0|test-owner",
    status: str = "active",
) -> str:
    """Create an apikey:{hash} record in Redis. Returns api_key_hash."""
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    await redis.hset(
        f"apikey:{api_key_hash}",
        mapping={
            "owner_sub": owner_sub,
            "created_at": "2026-03-29T00:00:00+00:00",
            "status": status,
            "key_prefix": api_key[:8],
        },
    )
    return api_key_hash


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
        await _setup_api_key(redis_client, _TEST_API_KEY)
        tenant_id = await _setup_tenant_agent(redis_client, agent_id, _TEST_API_KEY)
        request = _make_tenant_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id=agent_id,
        )

        result = await get_authenticated_agent(request, store)

        assert result == (agent_id, tenant_id)

    @pytest.mark.asyncio
    async def test_tenant_id_equals_sha256_of_api_key(self, redis_client, store):
        """The tenant_id in the returned tuple equals SHA256(api_key)."""
        agent_id = "agent-hash-check"
        await _setup_api_key(redis_client, _TEST_API_KEY)
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
        request = _make_tenant_request(authorization=None, x_agent_id="agent-001")

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_x_agent_id_header_raises_401(self, redis_client, store):
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
        await _setup_tenant_agent(redis_client, "agent-other", _OTHER_API_KEY)
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
        await _setup_tenant_agent(redis_client, "agent-wrong-tenant", _OTHER_API_KEY)
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
    async def test_multiple_agents_same_tenant_each_resolves(self, redis_client, store):
        """Multiple agents sharing the same API key each authenticate correctly."""
        await _setup_api_key(redis_client, _TEST_API_KEY)
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
    async def test_agents_in_different_tenants_isolated(self, redis_client, store):
        """Agents in different tenants cannot authenticate with each other's API key."""
        await _setup_api_key(redis_client, _TEST_API_KEY)
        await _setup_api_key(redis_client, _OTHER_API_KEY)
        await _setup_tenant_agent(redis_client, "agent-tenant-a", _TEST_API_KEY)
        await _setup_tenant_agent(redis_client, "agent-tenant-b", _OTHER_API_KEY)

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

    This helper always requires a valid, active API key for registration.
    - No auth → 401
    - Valid auth with active key → (api_key, api_key_hash)
    - Valid auth with revoked/missing key → 401
    - Malformed auth → 401
    """

    @pytest.mark.asyncio
    async def test_valid_token_existing_tenant_returns_tuple(self, redis_client, store):
        """Valid Bearer with active apikey record returns (api_key, api_key_hash)."""
        await _setup_api_key(redis_client, _TEST_API_KEY)
        request = _make_tenant_request(authorization=f"Bearer {_TEST_API_KEY}")

        result = await get_registration_tenant(request, store)

        assert result == (_TEST_API_KEY, _TEST_API_KEY_HASH)

    @pytest.mark.asyncio
    async def test_valid_token_empty_tenant_raises_401(self, redis_client, store):
        """Valid Bearer but no apikey record → 401.

        An API key without an apikey:{hash} record is not recognized.
        """
        # No apikey record in Redis
        request = _make_tenant_request(authorization=f"Bearer {_TEST_API_KEY}")

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_nonexistent_tenant_raises_401(self, redis_client, store):
        """Valid Bearer but tenant key doesn't exist at all → 401."""
        # Use a fresh API key that was never used
        fresh_key = "hky_00000000000000000000000000000000"
        request = _make_tenant_request(authorization=f"Bearer {fresh_key}")

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
    async def test_malformed_auth_empty_token_raises_401(self, redis_client, store):
        """'Bearer ' with empty token raises HTTP 401."""
        request = _make_tenant_request(authorization="Bearer ")

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_auth_no_scheme_raises_401(self, redis_client, store):
        """Token without scheme prefix raises HTTP 401."""
        request = _make_tenant_request(
            authorization="hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_correct_hash_for_api_key(self, redis_client, store):
        """The returned api_key_hash is the SHA256 hex digest of the api_key."""
        await _setup_api_key(redis_client, _TEST_API_KEY)
        request = _make_tenant_request(authorization=f"Bearer {_TEST_API_KEY}")

        api_key, api_key_hash = await get_registration_tenant(request, store)

        assert api_key == _TEST_API_KEY
        expected_hash = hashlib.sha256(_TEST_API_KEY.encode()).hexdigest()
        assert api_key_hash == expected_hash
