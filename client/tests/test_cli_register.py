"""Tests for CLI register command changes — HIKYAKU_API_KEY env var required.

Covers: removal of --api-key CLI option, register uses HIKYAKU_API_KEY
env var, error message when missing, and api.register_agent always sending
Authorization header.

Design doc reference: Step 2 — Refactor CLI Global Options.
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
# Register uses HIKYAKU_API_KEY env var
# ===========================================================================


class TestRegisterUsesEnvApiKey:
    """Tests for register command using HIKYAKU_API_KEY env var.

    The register command reads api_key from ctx.obj['api_key'],
    which is populated from the HIKYAKU_API_KEY environment variable.
    """

    def test_env_api_key_passed_to_register(self, runner):
        """Register with HIKYAKU_API_KEY env var passes it to api.register_agent."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "A test agent",
                ],
                env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0
        call_kwargs = mock.call_args
        assert call_kwargs is not None
        # api_key should be passed to register_agent
        all_args = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        assert API_KEY in all_args or call_kwargs.kwargs.get("api_key") == API_KEY

    def test_api_key_via_env_var(self, runner):
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

    def test_register_success_with_env_key(self, runner):
        """Register succeeds and shows output when HIKYAKU_API_KEY is set."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "A test agent",
                ],
                env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0
        assert AGENT_ID in result.output

    def test_register_json_output_with_env_key(self, runner):
        """Register with --json outputs valid JSON when HIKYAKU_API_KEY is set."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "A test agent",
                ],
                env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_id"] == AGENT_ID


# ===========================================================================
# Missing HIKYAKU_API_KEY env var error
# ===========================================================================


class TestRegisterMissingApiKey:
    """Tests for register command when HIKYAKU_API_KEY is missing.

    Register must validate that HIKYAKU_API_KEY env var is set and show
    a specific error message if not.
    """

    def test_missing_api_key_shows_error(self, runner):
        """Register without HIKYAKU_API_KEY prints error and exits non-zero."""
        result = runner.invoke(
            cli,
            [
                "register",
                "--name",
                "test-agent",
                "--description",
                "A test agent",
            ],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0

    def test_missing_api_key_error_message(self, runner):
        """Error message mentions HIKYAKU_API_KEY environment variable."""
        result = runner.invoke(
            cli,
            [
                "register",
                "--name",
                "test-agent",
                "--description",
                "A test agent",
            ],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        output = result.output + (result.stderr or "")
        assert "HIKYAKU_API_KEY" in output

    def test_missing_api_key_mentions_webui(self, runner):
        """Error message mentions creating API key at the WebUI."""
        result = runner.invoke(
            cli,
            [
                "register",
                "--name",
                "test-agent",
                "--description",
                "A test agent",
            ],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        output = result.output + (result.stderr or "")
        assert "WebUI" in output or "webui" in output.lower()

    def test_missing_api_key_does_not_call_api(self, runner):
        """Register without HIKYAKU_API_KEY does not make any API call."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            runner.invoke(
                cli,
                [
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "A test agent",
                ],
                env={"HIKYAKU_URL": BROKER_URL},
            )

        mock.assert_not_called()


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
