"""Tests for agent auth & registration changes — API key status checks.

Covers: get_authenticated_agent with apikey:{hash} status check,
get_registration_tenant always requiring auth, create_agent with required
api_key parameter, and register_agent endpoint without-auth → 401.

Design doc reference: Step 5 — Agent Auth & Registration Changes (Server).
"""

import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from hikyaku_registry.auth import get_authenticated_agent, get_registration_tenant
from hikyaku_registry.registry_store import RegistryStore


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_TEST_API_KEY = "hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
_TEST_API_KEY_HASH = hashlib.sha256(_TEST_API_KEY.encode()).hexdigest()
_TEST_OWNER_SUB = "auth0|owner-test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
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


async def _setup_api_key(redis, api_key: str, owner_sub: str, status: str = "active"):
    """Create an apikey:{hash} record in Redis."""
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
    await redis.sadd(f"account:{owner_sub}:keys", api_key_hash)
    return api_key_hash


async def _setup_agent(
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
        "registered_at": "2026-03-29T00:00:00+00:00",
    }
    await redis.hset(f"agent:{agent_id}", mapping=record)
    await redis.sadd(f"tenant:{api_key_hash}:agents", agent_id)
    if status == "active":
        await redis.sadd("agents:active", agent_id)
    return api_key_hash


# ===========================================================================
# get_authenticated_agent — API key status check
# ===========================================================================


