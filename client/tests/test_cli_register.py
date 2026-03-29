"""Tests for CLI register command changes — global --api-key required.

Covers: removal of register-specific --api-key (join_api_key), register
uses global --api-key, error message when missing, and api.register_agent
always sending Authorization header.

Design doc reference: Step 6 — CLI Changes.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from hikyaku_client.cli import cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    """Provide a click.testing.CliRunner."""
    return CliRunner()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BROKER_URL = "http://localhost:8000"
API_KEY = "hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
AGENT_ID = "550e8400-e29b-41d4-a716-446655440000"

SAMPLE_AGENT = {
    "agent_id": AGENT_ID,
    "api_key": API_KEY,
    "name": "test-agent",
    "description": "A test agent",
    "status": "active",
}


# ===========================================================================
# Register uses global --api-key
# ===========================================================================


class TestRegisterUsesGlobalApiKey:
    """Tests for register command using global --api-key.

    The register command no longer has its own --api-key option.
    It reads from ctx.obj['api_key'] (the global --api-key).
    """

    def test_global_api_key_passed_to_register(self, runner):
        """Register with global --api-key passes it to api.register_agent."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "--url",
                    BROKER_URL,
                    "--api-key",
                    API_KEY,
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "A test agent",
                ],
            )

        assert result.exit_code == 0
        call_kwargs = mock.call_args
        assert call_kwargs is not None
        # api_key should be passed to register_agent
        all_args = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        assert API_KEY in all_args or call_kwargs.kwargs.get("api_key") == API_KEY

    def test_global_api_key_via_env_var(self, runner):
        """Register uses HIKYAKU_API_KEY env var for authentication."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                ["register", "--name", "test-agent", "--description", "test"],
                env={
                    "HIKYAKU_URL": BROKER_URL,
                    "HIKYAKU_API_KEY": API_KEY,
                },
            )

        assert result.exit_code == 0

    def test_register_success_with_global_key(self, runner):
        """Register succeeds and shows output when global --api-key is set."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "--url",
                    BROKER_URL,
                    "--api-key",
                    API_KEY,
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "A test agent",
                ],
            )

        assert result.exit_code == 0
        assert AGENT_ID in result.output

    def test_register_json_output_with_global_key(self, runner):
        """Register with --json outputs valid JSON when global --api-key is set."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "--url",
                    BROKER_URL,
                    "--api-key",
                    API_KEY,
                    "--json",
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "A test agent",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_id"] == AGENT_ID


# ===========================================================================
# Missing --api-key error
# ===========================================================================


class TestRegisterMissingApiKey:
    """Tests for register command when --api-key is missing.

    Register must validate that global --api-key is set and show
    a specific error message if not.
    """

    def test_missing_api_key_shows_error(self, runner):
        """Register without --api-key prints error and exits non-zero."""
        result = runner.invoke(
            cli,
            [
                "--url",
                BROKER_URL,
                "register",
                "--name",
                "test-agent",
                "--description",
                "A test agent",
            ],
        )

        assert result.exit_code != 0

    def test_missing_api_key_error_message(self, runner):
        """Error message mentions --api-key requirement and WebUI."""
        result = runner.invoke(
            cli,
            [
                "--url",
                BROKER_URL,
                "register",
                "--name",
                "test-agent",
                "--description",
                "A test agent",
            ],
        )

        assert "--api-key" in result.output or "--api-key" in (result.stderr or "")

    def test_missing_api_key_mentions_webui(self, runner):
        """Error message mentions creating API key at the WebUI."""
        result = runner.invoke(
            cli,
            [
                "--url",
                BROKER_URL,
                "register",
                "--name",
                "test-agent",
                "--description",
                "A test agent",
            ],
        )

        output = result.output + (result.stderr or "")
        assert "WebUI" in output or "webui" in output.lower()

    def test_missing_api_key_does_not_call_api(self, runner):
        """Register without --api-key does not make any API call."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            runner.invoke(
                cli,
                [
                    "--url",
                    BROKER_URL,
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "A test agent",
                ],
            )

        mock.assert_not_called()


# ===========================================================================
# Register-specific --api-key removed
# ===========================================================================


class TestRegisterSpecificApiKeyRemoved:
    """Tests that the register-specific --api-key (join_api_key) is removed.

    Only the global --api-key on the cli group should exist.
    """

    def test_register_specific_api_key_not_accepted(self, runner):
        """Register does not accept its own --api-key after the global options."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "--url",
                    BROKER_URL,
                    "--api-key",
                    API_KEY,
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "A test agent",
                    "--api-key",
                    "hky_someOtherKey0000000000000000",
                ],
            )

        # Should fail because register no longer has its own --api-key
        assert result.exit_code != 0


# ===========================================================================
# api.register_agent always sends Authorization header
# ===========================================================================


class TestApiRegisterAgentAuth:
    """Tests for api.register_agent always sending Authorization header.

    The api_key parameter is required, and Authorization: Bearer <key>
    is always included in the request.
    """

    @pytest.mark.asyncio
    async def test_always_sends_authorization_header(self):
        """register_agent always includes Authorization: Bearer header."""
        from hikyaku_client.api import register_agent

        with patch("hikyaku_client.api.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.json.return_value = SAMPLE_AGENT
            mock_response.raise_for_status = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await register_agent(
                BROKER_URL, "test-agent", "A test agent", api_key=API_KEY
            )

            call_kwargs = mock_client.post.call_args
            headers = call_kwargs.kwargs.get("headers", {})
            assert "Authorization" in headers
            assert headers["Authorization"] == f"Bearer {API_KEY}"

    @pytest.mark.asyncio
    async def test_api_key_is_required_parameter(self):
        """register_agent requires api_key (not optional)."""
        from hikyaku_client.api import register_agent

        with pytest.raises(TypeError):
            await register_agent(BROKER_URL, "test-agent", "A test agent")
