# Hikyaku

A2A-native message broker and agent registry for coding agents.

Hikyaku enables ephemeral agents -- such as Claude Code sessions, CI/CD runners, and other coding agents -- to discover each other and exchange messages using the standard [A2A (Agent-to-Agent) protocol](https://github.com/google/A2A). Agents do not need to host HTTP servers; the broker handles all message routing and storage. Agents are organized into **tenants** via shared API keys -- agents sharing the same key form a tenant and can discover and message each other; agents in different tenants are invisible to one another.

## Features

- **Agent Registry** -- Register, discover, and deregister agents via REST API
- **Tenant Isolation** -- Shared API key defines a tenant boundary; cross-tenant agents are fully invisible to each other
- **Unicast Messaging** -- Send messages to a specific agent by ID (same-tenant only)
- **Broadcast Messaging** -- Send messages to all agents in the same tenant
- **Inbox Polling** -- Agents poll for new messages at their own pace; supports delta polling via `statusTimestampAfter`
- **Message Lifecycle** -- Acknowledge, cancel (retract), and track message status
- **Two-Header Auth** -- API key (tenant) + Agent-Id (identity) required on all authenticated requests
- **WebUI** -- Browser-based message viewer and sender; operators log in with their tenant API key to browse agents and message history
- **CLI Tool** -- Full-featured command-line client for all broker operations

## Architecture

```
     Tenant X (shared API key)              +---------------------------+
    +- - - - - - - - - - - - -+             |        Broker             |
                                            |                           |
    | +-----------+            |            |  +-----------------+      |
      |  Agent A  | SendMessage             |  | A2A Server      |      |
    | | (sender)  |--------------------------->| (tenant-scoped) |      |
      +-----------+ Authorization:          |  +-------+---------+      |
    |               Bearer <api_key>        |          |                |
                    X-Agent-Id: <id>        |          v                |
    | +-----------+            |            |  +-----------------+      |
      |  Agent B  | ListTasks               |  | Redis           |      |
    | |(recipient)|<------------------------   | Agent Store     |      |
      +-----------+            |            |  | Task Store      |      |
    +- - - - - - - - - - - - -+            |  | Tenant Sets     |      |
                                            |  +-----------------+      |
     Tenant Y (different API key)           +---------------------------+
    +- - - - - - - - - - - - -+
      +-----------+
    | |  Agent C  | (isolated) |
      | (no access|
    | +-----------+            |
    +- - - - - - - - - - - - -+
```

Key design decisions:

- The API key is the tenant boundary. `SHA-256(api_key)` is stored as `tenant_id`. All agents with the same key form one tenant.
- The `contextId` field is set to the recipient's agent ID on every delivery Task, enabling inbox discovery via `ListTasks(contextId=myAgentId)`.
- Task states map to message lifecycle: `INPUT_REQUIRED` (unread), `COMPLETED` (acknowledged), `CANCELED` (retracted), `FAILED` (routing error).
- FastAPI is the ASGI parent; the A2A SDK handler is mounted at the root path. FastAPI routes (`/api/v1/*`) take priority.
- Tenants are ephemeral: when the last agent in a tenant deregisters, the API key becomes invalid. A new registration without auth is needed to create a fresh tenant.
- The broker exposes three API surfaces: A2A Server (JSON-RPC 2.0), Registry REST API (`/api/v1/`), and WebUI (`/ui/`).

## Quick Start

### Prerequisites

- Python 3.12+
- Redis (running locally or via Docker)
- [uv](https://docs.astral.sh/uv/)

### Start the Broker Server

```bash
cd registry
uv run uvicorn hikyaku_registry.main:app
```

The broker will be available at `http://localhost:8000`.

### Install the CLI Client

```bash
cd client
uv tool install .
```

### Register an Agent (new tenant)

```bash
hikyaku register --name "my-agent" --description "A coding assistant"
```

This creates a new tenant and returns an agent ID and API key. Save both -- the API key is shown only once.

### Register a Second Agent (join existing tenant)

```bash
hikyaku --api-key "hky_..." register --name "my-second-agent" --description "Another agent"
```

Providing `--api-key` (or `HIKYAKU_API_KEY`) at registration joins the existing tenant.

### Set Credentials

```bash
export HIKYAKU_API_KEY="hky_..."
export HIKYAKU_AGENT_ID="<your-agent-id>"
```

### Send a Message

```bash
hikyaku send --to <recipient-agent-id> --text "Hello from my agent"
```

### Poll for Messages

```bash
hikyaku poll
```

### Acknowledge a Message

```bash
hikyaku ack --task-id <task-id>
```

## CLI Usage

All commands accept `--url` (default: `http://localhost:8000`), `--api-key`, `--agent-id`, and `--json` flags. Credentials can also be set via environment variables (`HIKYAKU_URL`, `HIKYAKU_API_KEY`, `HIKYAKU_AGENT_ID`).

| Command | Description |
|---|---|
| `hikyaku register` | Register a new agent; omit `--api-key` to create a new tenant, include it to join an existing one |
| `hikyaku send` | Send a unicast message to another agent in the same tenant |
| `hikyaku broadcast` | Broadcast a message to all agents in the same tenant |
| `hikyaku poll` | Poll inbox for incoming messages |
| `hikyaku ack` | Acknowledge receipt of a message |
| `hikyaku cancel` | Cancel (retract) a sent message before it is acknowledged |
| `hikyaku get-task` | Get details of a specific task/message |
| `hikyaku agents` | List agents in the tenant or get detail for a specific agent |
| `hikyaku deregister` | Deregister this agent from the broker |

## API Overview

### Authentication

All requests (except initial registration and the Agent Card endpoint) require two headers:

| Header | Purpose |
|---|---|
| `Authorization: Bearer <api_key>` | Authenticates the tenant (`SHA-256(api_key)` = `tenant_id`) |
| `X-Agent-Id: <agent_id>` | Identifies the specific agent within the tenant |

API keys use the format `hky_` + 32 random hex characters (e.g., `hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4`).

### Registry API (REST)

Base path: `/api/v1`

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/agents` | Optional | Register a new agent; no auth creates a new tenant, auth header joins existing tenant |
| GET | `/api/v1/agents` | Bearer + Agent-Id | List agents in the caller's tenant |
| GET | `/api/v1/agents/{id}` | Bearer + Agent-Id | Get agent detail (404 if not in same tenant) |
| DELETE | `/api/v1/agents/{id}` | Bearer + Agent-Id | Deregister an agent (self only) |
| GET | `/.well-known/agent-card.json` | None | Broker's own A2A Agent Card |

Registry API errors use a consistent JSON envelope:

```json
{
  "error": {
    "code": "AGENT_NOT_FOUND",
    "message": "Agent with id '...' not found"
  }
}
```

| Error Code | HTTP Status | Description |
|---|---|---|
| `UNAUTHORIZED` | 401 | Missing or invalid API key, missing `X-Agent-Id`, or agent-tenant mismatch |
| `FORBIDDEN` | 403 | API key does not match the target resource |
| `AGENT_NOT_FOUND` | 404 | Agent does not exist, is deregistered, or belongs to a different tenant |
| `INVALID_REQUEST` | 400 | Missing required fields or invalid values |

### A2A Operations (JSON-RPC 2.0)

| Method | Description |
|---|---|
| `SendMessage` | Send unicast (`metadata.destination=<agent-uuid>`) or broadcast (`metadata.destination=*`) |
| `ListTasks` | Poll inbox -- use `contextId=<own-agent-id>` to retrieve messages addressed to this agent; supports `statusTimestampAfter` for delta polling |
| `GetTask` | Retrieve a specific message by task ID |
| `CancelTask` | Retract an unread message (sender only, `INPUT_REQUIRED` state only) |

`ListTasks` enforces that `contextId` must equal the caller's agent ID. Providing a different `contextId` returns an error to prevent inbox snooping.

### Message Lifecycle

| Task State | Meaning |
|---|---|
| `TASK_STATE_INPUT_REQUIRED` | Message queued, awaiting recipient pickup (unread) |
| `TASK_STATE_COMPLETED` | Message acknowledged by recipient |
| `TASK_STATE_CANCELED` | Message retracted by sender before ACK |
| `TASK_STATE_FAILED` | Routing error (returned immediately to sender) |

### WebUI API

Base path: `/ui/api`

The WebUI API is consumed by the browser SPA. Authentication uses `Authorization: Bearer <api_key>` only (no `X-Agent-Id` required). No server-side session; the key lives in browser memory.

| Method | Endpoint | Description |
|---|---|---|
| POST | `/ui/api/login` | Validate API key; returns tenant agent list |
| GET | `/ui/api/agents` | List agents in the tenant |
| GET | `/ui/api/agents/{id}/inbox` | Inbox messages for an agent (newest first) |
| GET | `/ui/api/agents/{id}/sent` | Sent messages for an agent (newest first) |
| POST | `/ui/api/messages/send` | Send a unicast message between two same-tenant agents |

The WebUI SPA is served as static files at `/ui/`. It is built from `admin/` (Vite + React + TypeScript + Tailwind CSS).

## Tech Stack

- **Python 3.12+** with uv workspace
- **Server**: FastAPI + Redis + a2a-sdk + Pydantic + pydantic-settings
- **CLI**: click + httpx + a2a-sdk
- **WebUI**: Vite + React 19 + TypeScript + Tailwind CSS 4

## Project Structure

```
hikyaku/
  pyproject.toml          # Workspace root (uv workspace)
  registry/               # hikyaku-registry server package
    src/hikyaku_registry/
    tests/
    pyproject.toml
  client/                 # hikyaku-client CLI package
    src/hikyaku_client/
    tests/
    pyproject.toml
  admin/                  # WebUI SPA (Vite + React + TypeScript + Tailwind CSS)
  docs/
    spec/                 # API and data model specifications
      registry-api.md
      a2a-operations.md
      data-model.md
      webui-api.md
  ARCHITECTURE.md         # System architecture and design decisions
```

## Development

```bash
# Clone the repository
git clone https://github.com/himkt/hikyaku.git
cd hikyaku

# Install all workspace dependencies
uv sync

# Run registry tests
cd registry
uv run pytest tests/ -v

# Run client tests
cd client
uv run pytest tests/ -v
```

## License

MIT
