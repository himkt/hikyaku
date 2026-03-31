"""Tests for hikyaku CLI tool.

Covers: All CLI subcommands (register, send, broadcast, poll, ack, cancel,
get-task, agents, deregister), global options, --json flag, environment
variable fallback, and error handling.

Uses click.testing.CliRunner with mocked api.* functions.
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
# Helpers
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

SAMPLE_AGENT_INFO = {
    "agent_id": AGENT_ID,
    "name": "test-agent",
    "description": "A test agent",
    "status": "active",
    "skills": [],
}

SAMPLE_TASK = {
    "id": "task-001",
    "contextId": AGENT_ID,
    "status": {"state": "input-required", "timestamp": "2026-03-28T12:00:00Z"},
    "artifacts": [
        {
            "parts": [{"type": "text", "text": "Hello from Agent A"}],
        }
    ],
    "metadata": {
        "fromAgentId": "agent-sender-001",
        "toAgentId": AGENT_ID,
        "type": "unicast",
    },
}

SAMPLE_COMPLETED_TASK = {
    **SAMPLE_TASK,
    "status": {"state": "completed", "timestamp": "2026-03-28T12:01:00Z"},
}


def _auth_env():
    """Environment variables for authentication."""
    return {"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY}


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


class TestRegisterCommand:
    """Tests for `hikyaku register`."""

    def test_register_success(self, runner):
        """Register prints agent_id and name but not api_key."""
        with patch(
            "hikyaku_client.cli.api.register_agent",
            new_callable=AsyncMock,
            return_value=SAMPLE_AGENT,
        ):
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
        assert API_KEY not in result.output

    def test_register_prints_export_statements(self, runner):
        """Register output includes only HIKYAKU_AGENT_ID export."""
        with patch(
            "hikyaku_client.cli.api.register_agent",
            new_callable=AsyncMock,
            return_value=SAMPLE_AGENT,
        ):
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
        assert "export HIKYAKU_AGENT_ID=" in result.output
        assert "export HIKYAKU_API_KEY=" not in result.output
        assert "export HIKYAKU_URL=" not in result.output

    def test_register_json_output(self, runner):
        """Register with --json outputs valid JSON without api_key."""
        with patch(
            "hikyaku_client.cli.api.register_agent",
            new_callable=AsyncMock,
            return_value=SAMPLE_AGENT,
        ):
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
        assert "api_key" not in data

    def test_register_output_shows_name(self, runner):
        """Register output includes the agent name."""
        with patch(
            "hikyaku_client.cli.api.register_agent",
            new_callable=AsyncMock,
            return_value=SAMPLE_AGENT,
        ):
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
        assert "test-agent" in result.output

    def test_register_requires_api_key(self, runner):
        """Register requires HIKYAKU_API_KEY environment variable."""
        with patch(
            "hikyaku_client.cli.api.register_agent",
            new_callable=AsyncMock,
            return_value=SAMPLE_AGENT,
        ):
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

    def test_register_requires_name(self, runner):
        """Register fails if --name is not provided."""
        result = runner.invoke(
            cli,
            [
                "register",
                "--description",
                "A test agent",
            ],
            env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
        )

        assert result.exit_code != 0

    def test_register_with_skills(self, runner):
        """Register passes skills JSON to api.register_agent."""
        skills_json = json.dumps(
            [
                {
                    "id": "frontend",
                    "name": "Frontend Dev",
                    "description": "React/TS",
                    "tags": ["react", "typescript"],
                }
            ]
        )
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
                    "--skills",
                    skills_json,
                ],
                env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0
        call_kwargs = mock.call_args
        # Verify skills were passed (either as positional or keyword arg)
        assert call_kwargs is not None

    def test_register_api_error(self, runner):
        """Register shows error on API failure."""
        with patch(
            "hikyaku_client.cli.api.register_agent",
            new_callable=AsyncMock,
            side_effect=Exception("Connection refused"),
        ):
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

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


class TestSendCommand:
    """Tests for `hikyaku send`."""

    def test_send_success(self, runner):
        """Send unicast message succeeds."""
        target_id = "target-agent-001"
        mock = AsyncMock(return_value=SAMPLE_TASK)
        with patch("hikyaku_client.cli.api.send_message", mock):
            result = runner.invoke(
                cli,
                ["send", "--agent-id", AGENT_ID, "--to", target_id, "--text", "Hello from CLI"],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        mock.assert_called_once()

    def test_send_json_output(self, runner):
        """Send with --json outputs task JSON."""
        mock = AsyncMock(return_value=SAMPLE_TASK)
        with patch("hikyaku_client.cli.api.send_message", mock):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "send",
                    "--agent-id",
                    AGENT_ID,
                    "--to",
                    "target-001",
                    "--text",
                    "Hello",
                ],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "task-001"

    def test_send_requires_to(self, runner):
        """Send fails without --to option."""
        result = runner.invoke(
            cli,
            ["send", "--agent-id", AGENT_ID, "--text", "Hello"],
            env=_auth_env(),
        )

        assert result.exit_code != 0

    def test_send_requires_text(self, runner):
        """Send fails without --text option."""
        result = runner.invoke(
            cli,
            ["send", "--agent-id", AGENT_ID, "--to", "target-001"],
            env=_auth_env(),
        )

        assert result.exit_code != 0

    def test_send_requires_auth(self, runner):
        """Send fails without HIKYAKU_API_KEY."""
        result = runner.invoke(
            cli,
            ["send", "--agent-id", AGENT_ID, "--to", "target-001", "--text", "Hello"],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0

    def test_send_requires_agent_id(self, runner):
        """Send fails without --agent-id."""
        result = runner.invoke(
            cli,
            ["send", "--to", "target-001", "--text", "Hello"],
            env=_auth_env(),
        )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


class TestBroadcastCommand:
    """Tests for `hikyaku broadcast`."""

    def test_broadcast_success(self, runner):
        """Broadcast message succeeds."""
        mock = AsyncMock(return_value=[SAMPLE_TASK])
        with patch("hikyaku_client.cli.api.broadcast_message", mock):
            result = runner.invoke(
                cli,
                ["broadcast", "--agent-id", AGENT_ID, "--text", "Build failed on main"],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        mock.assert_called_once()

    def test_broadcast_json_output(self, runner):
        """Broadcast with --json outputs JSON."""
        mock = AsyncMock(return_value=[SAMPLE_TASK])
        with patch("hikyaku_client.cli.api.broadcast_message", mock):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "broadcast",
                    "--agent-id",
                    AGENT_ID,
                    "--text",
                    "Build failed on main",
                ],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_broadcast_requires_text(self, runner):
        """Broadcast fails without --text."""
        result = runner.invoke(
            cli,
            ["broadcast", "--agent-id", AGENT_ID],
            env=_auth_env(),
        )

        assert result.exit_code != 0

    def test_broadcast_requires_auth(self, runner):
        """Broadcast fails without HIKYAKU_API_KEY."""
        result = runner.invoke(
            cli,
            ["broadcast", "--agent-id", AGENT_ID, "--text", "Hello"],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0

    def test_broadcast_requires_agent_id(self, runner):
        """Broadcast fails without --agent-id."""
        result = runner.invoke(
            cli,
            ["broadcast", "--text", "Hello"],
            env=_auth_env(),
        )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------


class TestPollCommand:
    """Tests for `hikyaku poll`."""

    def test_poll_success(self, runner):
        """Poll returns inbox messages."""
        mock = AsyncMock(return_value=[SAMPLE_TASK])
        with patch("hikyaku_client.cli.api.poll_tasks", mock):
            result = runner.invoke(
                cli,
                ["poll", "--agent-id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        mock.assert_called_once()

    def test_poll_empty_inbox(self, runner):
        """Poll with empty inbox shows appropriate message."""
        mock = AsyncMock(return_value=[])
        with patch("hikyaku_client.cli.api.poll_tasks", mock):
            result = runner.invoke(
                cli,
                ["poll", "--agent-id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code == 0

    def test_poll_json_output(self, runner):
        """Poll with --json outputs JSON array."""
        mock = AsyncMock(return_value=[SAMPLE_TASK])
        with patch("hikyaku_client.cli.api.poll_tasks", mock):
            result = runner.invoke(
                cli,
                ["--json", "poll", "--agent-id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_poll_with_since(self, runner):
        """Poll passes --since parameter to api."""
        mock = AsyncMock(return_value=[])
        with patch("hikyaku_client.cli.api.poll_tasks", mock):
            result = runner.invoke(
                cli,
                ["poll", "--agent-id", AGENT_ID, "--since", "2026-03-28T12:00:00Z"],
                env=_auth_env(),
            )

        assert result.exit_code == 0

    def test_poll_with_page_size(self, runner):
        """Poll passes --page-size parameter to api."""
        mock = AsyncMock(return_value=[])
        with patch("hikyaku_client.cli.api.poll_tasks", mock):
            result = runner.invoke(
                cli,
                ["poll", "--agent-id", AGENT_ID, "--page-size", "50"],
                env=_auth_env(),
            )

        assert result.exit_code == 0

    def test_poll_requires_auth(self, runner):
        """Poll fails without HIKYAKU_API_KEY."""
        result = runner.invoke(
            cli,
            ["poll", "--agent-id", AGENT_ID],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0

    def test_poll_requires_agent_id(self, runner):
        """Poll fails without --agent-id."""
        result = runner.invoke(
            cli,
            ["poll"],
            env=_auth_env(),
        )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Ack
# ---------------------------------------------------------------------------


class TestAckCommand:
    """Tests for `hikyaku ack`."""

    def test_ack_success(self, runner):
        """Ack a message succeeds."""
        mock = AsyncMock(return_value=SAMPLE_COMPLETED_TASK)
        with patch("hikyaku_client.cli.api.ack_task", mock):
            result = runner.invoke(
                cli,
                ["ack", "--agent-id", AGENT_ID, "--task-id", "task-001"],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        mock.assert_called_once()

    def test_ack_json_output(self, runner):
        """Ack with --json outputs task JSON."""
        mock = AsyncMock(return_value=SAMPLE_COMPLETED_TASK)
        with patch("hikyaku_client.cli.api.ack_task", mock):
            result = runner.invoke(
                cli,
                ["--json", "ack", "--agent-id", AGENT_ID, "--task-id", "task-001"],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"]["state"] == "completed"

    def test_ack_requires_task_id(self, runner):
        """Ack fails without --task-id."""
        result = runner.invoke(
            cli,
            ["ack", "--agent-id", AGENT_ID],
            env=_auth_env(),
        )

        assert result.exit_code != 0

    def test_ack_requires_auth(self, runner):
        """Ack fails without HIKYAKU_API_KEY."""
        result = runner.invoke(
            cli,
            ["ack", "--agent-id", AGENT_ID, "--task-id", "task-001"],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0

    def test_ack_requires_agent_id(self, runner):
        """Ack fails without --agent-id."""
        result = runner.invoke(
            cli,
            ["ack", "--task-id", "task-001"],
            env=_auth_env(),
        )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


class TestCancelCommand:
    """Tests for `hikyaku cancel`."""

    def test_cancel_success(self, runner):
        """Cancel a task succeeds."""
        canceled_task = {
            **SAMPLE_TASK,
            "status": {"state": "canceled", "timestamp": "2026-03-28T12:01:00Z"},
        }
        mock = AsyncMock(return_value=canceled_task)
        with patch("hikyaku_client.cli.api.cancel_task", mock):
            result = runner.invoke(
                cli,
                ["cancel", "--agent-id", AGENT_ID, "--task-id", "task-001"],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        mock.assert_called_once()

    def test_cancel_json_output(self, runner):
        """Cancel with --json outputs task JSON."""
        canceled_task = {
            **SAMPLE_TASK,
            "status": {"state": "canceled", "timestamp": "2026-03-28T12:01:00Z"},
        }
        mock = AsyncMock(return_value=canceled_task)
        with patch("hikyaku_client.cli.api.cancel_task", mock):
            result = runner.invoke(
                cli,
                ["--json", "cancel", "--agent-id", AGENT_ID, "--task-id", "task-001"],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"]["state"] == "canceled"

    def test_cancel_requires_task_id(self, runner):
        """Cancel fails without --task-id."""
        result = runner.invoke(
            cli,
            ["cancel", "--agent-id", AGENT_ID],
            env=_auth_env(),
        )

        assert result.exit_code != 0

    def test_cancel_requires_auth(self, runner):
        """Cancel fails without HIKYAKU_API_KEY."""
        result = runner.invoke(
            cli,
            ["cancel", "--agent-id", AGENT_ID, "--task-id", "task-001"],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0

    def test_cancel_requires_agent_id(self, runner):
        """Cancel fails without --agent-id."""
        result = runner.invoke(
            cli,
            ["cancel", "--task-id", "task-001"],
            env=_auth_env(),
        )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Get-Task
# ---------------------------------------------------------------------------


class TestGetTaskCommand:
    """Tests for `hikyaku get-task`."""

    def test_get_task_success(self, runner):
        """Get-task returns task details."""
        mock = AsyncMock(return_value=SAMPLE_TASK)
        with patch("hikyaku_client.cli.api.get_task", mock):
            result = runner.invoke(
                cli,
                ["get-task", "--agent-id", AGENT_ID, "--task-id", "task-001"],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        mock.assert_called_once()

    def test_get_task_json_output(self, runner):
        """Get-task with --json outputs task JSON."""
        mock = AsyncMock(return_value=SAMPLE_TASK)
        with patch("hikyaku_client.cli.api.get_task", mock):
            result = runner.invoke(
                cli,
                ["--json", "get-task", "--agent-id", AGENT_ID, "--task-id", "task-001"],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "task-001"

    def test_get_task_requires_task_id(self, runner):
        """Get-task fails without --task-id."""
        result = runner.invoke(
            cli,
            ["get-task", "--agent-id", AGENT_ID],
            env=_auth_env(),
        )

        assert result.exit_code != 0

    def test_get_task_requires_auth(self, runner):
        """Get-task fails without HIKYAKU_API_KEY."""
        result = runner.invoke(
            cli,
            ["get-task", "--agent-id", AGENT_ID, "--task-id", "task-001"],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0

    def test_get_task_requires_agent_id(self, runner):
        """Get-task fails without --agent-id."""
        result = runner.invoke(
            cli,
            ["get-task", "--task-id", "task-001"],
            env=_auth_env(),
        )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class TestAgentsCommand:
    """Tests for `hikyaku agents`."""

    def test_list_agents_success(self, runner):
        """Agents lists all registered agents."""
        mock = AsyncMock(return_value=[SAMPLE_AGENT_INFO])
        with patch("hikyaku_client.cli.api.list_agents", mock):
            result = runner.invoke(
                cli,
                ["agents", "--agent-id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        mock.assert_called_once()

    def test_list_agents_json_output(self, runner):
        """Agents with --json outputs JSON array."""
        mock = AsyncMock(return_value=[SAMPLE_AGENT_INFO])
        with patch("hikyaku_client.cli.api.list_agents", mock):
            result = runner.invoke(
                cli,
                ["--json", "agents", "--agent-id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["agent_id"] == AGENT_ID

    def test_get_agent_detail(self, runner):
        """Agents with --id returns single agent detail."""
        mock = AsyncMock(return_value=SAMPLE_AGENT_INFO)
        with patch("hikyaku_client.cli.api.list_agents", mock):
            result = runner.invoke(
                cli,
                ["agents", "--agent-id", AGENT_ID, "--id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code == 0

    def test_get_agent_detail_json(self, runner):
        """Agents with --id and --json returns single agent JSON."""
        mock = AsyncMock(return_value=SAMPLE_AGENT_INFO)
        with patch("hikyaku_client.cli.api.list_agents", mock):
            result = runner.invoke(
                cli,
                ["--json", "agents", "--agent-id", AGENT_ID, "--id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_id"] == AGENT_ID

    def test_agents_requires_auth(self, runner):
        """Agents fails without HIKYAKU_API_KEY."""
        result = runner.invoke(
            cli,
            ["agents", "--agent-id", AGENT_ID],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0

    def test_agents_requires_agent_id(self, runner):
        """Agents fails without --agent-id."""
        result = runner.invoke(
            cli,
            ["agents"],
            env=_auth_env(),
        )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Deregister
# ---------------------------------------------------------------------------


class TestDeregisterCommand:
    """Tests for `hikyaku deregister`."""

    def test_deregister_success(self, runner):
        """Deregister removes own registration."""
        mock = AsyncMock(return_value=None)
        with patch("hikyaku_client.cli.api.deregister_agent", mock):
            result = runner.invoke(
                cli,
                ["deregister", "--agent-id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code == 0
        mock.assert_called_once()

    def test_deregister_json_output(self, runner):
        """Deregister with --json outputs JSON confirmation."""
        mock = AsyncMock(return_value=None)
        with patch("hikyaku_client.cli.api.deregister_agent", mock):
            result = runner.invoke(
                cli,
                ["--json", "deregister", "--agent-id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code == 0

    def test_deregister_requires_auth(self, runner):
        """Deregister fails without HIKYAKU_API_KEY."""
        result = runner.invoke(
            cli,
            ["deregister", "--agent-id", AGENT_ID],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0

    def test_deregister_requires_agent_id(self, runner):
        """Deregister fails without --agent-id."""
        result = runner.invoke(
            cli,
            ["deregister"],
            env=_auth_env(),
        )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Global Options & Environment Variables
# ---------------------------------------------------------------------------


class TestGlobalOptions:
    """Tests for global CLI options and environment variables."""

    def test_url_from_env(self, runner):
        """Broker URL is read from HIKYAKU_URL env var."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                ["register", "--name", "test-agent", "--description", "test"],
                env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0

    def test_api_key_from_env(self, runner):
        """API key is read from HIKYAKU_API_KEY env var."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                ["register", "--name", "test-agent", "--description", "test"],
                env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0

    def test_default_url(self, runner):
        """Default URL is http://localhost:8000 when HIKYAKU_URL is not set."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "register",
                    "--name",
                    "test-agent",
                    "--description",
                    "test",
                ],
                env={"HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0

    def test_missing_api_key_env_var_error(self, runner):
        """Missing HIKYAKU_API_KEY shows error referencing the env var."""
        result = runner.invoke(
            cli,
            [
                "register",
                "--name",
                "test-agent",
                "--description",
                "test",
            ],
            env={"HIKYAKU_URL": BROKER_URL},
        )

        assert result.exit_code != 0
        output = result.output + (result.stderr or "")
        assert "HIKYAKU_API_KEY" in output


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for CLI error handling."""

    def test_connection_error(self, runner):
        """Connection errors are reported gracefully."""
        with patch(
            "hikyaku_client.cli.api.poll_tasks",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Connection refused"),
        ):
            result = runner.invoke(
                cli,
                ["poll", "--agent-id", AGENT_ID],
                env=_auth_env(),
            )

        assert result.exit_code != 0

    def test_api_error_response(self, runner):
        """API error responses are reported gracefully."""
        with patch(
            "hikyaku_client.cli.api.send_message",
            new_callable=AsyncMock,
            side_effect=Exception("404: Agent not found"),
        ):
            result = runner.invoke(
                cli,
                ["send", "--agent-id", AGENT_ID, "--to", "nonexistent", "--text", "Hello"],
                env=_auth_env(),
            )

        assert result.exit_code != 0

    def test_invalid_json_in_skills(self, runner):
        """Register with invalid --skills JSON shows error."""
        result = runner.invoke(
            cli,
            [
                "register",
                "--name",
                "test-agent",
                "--description",
                "test",
                "--skills",
                "not-valid-json",
            ],
            env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
        )

        assert result.exit_code != 0

    def test_unknown_subcommand(self, runner):
        """Unknown subcommand shows usage error."""
        result = runner.invoke(
            cli,
            ["nonexistent-command"],
        )

        assert result.exit_code != 0


# ===========================================================================
# Multi-tenant CLI tests (access-control feature)
# ===========================================================================


class TestRegisterWithEnvApiKey:
    """Tests for register using HIKYAKU_API_KEY env var.

    The register command reads api_key from ctx.obj['api_key'],
    which is populated from the HIKYAKU_API_KEY environment variable.
    """

    def test_register_env_api_key_passes_key(self, runner):
        """Register with HIKYAKU_API_KEY env var passes api_key to api.register_agent."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "register",
                    "--name",
                    "Joiner",
                    "--description",
                    "Join tenant",
                ],
                env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0
        call_kwargs = mock.call_args
        assert call_kwargs is not None
        all_args = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        assert API_KEY in all_args or call_kwargs.kwargs.get("api_key") == API_KEY

    def test_register_shows_output(self, runner):
        """Register with env var shows agent_id."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "register",
                    "--name",
                    "Joiner",
                    "--description",
                    "Join",
                ],
                env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0
        assert AGENT_ID in result.output

    def test_register_json_output(self, runner):
        """Register with env var and --json outputs valid JSON."""
        mock = AsyncMock(return_value=SAMPLE_AGENT)
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "register",
                    "--name",
                    "Joiner",
                    "--description",
                    "Join",
                ],
                env={"HIKYAKU_URL": BROKER_URL, "HIKYAKU_API_KEY": API_KEY},
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["agent_id"] == AGENT_ID

    def test_register_api_error(self, runner):
        """Register with invalid API key shows error."""
        mock = AsyncMock(side_effect=Exception("401: Invalid API key"))
        with patch("hikyaku_client.cli.api.register_agent", mock):
            result = runner.invoke(
                cli,
                [
                    "register",
                    "--name",
                    "Joiner",
                    "--description",
                    "Join",
                ],
                env={
                    "HIKYAKU_URL": BROKER_URL,
                    "HIKYAKU_API_KEY": "hky_invalid000000000000000000000000",
                },
            )

        assert result.exit_code != 0
