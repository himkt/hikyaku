# Hikyaku — Architecture

An A2A-native message broker and agent registry for coding agents. Enables ephemeral agents (Claude Code, CI/CD runners, etc.) to communicate via unicast and broadcast messaging using standard A2A protocol operations. Agents are organized into **tenants** via shared API keys — agents sharing the same key form a tenant and can discover and message each other; agents in different tenants are invisible to one another.

## Architecture Diagram

```
         Tenant X (shared API key)              ┌──────────────────────────┐
        ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐           │         Broker           │
                                                │                          │
        │ ┌─────────────┐           │           │  ┌────────────────────┐  │
          │   Agent A    │ SendMessage            │  │ A2A Server        │  │
        │ │  (sender)    │──────────────────────→│  │ (tenant-scoped)   │  │
          └─────────────┘ Authorization:          │  └────────┬─────────┘  │
        │                  Bearer <api_key>      │           │             │
                           X-Agent-Id: <id>       │           ▼             │
        │ ┌─────────────┐           │           │  ┌────────────────────┐  │
          │   Agent B    │ ListTasks              │  │ Redis              │  │
        │ │ (recipient)  │←─────────────────────│  │ ┌────────────────┐ │  │
          └─────────────┘           │           │  │ │ Agent Store    │ │  │
        │                                       │  │ │ Task Store     │ │  │
         ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─            │  │ │ Tenant Sets    │ │  │
                                                │  │ └────────────────┘ │  │
         Tenant Y (different API key)           │  └────────────────────┘  │
        ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐           │                          │
          ┌─────────────┐                       │                          │
        │ │   Agent C    │ (isolated) │         │                          │
          │ (discovery)  │                      │                          │
        │ └─────────────┘             │         └──────────────────────────┘
         ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
```

## Tenant Isolation

The API key serves as the tenant boundary. All agents sharing the same API key form a tenant. The `SHA-256(api_key)` hash is stored as `api_key_hash` in agent records and used as the `tenant_id`.

**Authentication requires two headers on all requests (except registration)**:

| Header | Purpose |
|---|---|
| `Authorization: Bearer <api_key>` | Authenticates the tenant |
| `X-Agent-Id: <agent_id>` | Identifies the specific agent within the tenant |

**Registration** has two flows:
- **No `Authorization` header**: Creates a new tenant with a fresh API key
- **With `Authorization: Bearer <api_key>`**: Joins an existing tenant

**Isolation rules**: Every operation that reads or writes agent/task data enforces tenant boundaries. Cross-tenant requests always produce "not found" errors indistinguishable from the resource not existing.

## Three API Surfaces

1. **A2A Server** — Full A2A operations: SendMessage, GetTask, ListTasks, CancelTask (JSON-RPC 2.0)
2. **Registry** — Agent registration, search, listing (custom REST at `/api/v1/`)
3. **WebUI** — Browser-based message viewer and sender (SPA at `/ui/`, API at `/ui/api/`)

## Component Layout

| Component | Location | Description |
|---|---|---|
| `main.py` | `registry/src/hikyaku_registry/` | ASGI app: mount A2A + FastAPI |
| `config.py` | `registry/src/hikyaku_registry/` | Settings via pydantic-settings |
| `auth.py` | `registry/src/hikyaku_registry/` | API key + X-Agent-Id auth, tenant membership verification (shared by REST + A2A) |
| `redis_client.py` | `registry/src/hikyaku_registry/` | Redis connection pool |
| `models.py` | `registry/src/hikyaku_registry/` | Pydantic models (Registry API) |
| `executor.py` | `registry/src/hikyaku_registry/` | BrokerExecutor (A2A AgentExecutor) |
| `task_store.py` | `registry/src/hikyaku_registry/` | RedisTaskStore (A2A TaskStore for Redis) |
| `agent_card.py` | `registry/src/hikyaku_registry/` | Broker's own Agent Card definition |
| `registry_store.py` | `registry/src/hikyaku_registry/` | Agent CRUD on Redis (tenant-scoped) |
| `api/registry.py` | `registry/src/hikyaku_registry/api/` | Registry API router |
| `webui_api.py` | `registry/src/hikyaku_registry/` | WebUI API router (`/ui/api/*`) — login, agents, inbox, sent, send |
| `admin/` | Project root | WebUI SPA (Vite + React + TypeScript + Tailwind CSS) |
| `cli.py` | `client/src/hikyaku_client/` | click group + subcommands |
| `api.py` | `client/src/hikyaku_client/` | Helper functions (httpx / a2a-sdk) |
| `output.py` | `client/src/hikyaku_client/` | Output formatting (tables + JSON) |

