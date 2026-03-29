import asyncio
import json
from collections.abc import Coroutine
from typing import Any

import click

from hikyaku_client import api
from hikyaku_client import output


def _run(coro: Coroutine) -> Any:
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _require_auth(ctx: click.Context) -> None:
    """Validate that api_key and agent_id are set."""
    if not ctx.obj.get("api_key"):
        click.echo("Error: --api-key is required (or set HIKYAKU_API_KEY)", err=True)
        ctx.exit(1)
    if not ctx.obj.get("agent_id"):
        click.echo("Error: --agent-id is required (or set HIKYAKU_AGENT_ID)", err=True)
        ctx.exit(1)


@click.group()
@click.option(
    "--url",
    envvar="HIKYAKU_URL",
    default="http://localhost:8000",
    help="Broker URL",
)
@click.option(
    "--api-key",
    envvar="HIKYAKU_API_KEY",
    default=None,
    help="API key for authentication",
)
@click.option(
    "--agent-id",
    envvar="HIKYAKU_AGENT_ID",
    default=None,
    help="Agent ID",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Output in JSON format",
)
@click.pass_context
def cli(ctx, url, api_key, agent_id, json_output):
    """Hikyaku — CLI for the A2A message broker."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["api_key"] = api_key
    ctx.obj["agent_id"] = agent_id
    ctx.obj["json_output"] = json_output


@cli.command()
@click.option("--name", required=True, help="Agent name")
@click.option("--description", required=True, help="Agent description")
@click.option("--skills", default=None, help="Skills as JSON string")
@click.pass_context
def register(ctx, name, description, skills):
    """Register a new agent with the broker."""
    api_key = ctx.obj.get("api_key")
    if not api_key:
        click.echo(
            "Error: --api-key is required for registration. "
            "Create an API key at the Hikyaku WebUI.",
            err=True,
        )
        ctx.exit(1)
        return

    try:
        parsed_skills = None
        if skills is not None:
            try:
                parsed_skills = json.loads(skills)
            except json.JSONDecodeError as e:
                click.echo(f"Error: Invalid JSON in --skills: {e}", err=True)
                ctx.exit(1)
                return

        result = _run(
            api.register_agent(
                ctx.obj["url"], name, description,
                skills=parsed_skills, api_key=api_key,
            )
        )

        if ctx.obj["json_output"]:
            click.echo(output.format_json(result))
        else:
            click.echo(output.format_register(result))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.option("--to", required=True, help="Recipient agent ID")
@click.option("--text", required=True, help="Message text")
@click.pass_context
def send(ctx, to, text):
    """Send a unicast message to another agent."""
    _require_auth(ctx)
    try:
        result = _run(
            api.send_message(
                ctx.obj["url"],
                ctx.obj["api_key"],
                ctx.obj["agent_id"],
                to,
                text,
            )
        )

        if ctx.obj["json_output"]:
            click.echo(output.format_json(result))
        else:
            click.echo("Message sent.")
            click.echo(output.format_task(result))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.option("--text", required=True, help="Message text")
@click.pass_context
def broadcast(ctx, text):
    """Broadcast a message to all agents."""
    _require_auth(ctx)
    try:
        result = _run(
            api.broadcast_message(
                ctx.obj["url"],
                ctx.obj["api_key"],
                ctx.obj["agent_id"],
                text,
            )
        )

        if ctx.obj["json_output"]:
            click.echo(output.format_json(result))
        else:
            click.echo("Broadcast sent.")
            click.echo(output.format_task_list(result))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.option("--since", default=None, help="Filter tasks since timestamp")
@click.option("--page-size", default=None, type=int, help="Number of tasks")
@click.pass_context
def poll(ctx, since, page_size):
    """Poll inbox for messages."""
    _require_auth(ctx)
    try:
        result = _run(
            api.poll_tasks(
                ctx.obj["url"],
                ctx.obj["api_key"],
                ctx.obj["agent_id"],
                since=since,
                page_size=page_size,
            )
        )

        if ctx.obj["json_output"]:
            click.echo(output.format_json(result))
        else:
            click.echo(output.format_task_list(result))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.option("--task-id", required=True, help="Task ID to acknowledge")
@click.pass_context
def ack(ctx, task_id):
    """Acknowledge receipt of a message."""
    _require_auth(ctx)
    try:
        result = _run(
            api.ack_task(
                ctx.obj["url"],
                ctx.obj["api_key"],
                ctx.obj["agent_id"],
                task_id,
            )
        )

        if ctx.obj["json_output"]:
            click.echo(output.format_json(result))
        else:
            click.echo("Message acknowledged.")
            click.echo(output.format_task(result))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.option("--task-id", required=True, help="Task ID to cancel")
@click.pass_context
def cancel(ctx, task_id):
    """Cancel (retract) a sent message."""
    _require_auth(ctx)
    try:
        result = _run(
            api.cancel_task(
                ctx.obj["url"],
                ctx.obj["api_key"],
                ctx.obj["agent_id"],
                task_id,
            )
        )

        if ctx.obj["json_output"]:
            click.echo(output.format_json(result))
        else:
            click.echo("Task canceled.")
            click.echo(output.format_task(result))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)


@cli.command("get-task")
@click.option("--task-id", required=True, help="Task ID to retrieve")
@click.pass_context
def get_task(ctx, task_id):
    """Get details of a specific task."""
    _require_auth(ctx)
    try:
        result = _run(
            api.get_task(
                ctx.obj["url"],
                ctx.obj["api_key"],
                ctx.obj["agent_id"],
                task_id,
            )
        )

        if ctx.obj["json_output"]:
            click.echo(output.format_json(result))
        else:
            click.echo(output.format_task(result))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.option("--id", "detail_id", default=None, help="Get detail for specific agent")
@click.pass_context
def agents(ctx, detail_id):
    """List registered agents or get agent detail."""
    _require_auth(ctx)
    try:
        result = _run(
            api.list_agents(
                ctx.obj["url"],
                ctx.obj["api_key"],
                caller_id=ctx.obj["agent_id"],
                agent_id=detail_id,
            )
        )

        if ctx.obj["json_output"]:
            click.echo(output.format_json(result))
        else:
            if isinstance(result, list):
                click.echo(output.format_agent_list(result))
            else:
                click.echo(output.format_agent(result))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.pass_context
def deregister(ctx):
    """Deregister this agent from the broker."""
    _require_auth(ctx)
    try:
        _run(
            api.deregister_agent(
                ctx.obj["url"],
                ctx.obj["api_key"],
                ctx.obj["agent_id"],
            )
        )

        if ctx.obj["json_output"]:
            click.echo(output.format_json({"status": "deregistered"}))
        else:
            click.echo("Agent deregistered successfully.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)
