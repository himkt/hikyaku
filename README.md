# Hikyaku

A2A-native message broker and agent registry for coding agents.

Hikyaku enables ephemeral agents -- such as Claude Code sessions, CI/CD runners, and other coding agents -- to discover each other and exchange messages using the standard [A2A (Agent-to-Agent) protocol](https://github.com/google/A2A). Agents do not need to host HTTP servers; the broker handles all message routing and storage. Agents are organized into **tenants** via shared API keys -- agents sharing the same key form a tenant and can discover and message each other; agents in different tenants are invisible to one another.

## Features

- **Agent Registry** -- Register, discover, and deregister agents via REST API
- **Tenant Isolation** -- Shared API key defines a tenant boundary; cross-tenant agents are fully invisible to each other
- **Unicast Messaging** -- Send messages to a specific agent by ID (same-tenant only)
- **Broadcast Messaging** -- Send messages to all agents in the same tenant
- **Inbox Polling** -- Agents poll for new messages at their own pace; supports delta polling via `statusTimestampAfter`
- **Real-time Inbox Notification** -- SSE endpoint (`/api/v1/subscribe`) pushes new messages as they arrive
- **MCP Server** -- Transparent proxy for Claude Code and other MCP clients; `poll` returns instantly from SSE-buffered messages
- **Message Lifecycle** -- Acknowledge, cancel (retract), and track message status
- **Two-Header Auth** -- API key (tenant) + Agent-Id (identity) required on all authenticated requests
- **WebUI** -- Browser-based dashboard with Auth0 login; users manage API keys, select tenants, and browse agents and message history
- **CLI Tool** -- Full-featured command-line client for all broker operations

## Architecture

```
     Tenant X (shared API key)            +----------------------------+
    + - - - - - - - - - - - - +           |          Broker            |
                                          |                            |
    | +-------------+         |           |  +--------------------+    |
      |  Agent A     | SendMessage        |  | A2A Server         |    |
    | |  (sender)    |---------------------> | (tenant-scoped)    |    |
      +-------------+ Authorization:      |  +--------+-----------+    |
    |                  Bearer <api_key>   |           |                |
                       X-Agent-Id: <id>   |           v                |
    | +-------------+         |           |  +--------------------+    |
      |  Agent B     | ListTasks          |  | Redis              |    |
    | | (recipient)  |<-------------------+  | +----------------+ |    |
      +-------------+         |           |  | | Agent Store    | |    |
    |                                     |  | | Task Store     | |    |
     - - - - - - - - - - - - -            |  | | Tenant Sets    | |    |
                                          |  | | Pub/Sub Chans  | |    |
     Tenant Y (different API key)         |  | +----------------+ |    |
    + - - - - - - - - - - - - +           |  +--------------------+    |
      +-------------+                     |                            |
    | |  Agent C     | (isolated) |       |  +--------------------+    |
      | (discovery)  |                    |  | SSE Endpoint       |    |
    | +-------------+            |        |  | /api/v1/subscribe  |    |
     - - - - - - - - - - - - -            |  +--------------------+    |
                                          +----------------------------+

  +---------------+  MCP tools   +--------------------------------------+
  | Claude Code   |------------->|  hikyaku-mcp (transparent proxy)     |
  |  (agent)      |  poll, send, |                                      |
  |               |  ack, ...    |  +----------+   +----------------+   |
  +---------------+              |  | Buffer   |<--| SSE Client     |   |
                                 |  | (Queue)  |   | (background)   |   |
                                 |  +----+-----+   +-------+--------+   |
                                 |       | poll            | SSE        |
                                 |  +----+-----+   +-------+--------+   |
                                 |  | Registry  |   | /api/v1/       |  |
                                 |  | Forwarder |   | subscribe      |  |
                                 |  +----+-----+   +-------+--------+   |
                                 +-------+------------------+-----------+
                                         | REST/JSON-RPC    | SSE
                                         v                  v
                                 +--------------------------------------+
                                 |  hikyaku-registry (broker)           |
                                 +--------------------------------------+
```

Key design decisions:

- The API key is the tenant boundary. `SHA-256(api_key)` is stored as `tenant_id`. All agents with the same key form one tenant.
- The `contextId` field is set to the recipient's agent ID on every delivery Task, enabling inbox discovery via `ListTasks(contextId=myAgentId)`.
- Task states map to message lifecycle: `INPUT_REQUIRED` (unread), `COMPLETED` (acknowledged), `CANCELED` (retracted), `FAILED` (routing error).
- FastAPI is the ASGI parent; the A2A SDK handler is mounted at the root path. FastAPI routes (`/api/v1/*`) take priority.
- Tenants are created via WebUI API key management. Revoking a key deregisters all agents and invalidates the tenant. An empty tenant (no agents) remains valid as long as its key is active.
- The broker exposes three API surfaces: A2A Server (JSON-RPC 2.0), Registry REST API (`/api/v1/`), and WebUI (`/ui/`).
- The WebUI uses Auth0 for user authentication. Agent-to-broker communication uses API keys (unchanged).

## Quick Start

### Prerequisites

- Python 3.12+
- Redis (running locally or via Docker)
- [uv](https://docs.astral.sh/uv/)
- [Auth0](https://auth0.com/) account (for WebUI user authentication)

### Start the Broker Server

```bash
mise //registry:dev
```

The broker will be available at `http://localhost:8000`.

### Install the CLI Client

```bash
cd client
uv tool install .
```

### Create an API Key

Log into the WebUI at `http://localhost:8000/ui/` via Auth0, then create an API key from the key management page. The raw key is shown only once -- save it securely.

### Set Credentials

```bash
export HIKYAKU_API_KEY="hky_..."
export HIKYAKU_URL="http://localhost:8000"   # optional, defaults to http://localhost:8000
```

### Register an Agent

```bash
hikyaku register --name "my-agent" --description "A coding assistant"
```

Save the returned `agent_id` for subsequent commands. Registration always requires a valid `HIKYAKU_API_KEY`.

### Send a Message

```bash
hikyaku send --agent-id <your-agent-id> --to <recipient-agent-id> --text "Hello from my agent"
```

### Poll for Messages

```bash
hikyaku poll --agent-id <your-agent-id>
```

### Acknowledge a Message

```bash
hikyaku ack --agent-id <your-agent-id> --task-id <task-id>
```

## CLI Usage

Credentials are set via environment variables:

| Variable | Required | Description |
|---|---|---|
| `HIKYAKU_API_KEY` | Yes | API key for tenant authentication |
| `HIKYAKU_URL` | No | Broker URL (default: `http://localhost:8000`) |

The `--agent-id` option is a per-subcommand option required by most commands. The global `--json` flag enables JSON output.

| Command | `--agent-id` | Description |
|---|---|---|
| `hikyaku register` | Not required | Register a new agent; returns an agent ID |
| `hikyaku send` | Required | Send a unicast message to another agent in the same tenant |
| `hikyaku broadcast` | Required | Broadcast a message to all agents in the same tenant |
| `hikyaku poll` | Required | Poll inbox for incoming messages |
| `hikyaku ack` | Required | Acknowledge receipt of a message |
| `hikyaku cancel` | Required | Cancel (retract) a sent message before it is acknowledged |
| `hikyaku get-task` | Required | Get details of a specific task/message |
| `hikyaku agents` | Required | List agents in the tenant or get detail for a specific agent |
| `hikyaku deregister` | Required | Deregister this agent from the broker |

## API Overview

### Authentication

**Agent-to-broker**: All requests require two headers:

| Header | Purpose |
|---|---|
| `Authorization: Bearer <api_key>` | Authenticates the tenant (`SHA-256(api_key)` = `tenant_id`) |
| `X-Agent-Id: <agent_id>` | Identifies the specific agent within the tenant |

API keys use the format `hky_` + 32 random hex characters (e.g., `hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4`). Keys are created through the WebUI key management interface (requires Auth0 login).

**WebUI**: Auth0 JWT in `Authorization` header. Tenant-scoped endpoints additionally require `X-Tenant-Id` header.

### Registry API (REST)

Base path: `/api/v1`

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/agents` | Bearer | Register a new agent; API key (created via WebUI) is always required |
| GET | `/api/v1/agents` | Bearer + Agent-Id | List agents in the caller's tenant |
| GET | `/api/v1/agents/{id}` | Bearer + Agent-Id | Get agent detail (404 if not in same tenant) |
| DELETE | `/api/v1/agents/{id}` | Bearer + Agent-Id | Deregister an agent (self only) |
| GET | `/api/v1/subscribe` | Bearer + Agent-Id | SSE stream for real-time inbox notifications |
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

The WebUI API is consumed by the browser SPA. Authentication uses Auth0 JWT (`Authorization: Bearer <auth0_jwt>`). Tenant-scoped endpoints require an `X-Tenant-Id` header to select which tenant's data to view.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/ui/api/auth/config` | None | Returns Auth0 domain + client_id for SPA initialization |
| POST | `/ui/api/keys` | JWT | Create a new API key (raw key shown once) |
| GET | `/ui/api/keys` | JWT | List API keys owned by the authenticated user |
| DELETE | `/ui/api/keys/{tenant_id}` | JWT | Revoke an API key and deregister all its agents |
| GET | `/ui/api/agents` | JWT + Tenant-Id | List agents in the selected tenant |
| GET | `/ui/api/agents/{id}/inbox` | JWT + Tenant-Id | Inbox messages for an agent (newest first) |
| GET | `/ui/api/agents/{id}/sent` | JWT + Tenant-Id | Sent messages for an agent (newest first) |
| POST | `/ui/api/messages/send` | JWT + Tenant-Id | Send a unicast message between two same-tenant agents |

The WebUI SPA is served as static files at `/ui/`. It is built from `admin/` (Vite + React + TypeScript + Tailwind CSS).

## MCP Server (Claude Code Integration)

The `hikyaku-mcp` package provides a transparent MCP server proxy. It exposes the same tools as the CLI but `poll` returns instantly from a local SSE-buffered queue.

### Configuration

Add to your Claude Code `settings.json`:

```json
{
  "mcpServers": {
    "hikyaku": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/hikyaku/mcp-server", "hikyaku-mcp"],
      "env": {
        "HIKYAKU_URL": "http://localhost:8000",
        "HIKYAKU_API_KEY": "hky_...",
        "HIKYAKU_AGENT_ID": "..."
      }
    }
  }
}
```

The MCP server auto-connects to the broker's SSE endpoint on startup. All tools (send, broadcast, ack, cancel, get_task, agents, register, deregister) forward to the registry; `poll` drains the local buffer.

## Tech Stack

- **Python 3.12+** with uv workspace
- **Server**: FastAPI + Redis + a2a-sdk + Pydantic + pydantic-settings
- **CLI**: click + httpx + a2a-sdk
- **MCP Server**: mcp + httpx + httpx-sse
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
  mcp-server/             # hikyaku-mcp MCP server package
    src/hikyaku_mcp/
    tests/
    pyproject.toml
  admin/                  # WebUI SPA (Vite + React + TypeScript + Tailwind CSS)
  docs/
    spec/                 # API and data model specifications
      registry-api.md
      a2a-operations.md
      data-model.md
      webui-api.md
      streaming-subscribe.md
      cli-options.md
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
mise test

# Run client tests
cd client
mise test

# Run MCP server tests
cd mcp-server
mise test
```

## License

MIT
