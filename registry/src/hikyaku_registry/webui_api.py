"""WebUI API endpoints for the Hikyaku message viewer."""

import asyncio
import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import (
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TextPart,
)

from hikyaku_registry.executor import BrokerExecutor
from hikyaku_registry.redis_client import get_redis
from hikyaku_registry.registry_store import RegistryStore
from hikyaku_registry.task_store import RedisTaskStore


webui_router = APIRouter(prefix="/ui/api")


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def get_webui_store() -> RegistryStore:
    return RegistryStore(get_redis())


def get_webui_task_store() -> RedisTaskStore:
    return RedisTaskStore(get_redis())


def get_webui_executor() -> BrokerExecutor:
    return BrokerExecutor(
        registry_store=get_webui_store(),
        task_store=get_webui_task_store(),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _extract_bearer(request: Request) -> str:
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(status_code=401)

    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1].strip():
        raise HTTPException(status_code=401)

    return parts[1].strip()


async def _get_tenant_agents(
    tenant_id: str,
    store: RegistryStore,
    task_store: RedisTaskStore,
) -> list[dict]:
    agents = []

    # Active agents
    active_agents = await store.list_active_agents(tenant_id=tenant_id)
    for a in active_agents:
        agents.append({
            "agent_id": a["agent_id"],
            "name": a["name"],
            "description": a["description"],
            "status": "active",
            "registered_at": a["registered_at"],
        })

    # Deregistered agents with messages
    cursor = 0
    while True:
        cursor, keys = await store._redis.scan(
            cursor=cursor, match="agent:*", count=100
        )
        for key in keys:
            record = await store._redis.hgetall(key)
            if (
                record.get("api_key_hash") == tenant_id
                and record.get("status") == "deregistered"
            ):
                agent_id = record["agent_id"]
                has_messages = await store._redis.zcard(
                    f"tasks:ctx:{agent_id}"
                )
                if has_messages > 0:
                    agents.append({
                        "agent_id": agent_id,
                        "name": record.get("name", ""),
                        "description": record.get("description", ""),
                        "status": "deregistered",
                        "registered_at": record.get("registered_at", ""),
                    })
        if cursor == 0:
            break

    return agents


async def _authenticate_tenant(
    request: Request,
    store: RegistryStore,
    task_store: RedisTaskStore,
) -> str:
    token = _extract_bearer(request)
    tenant_id = hashlib.sha256(token.encode()).hexdigest()

    # Quick check: active agents in tenant?
    active_count = await store._redis.scard(f"tenant:{tenant_id}:agents")
    if active_count > 0:
        return tenant_id

    # Slow check: deregistered agents with messages?
    cursor = 0
    while True:
        cursor, keys = await store._redis.scan(
            cursor=cursor, match="agent:*", count=100
        )
        for key in keys:
            record = await store._redis.hgetall(key)
            if (
                record.get("api_key_hash") == tenant_id
                and record.get("status") == "deregistered"
            ):
                agent_id = record["agent_id"]
                has_messages = await store._redis.zcard(
                    f"tasks:ctx:{agent_id}"
                )
                if has_messages > 0:
                    return tenant_id
        if cursor == 0:
            break

    raise HTTPException(status_code=401)


def _extract_body(task: Task) -> str:
    if not task.artifacts:
        return ""
    for artifact in task.artifacts:
        if artifact.parts:
            for part in artifact.parts:
                if isinstance(part.root, TextPart):
                    return part.root.text
    return ""


async def _resolve_agent_name(store: RegistryStore, agent_id: str) -> str:
    name = await store._redis.hget(f"agent:{agent_id}", "name")
    return name or ""


async def _format_message(
    task: Task,
    store: RegistryStore,
    task_store: RedisTaskStore,
) -> dict:
    metadata = task.metadata or {}
    from_id = metadata.get("fromAgentId", "")
    to_id = metadata.get("toAgentId", "")

    from_name = await _resolve_agent_name(store, from_id) if from_id else ""
    to_name = await _resolve_agent_name(store, to_id) if to_id else ""

    created_at = (
        await task_store._redis.hget(f"task:{task.id}", "created_at") or ""
    )

    return {
        "task_id": task.id,
        "from_agent_id": from_id,
        "from_agent_name": from_name,
        "to_agent_id": to_id,
        "to_agent_name": to_name,
        "type": metadata.get("type", ""),
        "status": task.status.state.name,
        "created_at": created_at,
        "body": _extract_body(task),
    }


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class SendMessageRequest(BaseModel):
    from_agent_id: str
    to_agent_id: str
    text: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@webui_router.post("/login")
