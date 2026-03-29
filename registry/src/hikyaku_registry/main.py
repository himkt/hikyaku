import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
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

from hikyaku_registry.agent_card import build_agent_card
from hikyaku_registry.cleanup import cleanup_expired_agents
from hikyaku_registry.api.registry import (
    get_registry_store,
    registry_router,
)
from hikyaku_registry.api.subscribe import (
    subscribe_router,
    _get_pubsub,
    _get_task_store as _get_subscribe_task_store,
)
from hikyaku_registry.auth import get_authenticated_agent
from hikyaku_registry.config import settings
from hikyaku_registry.executor import BrokerExecutor
from hikyaku_registry.pubsub import PubSubManager
from hikyaku_registry.redis_client import close_pool, get_redis
from hikyaku_registry.registry_store import RegistryStore
from hikyaku_registry.task_store import RedisTaskStore
from hikyaku_registry.webui_api import (
    webui_router,
    get_webui_store,
    get_webui_task_store,
    get_webui_executor,
)


logger = logging.getLogger(__name__)


async def _cleanup_loop(redis: aioredis.Redis, ttl_days: int, interval: int) -> None:
    """Periodically clean up expired deregistered agents."""
    while True:
        try:
            count = await cleanup_expired_agents(redis, ttl_days=ttl_days)
            if count > 0:
                logger.info("Cleaned up %d expired agent(s)", count)
        except Exception:
            logger.exception("Error during agent cleanup")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = get_redis()
    cleanup_task = asyncio.create_task(
        _cleanup_loop(
            redis,
            ttl_days=settings.deregistered_task_ttl_days,
            interval=settings.cleanup_interval_seconds,
        )
    )
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await close_pool()


