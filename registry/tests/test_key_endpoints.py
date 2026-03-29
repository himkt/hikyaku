"""Tests for key management WebUI endpoints.

Covers: GET /ui/api/auth/config, POST /ui/api/keys, GET /ui/api/keys,
DELETE /ui/api/keys/{tenant_id}.
Verifies Auth0 JWT auth, key CRUD operations, and ownership enforcement.

Design doc reference: Step 3 — Key Management Endpoints (WebUI Backend).
"""

import hashlib

import pytest
import fakeredis.aioredis
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from hikyaku_registry.webui_api import (
    webui_router,
    get_webui_store,
    get_webui_task_store,
    get_webui_executor,
)
from hikyaku_registry.auth import verify_auth0_user, get_user_id
from hikyaku_registry.registry_store import RegistryStore
from hikyaku_registry.task_store import RedisTaskStore
from hikyaku_registry.executor import BrokerExecutor


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_TEST_AUTH0_DOMAIN = "test.auth0.com"
_TEST_AUTH0_CLIENT_ID = "test-client-id"
_TEST_SUB_A = "auth0|user-aaa"
_TEST_SUB_B = "auth0|user-bbb"
_TEST_JWT_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.test.sig"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jwt_header() -> dict:
    """Build Authorization header with a fake Auth0 JWT."""
    return {"Authorization": f"Bearer {_TEST_JWT_TOKEN}"}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def key_env():
    """Set up test FastAPI app with auth dependency overrides.

    Overrides verify_auth0_user to always succeed and get_user_id to
    return _TEST_SUB_A by default. Tests can change the user by updating
    app.dependency_overrides[get_user_id].

    Yields a dict with:
      - client: httpx.AsyncClient for making requests
      - store: RegistryStore backed by fakeredis
      - task_store: RedisTaskStore backed by fakeredis
      - redis: raw fakeredis client
      - app: the FastAPI app (for changing dependency overrides)
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RegistryStore(redis)
    task_store = RedisTaskStore(redis)
    executor = BrokerExecutor(registry_store=store, task_store=task_store)

    app = FastAPI()
    app.include_router(webui_router)

    # Override store dependencies
    app.dependency_overrides[get_webui_store] = lambda: store
    app.dependency_overrides[get_webui_task_store] = lambda: task_store
    app.dependency_overrides[get_webui_executor] = lambda: executor

    # Override auth dependencies — always succeed as _TEST_SUB_A
    async def _mock_verify(request=None, cred=None):
        if request is not None:
            request.scope["auth0"] = {"sub": _TEST_SUB_A}
            request.scope["token"] = _TEST_JWT_TOKEN
        return None

    app.dependency_overrides[verify_auth0_user] = _mock_verify
    app.dependency_overrides[get_user_id] = lambda: _TEST_SUB_A

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        yield {
            "client": client,
            "store": store,
            "task_store": task_store,
            "redis": redis,
            "app": app,
        }

    await redis.aclose()


def _set_user(app: FastAPI, sub: str):
    """Change the authenticated user for subsequent requests."""

    async def _mock_verify(request=None, cred=None):
        if request is not None:
            request.scope["auth0"] = {"sub": sub}
            request.scope["token"] = _TEST_JWT_TOKEN
        return None

    app.dependency_overrides[verify_auth0_user] = _mock_verify
    app.dependency_overrides[get_user_id] = lambda: sub


# ===========================================================================
# GET /ui/api/auth/config
# ===========================================================================


class TestAuthConfig:
    """Tests for GET /ui/api/auth/config.

    Returns Auth0 domain and client_id for SPA initialization.
    No authentication required.
    """

    @pytest.mark.asyncio
    async def test_returns_200(self, key_env):
        """GET /ui/api/auth/config returns 200."""
        client = key_env["client"]

        resp = await client.get("/ui/api/auth/config")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_domain_and_client_id(self, key_env):
        """Response contains domain and client_id fields."""
        client = key_env["client"]

        resp = await client.get("/ui/api/auth/config")
        data = resp.json()

        assert "domain" in data
        assert "client_id" in data

    @pytest.mark.asyncio
    async def test_no_auth_required(self, key_env):
        """Endpoint succeeds without Authorization header."""
        app, client = key_env["app"], key_env["client"]

        # Remove auth overrides to simulate no auth
        app.dependency_overrides.pop(verify_auth0_user, None)
        app.dependency_overrides.pop(get_user_id, None)

        resp = await client.get("/ui/api/auth/config")

        assert resp.status_code == 200


# ===========================================================================
# POST /ui/api/keys
# ===========================================================================


class TestCreateKey:
    """Tests for POST /ui/api/keys.

    Creates a new API key for the authenticated user. Returns the raw
    key (shown only once), tenant_id (hash), and created_at.
    """

    @pytest.mark.asyncio
    async def test_returns_201(self, key_env):
        """POST /ui/api/keys returns 201 on success."""
        client = key_env["client"]

        resp = await client.post("/ui/api/keys", headers=_jwt_header())

        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_returns_api_key_tenant_id_and_created_at(self, key_env):
        """Response contains api_key, tenant_id, and created_at."""
        client = key_env["client"]

        resp = await client.post("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        assert "api_key" in data
        assert "tenant_id" in data
        assert "created_at" in data

    @pytest.mark.asyncio
    async def test_api_key_has_hky_prefix(self, key_env):
        """Returned api_key starts with 'hky_'."""
        client = key_env["client"]

        resp = await client.post("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        assert data["api_key"].startswith("hky_")

    @pytest.mark.asyncio
    async def test_tenant_id_is_sha256_of_api_key(self, key_env):
        """tenant_id equals SHA256(api_key)."""
        client = key_env["client"]

        resp = await client.post("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        expected_hash = hashlib.sha256(
            data["api_key"].encode()
        ).hexdigest()
        assert data["tenant_id"] == expected_hash

    @pytest.mark.asyncio
    async def test_key_stored_in_redis(self, key_env):
        """Created key has apikey:{hash} record in Redis."""
        client, redis = key_env["client"], key_env["redis"]

        resp = await client.post("/ui/api/keys", headers=_jwt_header())
        tenant_id = resp.json()["tenant_id"]

        record = await redis.hgetall(f"apikey:{tenant_id}")
        assert record["status"] == "active"
        assert record["owner_sub"] == _TEST_SUB_A

    @pytest.mark.asyncio
    async def test_key_added_to_account_set(self, key_env):
        """Created key hash is added to account:{sub}:keys set."""
        client, redis = key_env["client"], key_env["redis"]

        resp = await client.post("/ui/api/keys", headers=_jwt_header())
        tenant_id = resp.json()["tenant_id"]

        is_member = await redis.sismember(
            f"account:{_TEST_SUB_A}:keys", tenant_id
        )
        assert is_member

    @pytest.mark.asyncio
    async def test_multiple_creates_produce_unique_keys(self, key_env):
        """Multiple POST calls produce unique api_key and tenant_id."""
        client = key_env["client"]

        resp1 = await client.post("/ui/api/keys", headers=_jwt_header())
        resp2 = await client.post("/ui/api/keys", headers=_jwt_header())

        assert resp1.json()["api_key"] != resp2.json()["api_key"]
        assert resp1.json()["tenant_id"] != resp2.json()["tenant_id"]

    @pytest.mark.asyncio
    async def test_requires_auth(self, key_env):
        """POST /ui/api/keys without valid JWT returns 401."""
        app, client = key_env["app"], key_env["client"]

        # Remove auth overrides to simulate missing JWT
        app.dependency_overrides.pop(verify_auth0_user, None)
        app.dependency_overrides.pop(get_user_id, None)

        resp = await client.post("/ui/api/keys")

        assert resp.status_code == 401 or resp.status_code == 403


# ===========================================================================
# GET /ui/api/keys
# ===========================================================================


class TestListKeys:
    """Tests for GET /ui/api/keys.

    Lists all API keys owned by the authenticated user.
    """

    @pytest.mark.asyncio
    async def test_returns_200(self, key_env):
        """GET /ui/api/keys returns 200."""
        client = key_env["client"]

        resp = await client.get("/ui/api/keys", headers=_jwt_header())

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_empty_list_for_new_user(self, key_env):
        """User with no keys returns empty list."""
        client = key_env["client"]

        resp = await client.get("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        assert isinstance(data, list)
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_returns_created_keys(self, key_env):
        """Lists keys after creation."""
        client = key_env["client"]

        await client.post("/ui/api/keys", headers=_jwt_header())
        await client.post("/ui/api/keys", headers=_jwt_header())

        resp = await client.get("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_each_key_has_required_fields(self, key_env):
        """Each key in list has tenant_id, key_prefix, created_at, status, agent_count."""
        client = key_env["client"]

        await client.post("/ui/api/keys", headers=_jwt_header())

        resp = await client.get("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        assert len(data) == 1
        key = data[0]
        required_fields = {
            "tenant_id",
            "key_prefix",
            "created_at",
            "status",
            "agent_count",
        }
        assert required_fields.issubset(set(key.keys()))

    @pytest.mark.asyncio
    async def test_does_not_return_raw_key(self, key_env):
        """List response does NOT include the raw api_key."""
        client = key_env["client"]

        await client.post("/ui/api/keys", headers=_jwt_header())

        resp = await client.get("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        assert "api_key" not in data[0]

    @pytest.mark.asyncio
    async def test_key_prefix_starts_with_hky(self, key_env):
        """key_prefix starts with 'hky_' (first 8 chars of raw key)."""
        client = key_env["client"]

        await client.post("/ui/api/keys", headers=_jwt_header())

        resp = await client.get("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        assert data[0]["key_prefix"].startswith("hky_")
        assert len(data[0]["key_prefix"]) == 8

    @pytest.mark.asyncio
    async def test_agent_count_reflects_registered_agents(self, key_env):
        """agent_count matches the number of agents in the tenant."""
        client, store = key_env["client"], key_env["store"]

        create_resp = await client.post(
            "/ui/api/keys", headers=_jwt_header()
        )
        api_key = create_resp.json()["api_key"]

        # Register agents under this key
        await store.create_agent(
            name="Agent 1", description="Test", api_key=api_key
        )
        await store.create_agent(
            name="Agent 2", description="Test", api_key=api_key
        )

        resp = await client.get("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        assert data[0]["agent_count"] == 2

    @pytest.mark.asyncio
    async def test_does_not_return_other_users_keys(self, key_env):
        """Keys created by other users are not returned."""
        app, client, store = (
            key_env["app"],
            key_env["client"],
            key_env["store"],
        )

        # User A creates a key
        await client.post("/ui/api/keys", headers=_jwt_header())

        # Switch to user B
        _set_user(app, _TEST_SUB_B)

        resp = await client.get("/ui/api/keys", headers=_jwt_header())
        data = resp.json()

        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_requires_auth(self, key_env):
        """GET /ui/api/keys without valid JWT returns 401."""
        app, client = key_env["app"], key_env["client"]

        app.dependency_overrides.pop(verify_auth0_user, None)
        app.dependency_overrides.pop(get_user_id, None)

        resp = await client.get("/ui/api/keys")

        assert resp.status_code == 401 or resp.status_code == 403


# ===========================================================================
# DELETE /ui/api/keys/{tenant_id}
# ===========================================================================


class TestRevokeKey:
    """Tests for DELETE /ui/api/keys/{tenant_id}.

    Revokes an API key, deregisters all agents under the tenant.
    Requires JWT auth and ownership of the key.
    """

    @pytest.mark.asyncio
    async def test_returns_204(self, key_env):
        """DELETE /ui/api/keys/{tenant_id} returns 204 on success."""
        client = key_env["client"]

        create_resp = await client.post(
            "/ui/api/keys", headers=_jwt_header()
        )
        tenant_id = create_resp.json()["tenant_id"]

        resp = await client.delete(
            f"/ui/api/keys/{tenant_id}", headers=_jwt_header()
        )

        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_sets_key_status_to_revoked(self, key_env):
        """Revoked key has status 'revoked' in Redis."""
        client, redis = key_env["client"], key_env["redis"]

        create_resp = await client.post(
            "/ui/api/keys", headers=_jwt_header()
        )
        tenant_id = create_resp.json()["tenant_id"]

        await client.delete(
            f"/ui/api/keys/{tenant_id}", headers=_jwt_header()
        )

        status = await redis.hget(f"apikey:{tenant_id}", "status")
        assert status == "revoked"

    @pytest.mark.asyncio
    async def test_deregisters_all_tenant_agents(self, key_env):
        """All agents under the revoked key are deregistered."""
        client, store, redis = (
            key_env["client"],
            key_env["store"],
            key_env["redis"],
        )

        create_resp = await client.post(
            "/ui/api/keys", headers=_jwt_header()
        )
        data = create_resp.json()
        api_key, tenant_id = data["api_key"], data["tenant_id"]

        r1 = await store.create_agent(
            name="Agent 1", description="Test", api_key=api_key
        )
        r2 = await store.create_agent(
            name="Agent 2", description="Test", api_key=api_key
        )

        await client.delete(
            f"/ui/api/keys/{tenant_id}", headers=_jwt_header()
        )

        status1 = await redis.hget(f"agent:{r1['agent_id']}", "status")
        status2 = await redis.hget(f"agent:{r2['agent_id']}", "status")
        assert status1 == "deregistered"
        assert status2 == "deregistered"

    @pytest.mark.asyncio
    async def test_non_owned_key_returns_404(self, key_env):
        """Deleting a key not owned by the user returns 404."""
        app, client = key_env["app"], key_env["client"]

        # User A creates a key
        create_resp = await client.post(
            "/ui/api/keys", headers=_jwt_header()
        )
        tenant_id = create_resp.json()["tenant_id"]

        # Switch to user B
        _set_user(app, _TEST_SUB_B)

        resp = await client.delete(
            f"/ui/api/keys/{tenant_id}", headers=_jwt_header()
        )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_nonexistent_key_returns_404(self, key_env):
        """Deleting a non-existent tenant_id returns 404."""
        client = key_env["client"]

        resp = await client.delete(
            "/ui/api/keys/nonexistent_hash", headers=_jwt_header()
        )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_revoked_key_shows_in_list_as_revoked(self, key_env):
        """After revocation, key still appears in GET /ui/api/keys with status 'revoked'."""
        client = key_env["client"]

        create_resp = await client.post(
            "/ui/api/keys", headers=_jwt_header()
        )
        tenant_id = create_resp.json()["tenant_id"]

        await client.delete(
            f"/ui/api/keys/{tenant_id}", headers=_jwt_header()
        )

        list_resp = await client.get(
            "/ui/api/keys", headers=_jwt_header()
        )
        keys = list_resp.json()

        assert len(keys) == 1
        assert keys[0]["status"] == "revoked"
        assert keys[0]["tenant_id"] == tenant_id

    @pytest.mark.asyncio
    async def test_does_not_affect_other_keys(self, key_env):
        """Revoking one key does not affect other keys from the same user."""
        client, redis = key_env["client"], key_env["redis"]

        resp1 = await client.post("/ui/api/keys", headers=_jwt_header())
        resp2 = await client.post("/ui/api/keys", headers=_jwt_header())
        tenant_id_1 = resp1.json()["tenant_id"]
        tenant_id_2 = resp2.json()["tenant_id"]

        await client.delete(
            f"/ui/api/keys/{tenant_id_1}", headers=_jwt_header()
        )

        status = await redis.hget(f"apikey:{tenant_id_2}", "status")
        assert status == "active"

    @pytest.mark.asyncio
    async def test_requires_auth(self, key_env):
        """DELETE /ui/api/keys/{tenant_id} without valid JWT returns 401."""
        app, client, store = (
            key_env["app"],
            key_env["client"],
            key_env["store"],
        )

        # Create a key first (authenticated)
        _, api_key_hash, _ = await store.create_api_key(_TEST_SUB_A)

        # Remove auth overrides
        app.dependency_overrides.pop(verify_auth0_user, None)
        app.dependency_overrides.pop(get_user_id, None)

        resp = await client.delete(f"/ui/api/keys/{api_key_hash}")

        assert resp.status_code == 401 or resp.status_code == 403
