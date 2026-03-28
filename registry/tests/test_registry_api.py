"""Tests for api/registry.py — Registry REST API endpoints.

Covers: POST /api/v1/agents, GET /api/v1/agents, GET /api/v1/agents/{id},
DELETE /api/v1/agents/{id}.
Tests authentication requirements, ownership checks, and error responses.
"""

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
    """Override auth dependency to return a fixed agent_id."""

    async def _fixed_auth():
        return agent_id

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
        client, app = api_env["client"], api_env["app"]

        # Register two agents
        r1 = await _register_agent(client, name="Agent 1")
        r2 = await _register_agent(client, name="Agent 2")

        # Authenticate as one of them
        _override_auth_as(app, r1["agent_id"])

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
        client, app = api_env["client"], api_env["app"]

        r = await _register_agent(
            client,
            name="Detailed Agent",
            description="Has all fields",
            skills=[{"id": "s1", "name": "Skill 1", "description": "A skill", "tags": []}],
        )
        _override_auth_as(app, r["agent_id"])

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

        r1 = await _register_agent(client, name="Active Agent")
        r2 = await _register_agent(client, name="Gone Agent")

        # Deregister one agent via store
        await store.deregister_agent(r2["agent_id"])

        _override_auth_as(app, r1["agent_id"])

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
        client, app = api_env["client"], api_env["app"]

        r = await _register_agent(client, name="Detail Agent", description="Full detail")
        _override_auth_as(app, r["agent_id"])

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
        client, app = api_env["client"], api_env["app"]

        r1 = await _register_agent(client, name="Keeper")
        r2 = await _register_agent(client, name="Leaver")

        # Deregister r2
        _override_auth_as(app, r2["agent_id"])
        await client.delete(f"/api/v1/agents/{r2['agent_id']}")

        # List agents as r1
        _override_auth_as(app, r1["agent_id"])
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
