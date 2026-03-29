"""Tests for Step 2: WebUI mounting in main.py.

Verifies that webui_router is included in the main FastAPI app and that
StaticFiles is mounted at /ui with html=True for SPA fallback. API routes
under /ui/api/* must take precedence over the static file catch-all.

These tests verify integration/mounting behavior, not endpoint logic
(which is covered by test_webui_api.py).
"""

import pytest
import fakeredis.aioredis
from httpx import AsyncClient, ASGITransport

from hikyaku_registry.main import create_app


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def mounted_app(tmp_path):
    """Create the main app with fakeredis and a temporary dist directory.

    The dist directory contains an index.html so StaticFiles can serve it.
    create_app is expected to accept a `webui_dist_dir` parameter (same
    testability pattern as the existing `redis` parameter).
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Create temporary dist directory with index.html
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text(
        "<!DOCTYPE html><html><body>Hikyaku WebUI</body></html>"
    )

    app = create_app(redis=redis, webui_dist_dir=str(dist_dir))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "app": app,
            "redis": redis,
            "dist_dir": dist_dir,
        }

    await redis.aclose()


# ===========================================================================
# WebUI API routes are accessible through the main app
# ===========================================================================


class TestWebuiRouterMounted:
    """Verify webui_router is included in the main app.

    Each WebUI API route should respond with a non-404 status, proving
    the router is mounted. Without auth, endpoints return 401.
    """

    @pytest.mark.asyncio
    async def test_login_route_accessible(self, mounted_app):
        """POST /ui/api/login is reachable (returns 401 without auth, not 404)."""
        client = mounted_app["client"]

        resp = await client.post("/ui/api/login")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_agents_route_accessible(self, mounted_app):
        """GET /ui/api/agents is reachable (returns 401 without auth, not 404)."""
        client = mounted_app["client"]

        resp = await client.get("/ui/api/agents")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_inbox_route_accessible(self, mounted_app):
        """GET /ui/api/agents/{id}/inbox is reachable (not 404)."""
        client = mounted_app["client"]

        resp = await client.get("/ui/api/agents/some-agent-id/inbox")
        assert resp.status_code != 404

    @pytest.mark.asyncio
    async def test_sent_route_accessible(self, mounted_app):
        """GET /ui/api/agents/{id}/sent is reachable (not 404)."""
        client = mounted_app["client"]

        resp = await client.get("/ui/api/agents/some-agent-id/sent")
        assert resp.status_code != 404

    @pytest.mark.asyncio
    async def test_send_route_accessible(self, mounted_app):
        """POST /ui/api/messages/send is reachable (not 404)."""
        client = mounted_app["client"]

        resp = await client.post(
            "/ui/api/messages/send",
            json={"from_agent_id": "x", "to_agent_id": "y", "text": "z"},
        )
        assert resp.status_code != 404


# ===========================================================================
# StaticFiles mounted at /ui
# ===========================================================================


class TestStaticFilesMounted:
    """Verify StaticFiles is mounted at /ui serving the SPA."""

    @pytest.mark.asyncio
    async def test_ui_root_serves_index_html(self, mounted_app):
        """GET /ui/ serves index.html from the dist directory."""
        client = mounted_app["client"]

        resp = await client.get("/ui/")
        assert resp.status_code == 200
        assert "Hikyaku WebUI" in resp.text

    @pytest.mark.asyncio
    async def test_static_file_served(self, mounted_app):
        """Static files in dist directory are accessible at /ui/."""
        dist_dir = mounted_app["dist_dir"]
        client = mounted_app["client"]

        (dist_dir / "app.js").write_text("console.log('hikyaku');")

        resp = await client.get("/ui/app.js")
        assert resp.status_code == 200
        assert "console.log" in resp.text

    @pytest.mark.asyncio
    async def test_spa_fallback_returns_index_html(self, mounted_app):
        """Non-existent paths under /ui/ return index.html (html=True SPA fallback)."""
        client = mounted_app["client"]

        # /ui/dashboard doesn't exist as a file — SPA fallback serves index.html
        resp = await client.get("/ui/dashboard")
        assert resp.status_code == 200
        assert "Hikyaku WebUI" in resp.text


# ===========================================================================
# API routes take precedence over static file catch-all
# ===========================================================================


class TestRoutePrecedence:
    """Verify API routes are checked before the StaticFiles mount.

    webui_router must be included BEFORE the StaticFiles mount so that
    /ui/api/* requests hit the API handler, not the static file handler.
    """

    @pytest.mark.asyncio
    async def test_login_returns_json_not_html(self, mounted_app):
        """POST /ui/api/login returns JSON (API), not HTML (static fallback)."""
        client = mounted_app["client"]

        resp = await client.post("/ui/api/login")
        assert resp.status_code == 401

        # Must be JSON from the API handler, not HTML from static fallback
        content_type = resp.headers.get("content-type", "")
        assert "application/json" in content_type

        data = resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_agents_returns_json_not_html(self, mounted_app):
        """GET /ui/api/agents returns JSON (API), not HTML (static fallback)."""
        client = mounted_app["client"]

        resp = await client.get("/ui/api/agents")
        assert resp.status_code == 401

        content_type = resp.headers.get("content-type", "")
        assert "application/json" in content_type

    @pytest.mark.asyncio
    async def test_send_returns_json_not_html(self, mounted_app):
        """POST /ui/api/messages/send returns JSON (API), not HTML (static fallback)."""
        client = mounted_app["client"]

        resp = await client.post(
            "/ui/api/messages/send",
            json={"from_agent_id": "x", "to_agent_id": "y", "text": "z"},
        )

        content_type = resp.headers.get("content-type", "")
        assert "application/json" in content_type


# ===========================================================================
# Existing routes still work after changes
# ===========================================================================


class TestExistingRoutesUnaffected:
    """Verify existing routes still work after WebUI mount changes."""

    @pytest.mark.asyncio
    async def test_registry_api_still_accessible(self, mounted_app):
        """GET /api/v1/agents is still accessible (registry router unaffected)."""
        client = mounted_app["client"]

        # Registry list requires auth — expect 401, not 404
        resp = await client.get("/api/v1/agents")
        assert resp.status_code != 404

    @pytest.mark.asyncio
    async def test_agent_card_still_accessible(self, mounted_app):
        """GET /.well-known/agent-card.json is still accessible."""
        client = mounted_app["client"]

        resp = await client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