class SPAStaticFiles(StaticFiles):
    """StaticFiles subclass that falls back to index.html for SPA routing."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def _task_to_dict(task: Task) -> dict:
    """Serialize a Task to JSON-compatible dict with camelCase keys."""
    return task.model_dump(mode="json", by_alias=True)


def _jsonrpc_success(result: dict, req_id: str | None) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "result": result, "id": req_id})


def _jsonrpc_error(
    code: int, message: str, req_id: str | None, status_code: int = 200
) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id},
        status_code=status_code,
    )


async def _handle_send_message(
    executor: BrokerExecutor, agent_id: str, tenant_id: str, params: dict
) -> dict:
    """Handle SendMessage JSON-RPC method."""
    message_data = params["message"]

    parts = []
    for p in message_data.get("parts", []):
        if p.get("kind") == "text":
            parts.append(Part(root=TextPart(text=p["text"])))

    msg = Message(
        message_id=message_data.get("messageId"),
        role=Role(message_data.get("role", "user")),
        parts=parts,
        metadata=message_data.get("metadata"),
        task_id=message_data.get("taskId"),
    )

    call_context = ServerCallContext(
        state={"agent_id": agent_id, "tenant_id": tenant_id}
    )
    send_params = MessageSendParams(message=msg)

    context = RequestContext(
        request=send_params,
        task_id=message_data.get("taskId"),
        call_context=call_context,
    )

    event_queue = EventQueue()
    await executor.execute(context, event_queue)

    # Drain all events from the queue
    events = []
    try:
        while True:
            event = event_queue.queue.get_nowait()
            events.append(event)
    except asyncio.QueueEmpty:
        pass

    # Return the last Task event
    last_task = None
    for event in reversed(events):
        if isinstance(event, Task):
            last_task = event
            break

    if last_task is None:
        raise ValueError("No task produced by executor")

    return {"task": _task_to_dict(last_task)}


async def _handle_get_task(
    task_store: RedisTaskStore,
    tenant_id: str,
    registry_store: RegistryStore,
    params: dict,
) -> dict:
    """Handle GetTask JSON-RPC method."""
    task_id = params.get("id")
    if not task_id:
        raise ValueError("Missing task id")

    task = await task_store.get(task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    # Verify task belongs to caller's tenant
    from_agent = await registry_store._redis.hget(f"task:{task_id}", "from_agent_id")
    to_agent = await registry_store._redis.hget(f"task:{task_id}", "to_agent_id")

    from_ok = from_agent and await registry_store.verify_agent_tenant(
        from_agent, tenant_id
    )
    to_ok = to_agent and await registry_store.verify_agent_tenant(to_agent, tenant_id)
    if not from_ok and not to_ok:
        raise ValueError(f"Task {task_id} not found")

    return {"task": _task_to_dict(task)}


async def _handle_cancel_task(
    executor: BrokerExecutor, agent_id: str, tenant_id: str, params: dict
) -> dict:
    """Handle CancelTask JSON-RPC method."""
    task_id = params.get("id")
    if not task_id:
        raise ValueError("Missing task id")

    call_context = ServerCallContext(
        state={"agent_id": agent_id, "tenant_id": tenant_id}
    )
    context = RequestContext(
        task_id=task_id,
        call_context=call_context,
    )

    event_queue = EventQueue()
    await executor.cancel(context, event_queue)

    # Drain events
    events = []
    try:
        while True:
            event = event_queue.queue.get_nowait()
            events.append(event)
    except asyncio.QueueEmpty:
        pass

    last_task = None
    for event in reversed(events):
        if isinstance(event, Task):
            last_task = event
            break

    if last_task is None:
        raise ValueError("No task produced by executor")

    return {"task": _task_to_dict(last_task)}


async def _handle_list_tasks(
    task_store: RedisTaskStore, agent_id: str, params: dict
) -> dict:
    """Handle ListTasks JSON-RPC method."""
    context_id = params.get("contextId")
    if not context_id:
        raise ValueError("Missing contextId")

    if context_id != agent_id:
        raise ValueError("Forbidden: contextId does not match caller")

    status_filter = params.get("status")
    tasks = await task_store.list(context_id)

    # Filter out broadcast summary tasks (not actual messages)
    tasks = [
        t
        for t in tasks
        if not (t.metadata and t.metadata.get("type") == "broadcast_summary")
    ]

    if status_filter:
        tasks = [t for t in tasks if t.status.state.value == status_filter]

    return {"tasks": [_task_to_dict(t) for t in tasks]}


def create_app(
    redis: aioredis.Redis | None = None, webui_dist_dir: str | None = None
) -> FastAPI:
    app = FastAPI(title="Hikyaku Broker", version="0.1.0", lifespan=lifespan)
    app.include_router(registry_router, prefix="/api/v1")
    app.include_router(subscribe_router, prefix="/api/v1")

    if redis is None:
        redis = get_redis()
    registry_store = RegistryStore(redis)
    task_store = RedisTaskStore(redis)
    pubsub_manager = PubSubManager(redis)
    executor = BrokerExecutor(
        registry_store=registry_store,
        task_store=task_store,
        pubsub=pubsub_manager,
    )

    # Override dependencies so API endpoints use the same redis
    async def _get_store() -> RegistryStore:
        return registry_store

    async def _get_auth(request: Request) -> tuple[str, str]:
        return await get_authenticated_agent(request, store=registry_store)

    app.dependency_overrides[get_registry_store] = _get_store
    app.dependency_overrides[get_authenticated_agent] = _get_auth
    app.dependency_overrides[_get_pubsub] = lambda: pubsub_manager
    app.dependency_overrides[_get_subscribe_task_store] = lambda: task_store

    # WebUI router (must be included BEFORE StaticFiles mount)
    app.include_router(webui_router)
    app.dependency_overrides[get_webui_store] = lambda: registry_store
    app.dependency_overrides[get_webui_task_store] = lambda: task_store
    app.dependency_overrides[get_webui_executor] = lambda: executor

    # Agent Card endpoint
    agent_card = build_agent_card()

    @app.get("/.well-known/agent-card.json")
    async def get_agent_card():
        return JSONResponse(agent_card.model_dump(mode="json", by_alias=True))

    # JSON-RPC endpoint for A2A operations
    @app.post("/")
    async def jsonrpc_endpoint(request: Request):
        # Authenticate: extract Bearer token and X-Agent-Id header
        auth_header = request.headers.get("authorization", "")
        parts = auth_header.split(" ", 1)
        token = parts[1].strip() if len(parts) == 2 and parts[0] == "Bearer" else ""
        if not token:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

        tenant_id = hashlib.sha256(token.encode()).hexdigest()
        agent_id = request.headers.get("x-agent-id")
        if not agent_id:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

        # Verify agent belongs to tenant
        agent_key_hash = await registry_store._redis.hget(
            f"agent:{agent_id}", "api_key_hash"
        )
        if agent_key_hash is None or agent_key_hash != tenant_id:
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

        # Parse JSON-RPC request
        body = await request.json()
        method = body.get("method")
        params = body.get("params", {})
        req_id = body.get("id")

        # Route to handler
        try:
            if method == "SendMessage":
                result = await _handle_send_message(
                    executor, agent_id, tenant_id, params
                )
            elif method == "GetTask":
                result = await _handle_get_task(
                    task_store, tenant_id, registry_store, params
                )
            elif method == "CancelTask":
                result = await _handle_cancel_task(
                    executor, agent_id, tenant_id, params
                )
            elif method == "ListTasks":
                result = await _handle_list_tasks(task_store, agent_id, params)
            else:
                return _jsonrpc_error(-32601, "Method not found", req_id)

            return _jsonrpc_success(result, req_id)
        except (ValueError, PermissionError) as e:
            return _jsonrpc_error(-32000, str(e), req_id)

    # Mount StaticFiles for WebUI SPA (AFTER router so API routes take precedence)
    if webui_dist_dir is None:
        webui_dist_dir = str(
            Path(__file__).resolve().parent.parent.parent.parent / "admin" / "dist"
        )
    dist_path = Path(webui_dist_dir)
    if dist_path.exists():
        app.mount(
            "/ui",
            SPAStaticFiles(directory=str(dist_path)),
            name="webui",
        )

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "hikyaku_registry.main:app",
        host=settings.broker_host,
        port=settings.broker_port,
        reload=True,
    )
