"""Tests for api/registry.py — Registry REST API endpoints.

Covers: POST /api/v1/agents, GET /api/v1/agents, GET /api/v1/agents/{id},
DELETE /api/v1/agents/{id}.
Tests authentication requirements, ownership checks, and error responses.

Also covers tenant-scoped registration, list, and get operations
(access-control feature).
"""

import hashlib

import pytest
import fakeredis.aioredis
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI, HTTPException

from hikyaku_registry.api.registry import registry_router
from hikyaku_registry.registry_store import RegistryStore
from hikyaku_registry.auth import get_authenticated_agent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_env():
    """Set up test FastAPI app with registry router and fakeredis store.

    Yields a dict with:
      - client: httpx.AsyncClient for making requests
      - store: RegistryStore backed by fakeredis
      - app: the FastAPI app (for dependency overrides)
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RegistryStore(redis)

    app = FastAPI()
    app.include_router(registry_router, prefix="/api/v1")

    # Make the store available to the router.
    # The router is expected to use a dependency (e.g. get_registry_store).
    # We override it here so the router uses our fakeredis-backed store.
    from hikyaku_registry.api.registry import get_registry_store

    app.dependency_overrides[get_registry_store] = lambda: store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "store": store, "app": app}

    await redis.aclose()


async def _register_agent(client, name="Test Agent", description="A test agent", skills=None):
    """Helper: register an agent via POST and return the response data."""
    body = {"name": name, "description": description}
    if skills is not None:
        body["skills"] = skills
    resp = await client.post("/api/v1/agents", json=body)
    assert resp.status_code == 201
    return resp.json()


def _auth_header(api_key: str) -> dict:
    """Build Authorization header dict."""
    return {"Authorization": f"Bearer {api_key}"}


def _override_auth_as(app: FastAPI, agent_id: str):
    """Override auth dependency to return a fixed (agent_id, tenant_id) tuple."""

    async def _fixed_auth():
        return (agent_id, "test-default-tenant")

    app.dependency_overrides[get_authenticated_agent] = _fixed_auth


def _override_auth_deny(app: FastAPI):
    """Override auth dependency to always raise HTTP 401."""

    async def _deny_auth():
        raise HTTPException(status_code=401, detail="Unauthorized")

    app.dependency_overrides[get_authenticated_agent] = _deny_auth


# ---------------------------------------------------------------------------
# POST /api/v1/agents — Register Agent (no auth)
# ---------------------------------------------------------------------------


class TestRegisterAgent:
    """Tests for POST /api/v1/agents."""

    @pytest.mark.asyncio
    async def test_register_returns_201(self, api_env):
        """Successful registration returns HTTP 201."""
        client = api_env["client"]
        resp = await client.post(
            "/api/v1/agents",
            json={"name": "My Agent", "description": "A coding agent"},
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_register_returns_agent_id_and_api_key(self, api_env):
        """Response contains agent_id, api_key, name, registered_at."""
        client = api_env["client"]
        data = await _register_agent(client, name="My Agent", description="Coder")

        assert "agent_id" in data
        assert "api_key" in data
        assert data["name"] == "My Agent"
        assert "registered_at" in data

    @pytest.mark.asyncio
    async def test_register_api_key_format(self, api_env):
        """api_key has hky_ prefix + 32 hex characters."""
        client = api_env["client"]
        data = await _register_agent(client)

        assert data["api_key"].startswith("hky_")
        assert len(data["api_key"]) == 36  # "hky_" (4) + 32 hex

    @pytest.mark.asyncio
    async def test_register_with_skills(self, api_env):
        """Registration with skills succeeds."""
        client = api_env["client"]
        skills = [
            {
                "id": "python-dev",
                "name": "Python Development",
                "description": "Writes Python code",
                "tags": ["python", "backend"],
            }
        ]
        data = await _register_agent(client, skills=skills)
        assert "agent_id" in data

    @pytest.mark.asyncio
    async def test_register_without_skills(self, api_env):
        """Registration without skills succeeds (skills is optional)."""
        client = api_env["client"]
        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Plain Agent", "description": "No skills"},
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_register_missing_name_returns_error(self, api_env):
        """Missing name field returns 400 or 422."""
        client = api_env["client"]
        resp = await client.post(
            "/api/v1/agents",
            json={"description": "No name provided"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_register_missing_description_returns_error(self, api_env):
        """Missing description field returns 400 or 422."""
        client = api_env["client"]
        resp = await client.post(
            "/api/v1/agents",
            json={"name": "No Description Agent"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_register_empty_body_returns_error(self, api_env):
        """Empty request body returns 400 or 422."""
        client = api_env["client"]
        resp = await client.post("/api/v1/agents", json={})
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_register_no_auth_required(self, api_env):
        """Registration does not require authentication."""
        client = api_env["client"]
        # No Authorization header
        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Open Agent", "description": "No auth needed"},
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_register_multiple_agents_unique_ids(self, api_env):
        """Each registration produces a unique agent_id."""
        client = api_env["client"]
        data1 = await _register_agent(client, name="Agent 1")
        data2 = await _register_agent(client, name="Agent 2")
        assert data1["agent_id"] != data2["agent_id"]


# ---------------------------------------------------------------------------
# GET /api/v1/agents — List Agents (auth required)
# ---------------------------------------------------------------------------


class TestListAgents:
    """Tests for GET /api/v1/agents."""

    @pytest.mark.asyncio
    async def test_list_returns_registered_agents(self, api_env):
        """Returns all registered agents in the agents array."""
        client, app, store = api_env["client"], api_env["app"], api_env["store"]

        # Register two agents in the same tenant
        key = "hky_testListAgentsKEYKEYKEYKEYKEYK"
        tenant_hash = hashlib.sha256(key.encode()).hexdigest()
        r1 = await store.create_agent(name="Agent 1", description="Test", api_key=key)
        r2 = await store.create_agent(name="Agent 2", description="Test", api_key=key)

        _override_auth_as_tenant(app, r1["agent_id"], tenant_hash)

        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200

        data = resp.json()
        assert "agents" in data
        names = {a["name"] for a in data["agents"]}
        assert "Agent 1" in names
        assert "Agent 2" in names

    @pytest.mark.asyncio
    async def test_list_empty_returns_empty_array(self, api_env):
        """Returns empty agents array when no agents registered."""
        client, app = api_env["client"], api_env["app"]

        # Override auth since no agents exist to provide a real key
        _override_auth_as(app, "any-agent-id")

        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200

        data = resp.json()
        assert data["agents"] == []

    @pytest.mark.asyncio
    async def test_list_agent_has_required_fields(self, api_env):
        """Each agent in the list has agent_id, name, description, registered_at."""
        client, app, store = api_env["client"], api_env["app"], api_env["store"]

        key = "hky_testListFieldsKEYKEYKEYKEYKEYK"
        tenant_hash = hashlib.sha256(key.encode()).hexdigest()
        r = await store.create_agent(
            name="Detailed Agent",
            description="Has all fields",
            skills=[{"id": "s1", "name": "Skill 1", "description": "A skill", "tags": []}],
            api_key=key,
        )
        _override_auth_as_tenant(app, r["agent_id"], tenant_hash)

        resp = await client.get("/api/v1/agents")
        data = resp.json()
        agent = data["agents"][0]

        assert "agent_id" in agent
        assert "name" in agent
        assert "description" in agent
        assert "registered_at" in agent

    @pytest.mark.asyncio
    async def test_list_excludes_deregistered(self, api_env):
        """Deregistered agents are not included in the list."""
        client, app, store = api_env["client"], api_env["app"], api_env["store"]

        key = "hky_testListExcludeKEYKEYKEYKEYKEYK"
        tenant_hash = hashlib.sha256(key.encode()).hexdigest()
        r1 = await store.create_agent(name="Active Agent", description="Test", api_key=key)
        r2 = await store.create_agent(name="Gone Agent", description="Test", api_key=key)

        # Deregister one agent via store
        await store.deregister_agent(r2["agent_id"])

        _override_auth_as_tenant(app, r1["agent_id"], tenant_hash)

        resp = await client.get("/api/v1/agents")
        data = resp.json()
        names = {a["name"] for a in data["agents"]}
        assert "Active Agent" in names
        assert "Gone Agent" not in names

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, api_env):
        """GET /api/v1/agents without auth returns 401."""
        client, app = api_env["client"], api_env["app"]
        _override_auth_deny(app)

        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/agents/{agent_id} — Get Agent Detail (auth required)
# ---------------------------------------------------------------------------


class TestGetAgentDetail:
    """Tests for GET /api/v1/agents/{agent_id}."""

    @pytest.mark.asyncio
    async def test_get_existing_agent(self, api_env):
        """Returns agent detail for a registered agent."""
        client, app, store = api_env["client"], api_env["app"], api_env["store"]

        key = "hky_testGetExistingKEYKEYKEYKEYKEYK"
        tenant_hash = hashlib.sha256(key.encode()).hexdigest()
        r = await store.create_agent(name="Detail Agent", description="Full detail", api_key=key)
        _override_auth_as_tenant(app, r["agent_id"], tenant_hash)

        resp = await client.get(f"/api/v1/agents/{r['agent_id']}")
        assert resp.status_code == 200

        data = resp.json()
        assert data["name"] == "Detail Agent"

    @pytest.mark.asyncio
    async def test_get_unknown_agent_returns_404(self, api_env):
        """Returns 404 for non-existent agent_id."""
        client, app = api_env["client"], api_env["app"]
        _override_auth_as(app, "some-authenticated-agent")

        resp = await client.get("/api/v1/agents/00000000-0000-4000-8000-000000000000")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_unknown_agent_error_format(self, api_env):
        """404 response uses the standard error format."""
        client, app = api_env["client"], api_env["app"]
        _override_auth_as(app, "some-authenticated-agent")

        resp = await client.get("/api/v1/agents/00000000-0000-4000-8000-000000000000")
        data = resp.json()

        assert "error" in data
        assert "code" in data["error"]
        assert data["error"]["code"] == "AGENT_NOT_FOUND"
        assert "message" in data["error"]

    @pytest.mark.asyncio
    async def test_get_deregistered_agent_returns_404(self, api_env):
        """Returns 404 for a deregistered agent."""
        client, app, store = api_env["client"], api_env["app"], api_env["store"]

        r = await _register_agent(client, name="Soon Gone")
        await store.deregister_agent(r["agent_id"])

        _override_auth_as(app, "some-authenticated-agent")

        resp = await client.get(f"/api/v1/agents/{r['agent_id']}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_requires_auth(self, api_env):
        """GET /api/v1/agents/{id} without auth returns 401."""
        client, app = api_env["client"], api_env["app"]

        r = await _register_agent(client, name="Auth Test Agent")
        _override_auth_deny(app)

        resp = await client.get(f"/api/v1/agents/{r['agent_id']}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/agents/{agent_id} — Deregister (auth + ownership)
# ---------------------------------------------------------------------------


class TestDeregisterAgent:
    """Tests for DELETE /api/v1/agents/{agent_id}."""

    @pytest.mark.asyncio
    async def test_owner_can_deregister(self, api_env):
        """Agent can deregister itself — returns 204."""
        client, app = api_env["client"], api_env["app"]

        r = await _register_agent(client, name="Self Delete")
        _override_auth_as(app, r["agent_id"])

        resp = await client.delete(f"/api/v1/agents/{r['agent_id']}")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_deregistered_agent_removed_from_list(self, api_env):
        """After deregistration, agent no longer appears in GET /agents list."""
        client, app, store = api_env["client"], api_env["app"], api_env["store"]

        key = "hky_testDeregListKEYKEYKEYKEYKEYKEY"
        tenant_hash = hashlib.sha256(key.encode()).hexdigest()
        r1 = await store.create_agent(name="Keeper", description="Test", api_key=key)
        r2 = await store.create_agent(name="Leaver", description="Test", api_key=key)

        # Deregister r2
        _override_auth_as_tenant(app, r2["agent_id"], tenant_hash)
        await client.delete(f"/api/v1/agents/{r2['agent_id']}")

        # List agents as r1
        _override_auth_as_tenant(app, r1["agent_id"], tenant_hash)
        resp = await client.get("/api/v1/agents")
        names = {a["name"] for a in resp.json()["agents"]}
        assert "Leaver" not in names
        assert "Keeper" in names

    @pytest.mark.asyncio
    async def test_non_owner_gets_403(self, api_env):
        """Deleting another agent's registration returns 403."""
        client, app = api_env["client"], api_env["app"]

        r1 = await _register_agent(client, name="Agent 1")
        r2 = await _register_agent(client, name="Agent 2")

        # Authenticate as r1, try to delete r2
        _override_auth_as(app, r1["agent_id"])

        resp = await client.delete(f"/api/v1/agents/{r2['agent_id']}")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_non_owner_error_format(self, api_env):
        """403 response uses the standard error format."""
        client, app = api_env["client"], api_env["app"]

        r1 = await _register_agent(client, name="Agent A")
        r2 = await _register_agent(client, name="Agent B")

        _override_auth_as(app, r1["agent_id"])

        resp = await client.delete(f"/api/v1/agents/{r2['agent_id']}")
        data = resp.json()

        assert "error" in data
        assert data["error"]["code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_delete_unknown_agent_returns_404(self, api_env):
        """Deleting non-existent agent returns 404."""
        client, app = api_env["client"], api_env["app"]

        r = await _register_agent(client, name="Existing Agent")
        _override_auth_as(app, r["agent_id"])

        resp = await client.delete("/api/v1/agents/00000000-0000-4000-8000-000000000000")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_unknown_agent_error_format(self, api_env):
        """404 response on delete uses the standard error format."""
        client, app = api_env["client"], api_env["app"]
        _override_auth_as(app, "some-agent-id")

        resp = await client.delete("/api/v1/agents/00000000-0000-4000-8000-000000000000")
        data = resp.json()

        assert "error" in data
        assert data["error"]["code"] == "AGENT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_delete_requires_auth(self, api_env):
        """DELETE /api/v1/agents/{id} without auth returns 401."""
        client, app = api_env["client"], api_env["app"]

        r = await _register_agent(client, name="Auth Test")
        _override_auth_deny(app)

        resp = await client.delete(f"/api/v1/agents/{r['agent_id']}")
        assert resp.status_code == 401


# ===========================================================================
# Multi-tenant API tests (access-control feature)
# ===========================================================================


def _override_auth_as_tenant(app: FastAPI, agent_id: str, tenant_id: str):
    """Override auth dependency to return a (agent_id, tenant_id) tuple."""

    async def _fixed_auth():
        return (agent_id, tenant_id)

    app.dependency_overrides[get_authenticated_agent] = _fixed_auth


async def _register_agent_with_key(
    client,
    api_key: str,
    name: str = "Joining Agent",
    description: str = "Joins tenant",
):
    """Register an agent with Authorization header (join-tenant flow)."""
    body = {"name": name, "description": description}
    resp = await client.post(
        "/api/v1/agents",
        json=body,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return resp


class TestRegisterAgentTenant:
    """Tests for tenant-aware registration via POST /api/v1/agents.

    Without Authorization → creates new tenant (new API key).
    With Authorization → joins existing tenant (reuses API key).
    """

    @pytest.mark.asyncio
    async def test_register_without_auth_creates_new_tenant(self, api_env):
        """Registration without Authorization creates a new tenant with new key."""
        client = api_env["client"]
        data = await _register_agent(client, name="New Tenant Agent")

        assert "api_key" in data
        assert data["api_key"].startswith("hky_")
        assert "agent_id" in data

    @pytest.mark.asyncio
    async def test_register_with_auth_joins_existing_tenant(self, api_env):
        """Registration with Authorization header joins the existing tenant."""
        client = api_env["client"]

        # First: create a tenant
        first = await _register_agent(client, name="Tenant Creator")
        api_key = first["api_key"]

        # Second: join the tenant
        resp = await _register_agent_with_key(
            client, api_key, name="Tenant Joiner"
        )
        assert resp.status_code == 201

        data = resp.json()
        assert data["name"] == "Tenant Joiner"
        assert "agent_id" in data

    @pytest.mark.asyncio
    async def test_join_tenant_returns_same_api_key(self, api_env):
        """Join-tenant registration echoes back the same API key."""
        client = api_env["client"]

        first = await _register_agent(client, name="Creator")
        api_key = first["api_key"]

        resp = await _register_agent_with_key(
            client, api_key, name="Joiner"
        )
        data = resp.json()

        assert data["api_key"] == api_key

    @pytest.mark.asyncio
    async def test_join_tenant_different_agent_ids(self, api_env):
        """Each agent joining a tenant gets a unique agent_id."""
        client = api_env["client"]

        first = await _register_agent(client, name="Agent 1")
        api_key = first["api_key"]

        resp = await _register_agent_with_key(
            client, api_key, name="Agent 2"
        )
        second = resp.json()

        assert first["agent_id"] != second["agent_id"]

    @pytest.mark.asyncio
    async def test_join_dead_tenant_returns_401(self, api_env):
        """Joining a tenant whose API key has no active agents → 401."""
        client, store = api_env["client"], api_env["store"]

        # Create and immediately deregister the only agent
        first = await _register_agent(client, name="Solo Agent")
        await store.deregister_agent(first["agent_id"])

        resp = await _register_agent_with_key(
            client, first["api_key"], name="Late Joiner"
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_join_with_invalid_key_returns_401(self, api_env):
        """Registration with an API key that was never used → 401."""
        client = api_env["client"]

        resp = await _register_agent_with_key(
            client, "hky_00000000000000000000000000000000", name="Invalid"
        )
        assert resp.status_code == 401


class TestTenantScopedListAgents:
    """Tests for GET /api/v1/agents with tenant isolation.

    List returns only agents in the caller's tenant.
    """

    @pytest.mark.asyncio
    async def test_list_returns_only_same_tenant_agents(self, api_env):
        """List agents returns only agents in the caller's tenant."""
        client, app, store = (
            api_env["client"], api_env["app"], api_env["store"],
        )

        # Create agents in tenant A
        a1 = await store.create_agent(
            name="A1", description="Tenant A", api_key="hky_tenantAAAAAAAAAAAAAAAAAAAAAAAA"
        )
        a2 = await store.create_agent(
            name="A2", description="Tenant A", api_key="hky_tenantAAAAAAAAAAAAAAAAAAAAAAAA"
        )
        tenant_a_hash = hashlib.sha256(
            b"hky_tenantAAAAAAAAAAAAAAAAAAAAAAAA"
        ).hexdigest()

        # Create agent in tenant B
        await store.create_agent(
            name="B1", description="Tenant B", api_key="hky_tenantBBBBBBBBBBBBBBBBBBBBBBBB"
        )

        _override_auth_as_tenant(app, a1["agent_id"], tenant_a_hash)

        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200

        data = resp.json()
        names = {a["name"] for a in data["agents"]}
        assert "A1" in names
        assert "A2" in names
        assert "B1" not in names

    @pytest.mark.asyncio
    async def test_list_empty_tenant_returns_empty(self, api_env):
        """List agents for a tenant with no agents returns empty array."""
        client, app = api_env["client"], api_env["app"]

        _override_auth_as_tenant(app, "any-agent", "nonexistent-tenant-hash")

        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200
        assert resp.json()["agents"] == []


class TestTenantScopedGetAgent:
    """Tests for GET /api/v1/agents/{id} with tenant isolation.

    Cross-tenant lookups return 404 (same as nonexistent).
    """

    @pytest.mark.asyncio
    async def test_get_same_tenant_agent_succeeds(self, api_env):
        """Getting an agent in the same tenant returns 200."""
        client, app, store = (
            api_env["client"], api_env["app"], api_env["store"],
        )

        key = "hky_sameTenantKEYKEYKEYKEYKEYKEYKE"
        tenant_hash = hashlib.sha256(key.encode()).hexdigest()

        a1 = await store.create_agent(
            name="A1", description="Same tenant", api_key=key
        )
        a2 = await store.create_agent(
            name="A2", description="Same tenant", api_key=key
        )

        _override_auth_as_tenant(app, a1["agent_id"], tenant_hash)

        resp = await client.get(f"/api/v1/agents/{a2['agent_id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "A2"

    @pytest.mark.asyncio
    async def test_get_cross_tenant_agent_returns_404(self, api_env):
        """Getting an agent from a different tenant returns 404."""
        client, app, store = (
            api_env["client"], api_env["app"], api_env["store"],
        )

        key_a = "hky_crossTenantAAAAAAAAAAAAAAAAAA"
        key_b = "hky_crossTenantBBBBBBBBBBBBBBBBBB"
        tenant_a_hash = hashlib.sha256(key_a.encode()).hexdigest()

        a1 = await store.create_agent(
            name="A1", description="Tenant A", api_key=key_a
        )
        b1 = await store.create_agent(
            name="B1", description="Tenant B", api_key=key_b
        )

        _override_auth_as_tenant(app, a1["agent_id"], tenant_a_hash)

        resp = await client.get(f"/api/v1/agents/{b1['agent_id']}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_404_same_as_nonexistent(self, api_env):
        """Cross-tenant 404 looks identical to nonexistent agent 404."""
        client, app, store = (
            api_env["client"], api_env["app"], api_env["store"],
        )

        key_a = "hky_leakCheckAAAAAAAAAAAAAAAAAAAA"
        key_b = "hky_leakCheckBBBBBBBBBBBBBBBBBBBB"
        tenant_a_hash = hashlib.sha256(key_a.encode()).hexdigest()

        a1 = await store.create_agent(
            name="A1", description="Tenant A", api_key=key_a
        )
        b1 = await store.create_agent(
            name="B1", description="Tenant B", api_key=key_b
        )

        _override_auth_as_tenant(app, a1["agent_id"], tenant_a_hash)

        # Cross-tenant lookup
        cross_resp = await client.get(f"/api/v1/agents/{b1['agent_id']}")
        # Nonexistent lookup
        ghost_resp = await client.get(
            "/api/v1/agents/00000000-0000-4000-8000-000000000000"
        )

        assert cross_resp.status_code == 404
        assert ghost_resp.status_code == 404
        # Both should have the same error structure
        assert cross_resp.json()["error"]["code"] == ghost_resp.json()["error"]["code"]
