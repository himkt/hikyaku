"""MCP server tool handlers — transparent proxy to broker.

poll reads from the local SSEClient buffer; all other tools forward
to the broker via RegistryForwarder.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from hikyaku_mcp.config import get_config
from hikyaku_mcp.registry import RegistryForwarder
from hikyaku_mcp.sse_client import SSEClient


async def handle_poll(
    *,
    sse_client: SSEClient,
    page_size: int | None = None,
    since: str | None = None,
) -> list[dict]:
    """Drain buffered messages from SSEClient, with optional filtering."""
    if page_size is not None:
        items = sse_client.drain(max_items=page_size)
    else:
        items = sse_client.drain()

    if since is not None:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        filtered = []
        for task in items:
            ts_str = task.get("status", {}).get("timestamp", "")
            if ts_str:
                task_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if task_dt > since_dt:
                    filtered.append(task)
        items = filtered

    if page_size is not None:
        items = items[:page_size]

    return items


async def handle_send(*, forwarder: RegistryForwarder, to: str, text: str) -> dict:
    """Forward send to RegistryForwarder."""
    return await forwarder.send(to=to, text=text)


async def handle_broadcast(*, forwarder: RegistryForwarder, text: str) -> dict:
    """Forward broadcast to RegistryForwarder."""
    return await forwarder.broadcast(text=text)


async def handle_ack(*, forwarder: RegistryForwarder, task_id: str) -> dict:
    """Forward ack to RegistryForwarder."""
    return await forwarder.ack(task_id=task_id)


async def handle_cancel(*, forwarder: RegistryForwarder, task_id: str) -> dict:
    """Forward cancel to RegistryForwarder."""
    return await forwarder.cancel(task_id=task_id)


async def handle_get_task(*, forwarder: RegistryForwarder, task_id: str) -> dict:
    """Forward get_task to RegistryForwarder."""
    return await forwarder.get_task(task_id=task_id)


async def handle_agents(*, forwarder: RegistryForwarder, id: str | None = None) -> dict:
    """Forward agents to RegistryForwarder."""
    return await forwarder.agents(id=id)


async def handle_register(
    *,
    forwarder: RegistryForwarder,
    name: str,
    description: str,
    skills: str | None = None,
    api_key: str | None = None,
) -> dict:
    """Forward register to RegistryForwarder."""
    return await forwarder.register(
        name=name, description=description, skills=skills, api_key=api_key
    )


async def handle_deregister(*, forwarder: RegistryForwarder) -> dict:
    """Forward deregister to RegistryForwarder."""
    return await forwarder.deregister()


def _build_server() -> tuple[Server, SSEClient, RegistryForwarder]:
    """Build MCP server with tool definitions."""
    config = get_config()
    sse_client = SSEClient(
        broker_url=config["broker_url"],
        api_key=config["api_key"],
        agent_id=config["agent_id"],
    )
    forwarder = RegistryForwarder(
        broker_url=config["broker_url"],
        api_key=config["api_key"],
        agent_id=config["agent_id"],
    )

    server = Server("hikyaku-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="poll",
                description="Poll inbox for messages (returns from local SSE buffer)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "since": {
                            "type": "string",
                            "description": "Filter tasks since timestamp",
                        },
                        "page_size": {
                            "type": "integer",
                            "description": "Max number of tasks to return",
                        },
                    },
                },
            ),
            Tool(
                name="send",
                description="Send a unicast message to another agent",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient agent ID"},
                        "text": {"type": "string", "description": "Message text"},
                    },
                    "required": ["to", "text"],
                },
            ),
            Tool(
                name="broadcast",
                description="Broadcast a message to all agents in the tenant",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Message text"}
                    },
                    "required": ["text"],
                },
            ),
            Tool(
                name="ack",
                description="Acknowledge receipt of a message",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task ID to acknowledge",
                        }
                    },
                    "required": ["task_id"],
                },
            ),
            Tool(
                name="cancel",
                description="Cancel (retract) a sent message",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task ID to cancel",
                        }
                    },
                    "required": ["task_id"],
                },
            ),
            Tool(
                name="get_task",
                description="Get details of a specific task",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task ID to retrieve",
                        }
                    },
                    "required": ["task_id"],
                },
            ),
            Tool(
                name="agents",
                description="List registered agents or get agent detail",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Specific agent ID for detail view",
                        }
                    },
                },
            ),
            Tool(
                name="register",
                description="Register a new agent with the broker",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Agent name"},
                        "description": {
                            "type": "string",
                            "description": "Agent description",
                        },
                        "skills": {
                            "type": "string",
                            "description": "Skills as JSON string",
                        },
                        "api_key": {
                            "type": "string",
                            "description": "API key to join existing tenant",
                        },
                    },
                    "required": ["name", "description"],
                },
            ),
            Tool(
                name="deregister",
                description="Deregister this agent from the broker",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "poll":
            result = await handle_poll(
                sse_client=sse_client,
                page_size=arguments.get("page_size"),
                since=arguments.get("since"),
            )
        elif name == "send":
            result = await handle_send(
                forwarder=forwarder, to=arguments["to"], text=arguments["text"]
            )
        elif name == "broadcast":
            result = await handle_broadcast(forwarder=forwarder, text=arguments["text"])
        elif name == "ack":
            result = await handle_ack(forwarder=forwarder, task_id=arguments["task_id"])
        elif name == "cancel":
            result = await handle_cancel(
                forwarder=forwarder, task_id=arguments["task_id"]
            )
        elif name == "get_task":
            result = await handle_get_task(
                forwarder=forwarder, task_id=arguments["task_id"]
            )
        elif name == "agents":
            result = await handle_agents(forwarder=forwarder, id=arguments.get("id"))
        elif name == "register":
            result = await handle_register(
                forwarder=forwarder,
                name=arguments["name"],
                description=arguments["description"],
                skills=arguments.get("skills"),
                api_key=arguments.get("api_key"),
            )
        elif name == "deregister":
            result = await handle_deregister(forwarder=forwarder)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        return [TextContent(type="text", text=json.dumps(result, default=str))]

    return server, sse_client, forwarder


def main():
    """Entry point for hikyaku-mcp CLI."""

    async def _run():
        server, sse_client, _forwarder = _build_server()
        await sse_client.connect()
        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream, write_stream, server.create_initialization_options()
                )
        finally:
            await sse_client.disconnect()

    asyncio.run(_run())
