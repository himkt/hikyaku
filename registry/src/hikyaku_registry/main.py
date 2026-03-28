import hashlib
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.default_request_handler import (
    DefaultRequestHandler,
)
from a2a.server.tasks import TaskStore
from a2a.types import Task

from hikyaku_registry.agent_card import build_agent_card
from hikyaku_registry.api.registry import registry_router
from hikyaku_registry.config import settings
from hikyaku_registry.executor import BrokerExecutor
from hikyaku_registry.redis_client import close_pool, get_redis
from hikyaku_registry.registry_store import RegistryStore
from hikyaku_registry.task_store import RedisTaskStore


class A2ATaskStoreAdapter(TaskStore):
    """Adapts RedisTaskStore to the a2a-sdk TaskStore interface."""

    def __init__(self, store: RedisTaskStore) -> None:
        self._store = store

    async def save(
        self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        await self._store.save(task)

    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        return await self._store.get(task_id)

    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        await self._store.delete(task_id)


def _build_call_context(request) -> ServerCallContext:
    """Extract agent_id from Bearer token and build ServerCallContext."""
    auth_header = request.headers.get("authorization", "")
    parts = auth_header.split(" ", 1)
    token = parts[1].strip() if len(parts) == 2 else ""
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else ""
    return ServerCallContext(state={"api_key_hash": token_hash})


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="Hikyaku Broker", version="0.1.0", lifespan=lifespan)
    app.include_router(registry_router, prefix="/api/v1")

    redis = get_redis()
    registry_store = RegistryStore(redis)
    task_store = RedisTaskStore(redis)
    executor = BrokerExecutor(
        registry_store=registry_store, task_store=task_store
    )

    agent_card = build_agent_card()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=A2ATaskStoreAdapter(task_store),
    )

    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )
    a2a_app.add_routes_to_app(app)

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "hikyaku_registry.main:app",
        host=settings.broker_host,
        port=settings.broker_port,
        reload=True,
    )