class TestAuthenticatedAgentKeyStatus:
    """Tests for get_authenticated_agent with apikey:{hash} status check.

    The function now checks apikey:{hash} status before authenticating.
    Active key → proceed. Revoked/missing key → 401.
    """

    @pytest.mark.asyncio
    async def test_active_key_authenticates_successfully(self, redis_client, store):
        """Active API key with valid agent returns (agent_id, tenant_id)."""
        agent_id = "agent-active-key"
        await _setup_api_key(redis_client, _TEST_API_KEY, _TEST_OWNER_SUB)
        await _setup_agent(redis_client, agent_id, _TEST_API_KEY)

        request = _make_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id=agent_id,
        )

        result = await get_authenticated_agent(request, store)

        assert result == (agent_id, _TEST_API_KEY_HASH)

    @pytest.mark.asyncio
    async def test_revoked_key_raises_401(self, redis_client, store):
        """Revoked API key raises 401 even if agent record exists and matches."""
        agent_id = "agent-revoked-key"
        await _setup_api_key(
            redis_client, _TEST_API_KEY, _TEST_OWNER_SUB, status="revoked"
        )
        await _setup_agent(redis_client, agent_id, _TEST_API_KEY)

        request = _make_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id=agent_id,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_nonexistent_key_record_raises_401(self, redis_client, store):
        """API key with no apikey:{hash} record raises 401."""
        agent_id = "agent-no-key-record"
        # Set up agent but do NOT create apikey:{hash} record
        await _setup_agent(redis_client, agent_id, _TEST_API_KEY)

        request = _make_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id=agent_id,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_key_status_checked_before_agent_lookup(self, redis_client, store):
        """Key status check happens first — revoked key fails before agent lookup."""
        # Only create key record (revoked), no agent record at all
        await _setup_api_key(
            redis_client, _TEST_API_KEY, _TEST_OWNER_SUB, status="revoked"
        )

        request = _make_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id="nonexistent-agent",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_active_key_still_validates_agent(self, redis_client, store):
        """Active key still requires valid agent_id and matching tenant."""
        await _setup_api_key(redis_client, _TEST_API_KEY, _TEST_OWNER_SUB)
        # No agent set up

        request = _make_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id="nonexistent-agent",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_active_key_wrong_agent_tenant_raises_401(self, redis_client, store):
        """Active key with agent from a different tenant raises 401."""
        other_key = "hky_ffffffffffffffffffffffffffffffff"
        await _setup_api_key(redis_client, _TEST_API_KEY, _TEST_OWNER_SUB)
        await _setup_agent(redis_client, "agent-other", other_key)

        request = _make_request(
            authorization=f"Bearer {_TEST_API_KEY}",
            x_agent_id="agent-other",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_agent(request, store)

        assert exc_info.value.status_code == 401


# ===========================================================================
# get_registration_tenant — always requires auth
# ===========================================================================


class TestRegistrationTenantRequiresAuth:
    """Tests for get_registration_tenant with mandatory auth.

    The "no auth → create new tenant" flow is removed.
    Missing Authorization header now raises 401.
    """

    @pytest.mark.asyncio
    async def test_missing_auth_raises_401(self, redis_client, store):
        """No Authorization header raises 401 (was previously allowed)."""
        request = _make_request(authorization=None)

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_active_key_returns_tuple(self, redis_client, store):
        """Valid Bearer with active apikey:{hash} returns (api_key, api_key_hash)."""
        await _setup_api_key(redis_client, _TEST_API_KEY, _TEST_OWNER_SUB)

        request = _make_request(authorization=f"Bearer {_TEST_API_KEY}")

        result = await get_registration_tenant(request, store)

        assert result == (_TEST_API_KEY, _TEST_API_KEY_HASH)

    @pytest.mark.asyncio
    async def test_revoked_key_raises_401(self, redis_client, store):
        """Revoked API key raises 401."""
        await _setup_api_key(
            redis_client, _TEST_API_KEY, _TEST_OWNER_SUB, status="revoked"
        )

        request = _make_request(authorization=f"Bearer {_TEST_API_KEY}")

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_nonexistent_key_raises_401(self, redis_client, store):
        """API key with no apikey:{hash} record raises 401."""
        request = _make_request(authorization=f"Bearer {_TEST_API_KEY}")

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_longer_returns_none(self, redis_client, store):
        """get_registration_tenant never returns None (always raises or returns tuple)."""
        # Without auth → raises
        request_no_auth = _make_request(authorization=None)
        with pytest.raises(HTTPException):
            await get_registration_tenant(request_no_auth, store)

        # With auth + active key → returns tuple
        await _setup_api_key(redis_client, _TEST_API_KEY, _TEST_OWNER_SUB)
        request_auth = _make_request(authorization=f"Bearer {_TEST_API_KEY}")
        result = await get_registration_tenant(request_auth, store)
        assert isinstance(result, tuple)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_malformed_auth_raises_401(self, redis_client, store):
        """Malformed Authorization header raises 401."""
        request = _make_request(authorization="Basic dXNlcjpwYXNz")

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_bearer_raises_401(self, redis_client, store):
        """Empty Bearer token raises 401."""
        request = _make_request(authorization="Bearer ")

        with pytest.raises(HTTPException) as exc_info:
            await get_registration_tenant(request, store)

        assert exc_info.value.status_code == 401


# ===========================================================================
# RegistryStore.create_agent — api_key required
# ===========================================================================


class TestCreateAgentApiKeyRequired:
    """Tests for RegistryStore.create_agent with required api_key.

    The api_key parameter is no longer optional — key generation logic
    is removed. Callers must always provide an API key.
    """

    @pytest.mark.asyncio
    async def test_works_with_provided_api_key(self, store):
        """create_agent with explicit api_key works correctly."""
        result = await store.create_agent(
            name="Test Agent",
            description="Test",
            api_key=_TEST_API_KEY,
        )

        assert result["api_key"] == _TEST_API_KEY
        assert "agent_id" in result
        assert "registered_at" in result

    @pytest.mark.asyncio
    async def test_no_key_generation_without_api_key(self, store):
        """create_agent without api_key raises TypeError (required param)."""
        with pytest.raises(TypeError):
            await store.create_agent(
                name="Test Agent",
                description="Test",
            )

    @pytest.mark.asyncio
    async def test_stores_correct_hash(self, store, redis_client):
        """Agent record contains SHA256 hash of the provided key."""
        result = await store.create_agent(
            name="Test Agent",
            description="Test",
            api_key=_TEST_API_KEY,
        )

        stored_hash = await redis_client.hget(
            f"agent:{result['agent_id']}", "api_key_hash"
        )
        assert stored_hash == _TEST_API_KEY_HASH

    @pytest.mark.asyncio
    async def test_adds_to_tenant_set(self, store, redis_client):
        """Agent is added to tenant:{hash}:agents set."""
        result = await store.create_agent(
            name="Test Agent",
            description="Test",
            api_key=_TEST_API_KEY,
        )

        is_member = await redis_client.sismember(
            f"tenant:{_TEST_API_KEY_HASH}:agents", result["agent_id"]
        )
        assert is_member


# ===========================================================================
# POST /api/v1/agents — registration endpoint
# ===========================================================================


class TestRegisterAgentEndpoint:
    """Tests for POST /api/v1/agents with required auth.

    Registration always requires a valid, active API key.
    No-auth registration (new tenant creation) is removed.
    """

    @pytest.fixture
    async def reg_env(self):
        """Set up test app with registry router for registration tests."""
        import fakeredis.aioredis
        from httpx import AsyncClient, ASGITransport
        from fastapi import FastAPI

        from hikyaku_registry.api.registry import (
            registry_router,
            get_registry_store,
        )

        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = RegistryStore(redis)

        app = FastAPI()
        app.include_router(registry_router, prefix="/api/v1")
        app.dependency_overrides[get_registry_store] = lambda: store

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "store": store,
                "redis": redis,
                "app": app,
            }

        await redis.aclose()

    @pytest.mark.asyncio
    async def test_no_auth_returns_401(self, reg_env):
        """POST /api/v1/agents without Authorization returns 401."""
        client = reg_env["client"]

        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Agent", "description": "Test"},
        )

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_active_key_registers_successfully(self, reg_env):
        """POST /api/v1/agents with active API key returns 201."""
        client, redis = reg_env["client"], reg_env["redis"]

        # Set up active key
        await _setup_api_key(redis, _TEST_API_KEY, _TEST_OWNER_SUB)

        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Agent", "description": "Test"},
            headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert "agent_id" in data

    @pytest.mark.asyncio
    async def test_revoked_key_returns_401(self, reg_env):
        """POST /api/v1/agents with revoked API key returns 401."""
        client, redis = reg_env["client"], reg_env["redis"]

        await _setup_api_key(redis, _TEST_API_KEY, _TEST_OWNER_SUB, status="revoked")

        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Agent", "description": "Test"},
            headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
        )

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_nonexistent_key_returns_401(self, reg_env):
        """POST /api/v1/agents with unknown API key returns 401."""
        client = reg_env["client"]

        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Agent", "description": "Test"},
            headers={"Authorization": "Bearer hky_unknownkey00000000000000000000"},
        )

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_registered_agent_in_correct_tenant(self, reg_env):
        """Agent registered with API key is placed in correct tenant set."""
        client, redis = reg_env["client"], reg_env["redis"]

        await _setup_api_key(redis, _TEST_API_KEY, _TEST_OWNER_SUB)

        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Agent", "description": "Test"},
            headers={"Authorization": f"Bearer {_TEST_API_KEY}"},
        )

        agent_id = resp.json()["agent_id"]
        is_member = await redis.sismember(
            f"tenant:{_TEST_API_KEY_HASH}:agents", agent_id
        )
        assert is_member
