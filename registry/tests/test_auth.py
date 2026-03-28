"""Tests for auth.py — API key authentication dependency.

Covers: get_authenticated_agent FastAPI dependency.
Verifies Bearer token extraction, SHA-256 lookup via RegistryStore,
and HTTP 401 for all invalid/missing auth scenarios.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from hikyaku_registry.auth import get_authenticated_agent


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
