from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from hikyaku_registry.auth import get_authenticated_agent
from hikyaku_registry.models import (
    ErrorDetail,
    ErrorResponse,
    ListAgentsResponse,
    RegisterAgentRequest,
    RegisterAgentResponse,
)
from hikyaku_registry.registry_store import RegistryStore
from hikyaku_registry.redis_client import get_redis

registry_router = APIRouter()


async def get_registry_store() -> RegistryStore:
    redis = get_redis()
    return RegistryStore(redis)


@registry_router.post("/agents", status_code=201, response_model=RegisterAgentResponse)
async def register_agent(
    body: RegisterAgentRequest,
    store: RegistryStore = Depends(get_registry_store),
):
    result = await store.create_agent(
        name=body.name,
        description=body.description,
        skills=body.skills,
    )
    return result


@registry_router.get("/agents", response_model=ListAgentsResponse)
async def list_agents(
    _agent_id: str = Depends(get_authenticated_agent),
    store: RegistryStore = Depends(get_registry_store),
):
    agents = await store.list_active_agents()
    return {"agents": agents}


@registry_router.get("/agents/{agent_id}")
async def get_agent_detail(
    agent_id: str,
    _caller_id: str = Depends(get_authenticated_agent),
    store: RegistryStore = Depends(get_registry_store),
):
    agent = await store.get_agent(agent_id)
    if agent is None or agent.get("status") == "deregistered":
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="AGENT_NOT_FOUND",
                    message=f"Agent with id '{agent_id}' not found",
                )
            ).model_dump(),
        )
    return agent


@registry_router.delete("/agents/{agent_id}")
async def deregister_agent(
    agent_id: str,
    caller_id: str = Depends(get_authenticated_agent),
    store: RegistryStore = Depends(get_registry_store),
):
    agent = await store.get_agent(agent_id)
    if agent is None or agent.get("status") == "deregistered":
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="AGENT_NOT_FOUND",
                    message=f"Agent with id '{agent_id}' not found",
                )
            ).model_dump(),
        )

    if agent["agent_id"] != caller_id:
        return JSONResponse(
            status_code=403,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="FORBIDDEN",
                    message="API key does not match the target resource",
                )
            ).model_dump(),
        )

    await store.deregister_agent(agent_id)
    return Response(status_code=204)