async def login(
    request: Request,
    store: RegistryStore = Depends(get_webui_store),
    task_store: RedisTaskStore = Depends(get_webui_task_store),
):
    token = _extract_bearer(request)
    tenant_id = hashlib.sha256(token.encode()).hexdigest()

    agents = await _get_tenant_agents(tenant_id, store, task_store)
    if not agents:
        return JSONResponse(
            status_code=401, content={"error": "Invalid API key"}
        )

    return {"tenant_id": tenant_id, "agents": agents}


@webui_router.get("/agents")
async def list_agents(
    request: Request,
    store: RegistryStore = Depends(get_webui_store),
    task_store: RedisTaskStore = Depends(get_webui_task_store),
):
    tenant_id = await _authenticate_tenant(request, store, task_store)
    agents = await _get_tenant_agents(tenant_id, store, task_store)
    return {"agents": agents}


@webui_router.get("/agents/{agent_id}/inbox")
async def get_inbox(
    agent_id: str,
    request: Request,
    store: RegistryStore = Depends(get_webui_store),
    task_store: RedisTaskStore = Depends(get_webui_task_store),
):
    await _authenticate_tenant(request, store, task_store)

    tasks = await task_store.list(agent_id)
    tasks = [
        t
        for t in tasks
        if not (t.metadata and t.metadata.get("type") == "broadcast_summary")
    ]

    messages = []
    for task in tasks:
        msg = await _format_message(task, store, task_store)
        messages.append(msg)

    return {"messages": messages}


@webui_router.get("/agents/{agent_id}/sent")
async def get_sent(
    agent_id: str,
    request: Request,
    store: RegistryStore = Depends(get_webui_store),
    task_store: RedisTaskStore = Depends(get_webui_task_store),
):
    await _authenticate_tenant(request, store, task_store)

    task_ids = await task_store._redis.smembers(f"tasks:sender:{agent_id}")
    if not task_ids:
        return {"messages": []}

    filtered_tasks = []
    for task_id in task_ids:
        task = await task_store.get(task_id)
        if task is None:
            continue
        if task.metadata and task.metadata.get("type") == "broadcast_summary":
            continue
        filtered_tasks.append(task)

    # Sort by status timestamp descending (newest first)
    filtered_tasks.sort(key=lambda t: t.status.timestamp, reverse=True)

    messages = []
    for task in filtered_tasks:
        msg = await _format_message(task, store, task_store)
        messages.append(msg)

    return {"messages": messages}


@webui_router.post("/messages/send")
async def send_message(
    request: Request,
    body: SendMessageRequest,
    store: RegistryStore = Depends(get_webui_store),
    task_store: RedisTaskStore = Depends(get_webui_task_store),
    executor: BrokerExecutor = Depends(get_webui_executor),
):
    tenant_id = await _authenticate_tenant(request, store, task_store)

    # Verify from_agent belongs to tenant
    from_in_tenant = await store._redis.sismember(
        f"tenant:{tenant_id}:agents", body.from_agent_id
    )
    if not from_in_tenant:
        raise HTTPException(status_code=400, detail="from_agent not in tenant")

    # Verify to_agent exists and is active
    to_agent = await store.get_agent(body.to_agent_id)
    if to_agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if to_agent.get("status") == "deregistered":
        raise HTTPException(
            status_code=400, detail="Agent is deregistered"
        )

    # Verify to_agent is in same tenant
    to_in_tenant = await store.verify_agent_tenant(
        body.to_agent_id, tenant_id
    )
    if not to_in_tenant:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Build Message and execute
    msg = Message(
        message_id=str(uuid.uuid4()),
        role=Role("user"),
        parts=[Part(root=TextPart(text=body.text))],
        metadata={"destination": body.to_agent_id},
    )

    call_context = ServerCallContext(
        state={"agent_id": body.from_agent_id, "tenant_id": tenant_id}
    )
    send_params = MessageSendParams(message=msg)
    context = RequestContext(
        request=send_params,
        call_context=call_context,
    )

    event_queue = EventQueue()
    await executor.execute(context, event_queue)

    # Drain events to find the produced task
    last_task = None
    try:
        while True:
            event = event_queue.queue.get_nowait()
            if isinstance(event, Task):
                last_task = event
    except asyncio.QueueEmpty:
        pass

    if last_task is None:
        raise HTTPException(status_code=500, detail="No task produced")

    return {
        "task_id": last_task.id,
        "status": last_task.status.state.name,
    }