## Responsibility Assignment

The Broker acts as the central A2A Server. Individual agents are A2A clients that interact with the Broker using standard HTTP requests. No agent needs to host an HTTP server.

| Operation | Responsible | Method |
|---|---|---|
| Broker Agent Card serving | Broker | `GET /.well-known/agent-card.json` |
| Individual agent card storage | Broker (Registry) | `POST /api/v1/agents`, `GET /api/v1/agents/{id}` |
| Message sending | Sending agent (A2A client) | A2A `SendMessage` to Broker |
| Message storage & routing | Broker | Redis Task store, contextId-based routing |
| Message retrieval | Receiving agent (A2A client) | A2A `ListTasks(contextId=own_id)` to Broker |
| Message ACK | Receiving agent (A2A client) | A2A `SendMessage(taskId=existing)` multi-turn |
| Message cancellation | Sending agent (A2A client) | A2A `CancelTask` to Broker |

## Key Design Decisions

### contextId Convention

The Broker sets `contextId = recipient_agent_id` on every delivery Task. This enables inbox discovery — recipients call `ListTasks(contextId=myAgentId)` to find all messages addressed to them. This trades per-conversation grouping (the typical contextId use case) for simple inbox discovery, which suits the fire-and-forget messaging pattern of coding agents. The A2A spec (Section 3.4.1) states that server-generated contextId values should be treated as opaque identifiers by clients, so this usage is compliant.

### Task Lifecycle Mapping

Each message delivery is modeled as an A2A Task:

| Task State | Message Meaning |
|---|---|
| `TASK_STATE_INPUT_REQUIRED` | Message queued, awaiting recipient pickup (unread) |
| `TASK_STATE_COMPLETED` | Message acknowledged by recipient |
| `TASK_STATE_CANCELED` | Message retracted by sender before ACK |
| `TASK_STATE_FAILED` | Routing error (returned immediately to sender) |

### ASGI Mount Strategy

FastAPI is the parent ASGI application. The A2A SDK's `A2AStarletteApplication` is mounted at the root path. FastAPI routes (`/api/v1/*`) take priority; A2A protocol paths fall through to the mounted Starlette app.

```python
from fastapi import FastAPI
from a2a.server.apps.starlette import A2AStarletteApplication

fastapi_app = FastAPI()
fastapi_app.include_router(registry_router, prefix="/api/v1")

a2a_app = A2AStarletteApplication(agent_card=broker_card, http_handler=handler)
fastapi_app.mount("/", a2a_app.build())
```

## WebUI

A browser-based message viewer served as a SPA at `/ui/`. Operators log in with their tenant API key and can browse agents, view message history (inbox/sent), and send unicast messages.

- **Frontend**: `admin/` — Vite + React 19 + TypeScript + Tailwind CSS 4
- **Backend API**: `/ui/api/*` endpoints in `webui_api.py` — login, agent list, inbox, sent, send
- **Auth**: Bearer API key in `Authorization` header (same key as A2A API). No server-side session; key stored in browser memory only.
- **Static serving**: `StaticFiles` mount at `/ui` serves `admin/dist/` (production build)

## Monorepo Structure

A uv workspace monorepo with two packages and a frontend app:

- **`registry/`** — `hikyaku-registry`: FastAPI + Redis + a2a-sdk (server)
- **`client/`** — `hikyaku-client`: click + httpx + a2a-sdk (CLI tool)
- **`admin/`** — WebUI SPA: Vite + React + TypeScript + Tailwind CSS

Agents only need `pip install hikyaku-client`. The Broker server is deployed separately.
