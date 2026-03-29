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
                                                │  │ │ Pub/Sub Chans  │ │  │
         Tenant Y (different API key)           │  │ └────────────────┘ │  │
        ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐           │  └────────────────────┘  │
          ┌─────────────┐                       │                          │
        │ │   Agent C    │ (isolated) │         │  ┌────────────────────┐  │
          │ (discovery)  │                      │  │ SSE Endpoint       │  │
        │ └─────────────┘             │         │  │ /api/v1/subscribe  │  │
         ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─            │  └────────────────────┘  │
                                                └──────────────────────────┘

  ┌─────────────┐  MCP tools   ┌──────────────────────────────────────┐
  │ Claude Code  │─────────────→│  hikyaku-mcp (transparent proxy)    │
  │  (agent)     │  poll, send, │                                     │
  │              │  ack, ...    │  ┌──────────┐   ┌────────────────┐  │
  └─────────────┘              │  │  Buffer   │◄──│  SSE Client    │  │
                               │  │ (Queue)   │   │  (background)  │  │
                               │  └─────┬─────┘   └───────┬────────┘  │
                               │        │ poll            │ SSE       │
                               │  ┌─────┴─────┐   ┌──────┴─────────┐ │
                               │  │  Registry  │   │ /api/v1/       │ │
                               │  │  Forwarder │   │ subscribe      │ │
                               │  └─────┬─────┘   └──────┬─────────┘ │
                               └────────┼─────────────────┼───────────┘
                                        │ REST/JSON-RPC   │ SSE
                                        ▼                 ▼
                               ┌──────────────────────────────────────┐
                               │  hikyaku-registry (broker)           │
                               └──────────────────────────────────────┘
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

## Streaming Subscribe (SSE)

Real-time inbox notification via Server-Sent Events (SSE). Agents can subscribe to their inbox and receive messages as they arrive, instead of polling.

### Server-side: SSE Endpoint

- **Endpoint**: `GET /api/v1/subscribe` (authenticated via `Authorization: Bearer <api_key>` + `X-Agent-Id: <agent_id>`)
- **Response**: `text/event-stream` — SSE events with `event: message`, `id: <task_id>`, `data: <A2A Task JSON>`
- **Keepalive**: `: keepalive` comment every 30 seconds
- **Mechanism**: When `BrokerExecutor` delivers a message, it publishes the `task_id` to Redis Pub/Sub channel `inbox:{recipient_agent_id}`. The SSE endpoint subscribes to this channel, fetches the full Task from Redis, and streams it to the client.
- **Connection lifecycle**: Connection stays open until client disconnects. Server detects disconnect and unsubscribes from Redis Pub/Sub. No server-side replay — client uses `poll --since` to catch up on missed messages.

### Redis Pub/Sub Integration

- **PubSubManager** (`registry/src/hikyaku_registry/pubsub.py`): Manages Redis Pub/Sub subscriptions. Provides `publish(channel, message)` and async iteration over subscribed channels.
- **Channel pattern**: `inbox:{agent_id}` — one channel per agent inbox
- **Payload**: Only the `task_id` is published (lightweight). The SSE endpoint fetches the full Task from Redis to ensure data consistency.

### MCP Server (Transparent Proxy)

The `hikyaku-mcp` package (`mcp-server/`) is a transparent proxy that exposes the same tool interface as the `hikyaku` CLI but internally maintains an SSE connection to pre-buffer messages. The agent's workflow is unchanged — it calls `poll`, `send`, `ack`, etc. — but `poll` returns instantly from a local buffer.

- **SSE Client**: Background `asyncio.Task` connects to `/api/v1/subscribe` and buffers incoming Task objects in an `asyncio.Queue` (max 1000 messages, oldest dropped on overflow)
- **Registry Forwarder**: All non-poll tools (send, broadcast, ack, cancel, get_task, agents, register, deregister) forward requests to the registry via httpx
- **Poll**: Drains the local buffer; supports optional `since` filter and `page_size` limit
- **Configuration**: `HIKYAKU_URL`, `HIKYAKU_API_KEY`, `HIKYAKU_AGENT_ID` environment variables

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
| `pubsub.py` | `registry/src/hikyaku_registry/` | PubSubManager for Redis Pub/Sub (inbox notification channels) |
| `subscribe.py` | `registry/src/hikyaku_registry/api/` | SSE endpoint router (`GET /api/v1/subscribe`) |
| `webui_api.py` | `registry/src/hikyaku_registry/` | WebUI API router (`/ui/api/*`) — login, agents, inbox, sent, send |
| `admin/` | Project root | WebUI SPA (Vite + React + TypeScript + Tailwind CSS) |
| `cli.py` | `client/src/hikyaku_client/` | click group + subcommands |
| `api.py` | `client/src/hikyaku_client/` | Helper functions (httpx / a2a-sdk) |
| `output.py` | `client/src/hikyaku_client/` | Output formatting (tables + JSON) |
| `server.py` | `mcp-server/src/hikyaku_mcp/` | MCP server entry point + tool definitions |
| `sse_client.py` | `mcp-server/src/hikyaku_mcp/` | SSE connection manager (auto-connect, buffer) |
| `registry.py` | `mcp-server/src/hikyaku_mcp/` | Registry API forwarder (httpx) |
| `config.py` | `mcp-server/src/hikyaku_mcp/` | Environment variable configuration |

## Responsibility Assignment

The Broker acts as the central A2A Server. Individual agents are A2A clients that interact with the Broker using standard HTTP requests. No agent needs to host an HTTP server.

| Operation | Responsible | Method |
|---|---|---|
| Broker Agent Card serving | Broker | `GET /.well-known/agent-card.json` |
| Individual agent card storage | Broker (Registry) | `POST /api/v1/agents`, `GET /api/v1/agents/{id}` |
| Message sending | Sending agent (A2A client) | A2A `SendMessage` to Broker |
| Message storage & routing | Broker | Redis Task store, contextId-based routing |
| Message retrieval | Receiving agent (A2A client) | A2A `ListTasks(contextId=own_id)` to Broker |
| Real-time inbox notification | Broker | `GET /api/v1/subscribe` (SSE) via Redis Pub/Sub |
| Message ACK | Receiving agent (A2A client) | A2A `SendMessage(taskId=existing)` multi-turn |
| Message cancellation | Sending agent (A2A client) | A2A `CancelTask` to Broker |
| MCP proxy (all tools) | hikyaku-mcp | Transparent proxy with SSE-buffered poll |

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

A uv workspace monorepo with three packages and a frontend app:

- **`registry/`** — `hikyaku-registry`: FastAPI + Redis + a2a-sdk (server)
- **`client/`** — `hikyaku-client`: click + httpx + a2a-sdk (CLI tool)
- **`mcp-server/`** — `hikyaku-mcp`: MCP server transparent proxy (mcp + httpx + httpx-sse)
- **`admin/`** — WebUI SPA: Vite + React + TypeScript + Tailwind CSS

Agents can use `pip install hikyaku-client` for the CLI, or configure `hikyaku-mcp` as an MCP server for instant poll responses. The Broker server is deployed separately.
