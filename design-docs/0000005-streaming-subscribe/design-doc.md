# Streaming Subscribe (MCP Server)

**Status**: Approved
**Progress**: 11/22 tasks complete
**Last Updated**: 2026-03-29

## Overview

Add a server-side SSE endpoint for real-time inbox notification and an MCP server package that acts as a transparent proxy for hikyaku-registry. The MCP server exposes the same tool interface as the existing hikyaku CLI (poll, send, ack, etc.) but internally maintains an SSE connection to pre-buffer messages, making poll responses instant.

## Success Criteria

- [ ] Server pushes new messages to subscribed clients via SSE within 1 second of delivery
- [ ] MCP server's `poll` tool returns pre-buffered messages instantly (no round-trip to registry)
- [ ] MCP server's forwarding tools (send, ack, register, etc.) behave identically to the CLI
- [ ] Existing `hikyaku` CLI continues to work independently
- [ ] No messages are lost — missed messages during SSE disconnect are recoverable via `hikyaku poll --since` CLI as fallback

---

## Background

Hikyaku currently uses polling-based message retrieval: agents call `hikyaku poll` (or the `ListTasks` A2A operation) to check their inbox. This works but has latency proportional to the polling interval and wastes resources on empty polls.

For coding agents integrated via MCP, polling is especially awkward — the agent must either poll in a loop (burning context and API calls) or sleep between polls (adding latency). A transparent proxy MCP server solves this: it maintains a persistent SSE connection to the broker, pre-buffers incoming messages, and serves them instantly when the agent calls `poll`. The agent's workflow doesn't change — it still calls `poll`, `send`, `ack` as before — but poll responses are instant.

**Note**: This is a custom inbox-level notification stream, not the A2A-standard `SubscribeToTask` (per-task streaming) or `SendStreamingMessage`. The `AgentCard.capabilities.streaming` flag remains `false`.

---

## Specification

### Architecture

```
┌─────────────┐  MCP tools   ┌──────────────────────────────────────┐
│ Claude Code │─────────────►│  hikyaku-mcp (transparent proxy)     │
│  (agent)    │  poll, send, │                                      │
│             │  ack, ...    │  ┌──────────┐    ┌────────────────┐  │
└─────────────┘              │  │  Buffer   │◄───│  SSE Client    │  │
                             │  │ (Queue)   │    │  (background)  │  │
                             │  └─────┬─────┘    └───────┬────────┘  │
                             │        │ poll             │ SSE       │
                             │  ┌─────┴─────┐    ┌───────┴────────┐  │
                             │  │  Registry  │    │  /api/v1/      │  │
                             │  │  Forwarder │    │  subscribe     │  │
                             │  └─────┬─────┘    └───────┬────────┘  │
                             └────────┼──────────────────┼───────────┘
                                      │ REST/JSON-RPC    │ SSE
                                      ▼                  ▼
                             ┌──────────────────────────────────────┐
                             │  hikyaku-registry (broker)           │
                             │                                      │
                             │  Executor ──► Redis Pub/Sub ──► SSE  │
                             └──────────────────────────────────────┘
```

1. **Executor** delivers a message → publishes task_id to Redis Pub/Sub channel `inbox:{recipient_agent_id}`
2. **SSE endpoint** subscribes to the channel, fetches the full Task from Redis, and streams it as an SSE event
3. **MCP server** (transparent proxy) consumes the SSE stream into a local buffer; `poll` returns from buffer, other tools forward to registry

### Server-side: SSE Endpoint

#### Endpoint

| Property | Value |
|----------|-------|
| Method | `GET` |
| Path | `/api/v1/subscribe` |
| Auth | `Authorization: Bearer <api_key>` + `X-Agent-Id: <agent_id>` |
| Response | `text/event-stream` |

#### Event Format

```
event: message
id: <task_id>
data: <A2A Task JSON>

: keepalive
```

- **`message` events**: Sent when a new message arrives in the agent's inbox. The `data` field contains the full A2A Task JSON, the same format as individual items in the `ListTasks` response. The `id` field is the task_id.
- **Keepalive comments**: Sent every 30 seconds to prevent proxy/load-balancer timeouts. SSE comment lines (starting with `:`) are ignored by compliant clients.

#### Redis Pub/Sub Integration

When `BrokerExecutor` delivers a message (unicast or broadcast), it publishes to the recipient's channel. `BrokerExecutor.__init__` must accept a `PubSubManager` dependency (in addition to existing `registry_store` and `task_store`), and `create_app()` in `main.py` must wire it up.

```python
# In BrokerExecutor._handle_unicast / _handle_broadcast, after task_store.save(task):
await self._pubsub.publish(f"inbox:{recipient_agent_id}", task_id)
```

The SSE endpoint handler:
1. Subscribes to Redis Pub/Sub channel `inbox:{agent_id}`
2. On receiving a task_id notification, calls `task_store.get(task_id)` to fetch the full Task
3. Serializes the Task as JSON and sends as an SSE `message` event

Publishing only the task_id keeps Pub/Sub payloads lightweight. Fetching the full Task from Redis ensures the SSE event reflects the current stored state.

#### Connection Lifecycle

| Aspect | Behavior |
|--------|----------|
| Keepalive | `:keepalive\n\n` comment every 30 seconds |
| Idle timeout | None — connection stays open until client disconnects |
| Server shutdown | Graceful close via ASGI lifespan; unsubscribe all Pub/Sub channels |
| Client disconnect | Server detects closed connection, unsubscribes from Redis Pub/Sub, cleans up |
| Reconnection | No server-side replay; client uses `poll --since` to catch up on missed messages |

#### Authentication

Reuse the existing `get_authenticated_agent()` function from `hikyaku_registry.auth`. It returns `(agent_id, tenant_id)` or raises `HTTPException(401)` for all auth failures (missing header, invalid token, agent not found, tenant mismatch). The SSE endpoint uses it as a FastAPI dependency, consistent with existing `/api/v1/` routes.

| Condition | HTTP Status |
|-----------|-------------|
| Missing/malformed Authorization header | 401 |
| Missing X-Agent-Id header | 401 |
| Agent not found or tenant mismatch | 401 |

### MCP Server Package (Transparent Proxy)

The MCP server is a transparent proxy for hikyaku-registry. It exposes the same tool interface as the existing hikyaku CLI — agents call `poll`, `send`, `ack`, etc. with the same parameters and get the same response format. The only behavioral difference is that `poll` returns from a local buffer pre-populated via SSE, making it instant.

#### Package Structure

```
mcp-server/
├── pyproject.toml          # Package: hikyaku-mcp
├── src/
│   └── hikyaku_mcp/
│       ├── __init__.py
│       ├── server.py       # MCP server entry point + tool definitions
│       ├── sse_client.py   # SSE connection manager (internal)
│       ├── registry.py     # Registry API forwarder (httpx)
│       └── config.py       # Environment variable config
└── tests/
    ├── test_server.py
    ├── test_sse_client.py
    └── test_registry.py
```

**Entry point** in `pyproject.toml`:

```toml
[project.scripts]
hikyaku-mcp = "hikyaku_mcp.server:main"
```

**Workspace**: Add `"mcp-server"` to `[tool.uv.workspace] members` in the root `pyproject.toml` (currently `["registry", "client"]`).

**Dependencies**: `mcp`, `httpx`, `httpx-sse`

#### Configuration

| Env Var | Required | Description |
|---------|----------|-------------|
| `HIKYAKU_URL` | Yes | Broker URL (e.g., `http://localhost:8000`) |
| `HIKYAKU_API_KEY` | Yes | API key for authentication |
| `HIKYAKU_AGENT_ID` | Yes | Agent UUID |

#### MCP Server Lifecycle

On startup, the MCP server automatically establishes an SSE connection to `{HIKYAKU_URL}/api/v1/subscribe` using the configured credentials. A background `asyncio.Task` reads SSE events and buffers parsed Task objects in an internal `asyncio.Queue`. The agent is unaware of the SSE connection — it is purely an internal optimization.

On shutdown, the SSE connection is closed and the buffer is drained.

#### MCP Tools

All tools match the existing hikyaku CLI interface. The agent's workflow is identical to using the CLI — the only difference is transport (MCP tools instead of shell commands) and poll latency (instant from buffer instead of round-trip to registry).

##### `register`

Register a new agent with the broker.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | `str` | Yes | Agent name |
| `description` | `str` | Yes | Agent description |
| `skills` | `str` | No | Skills as JSON string |
| `api_key` | `str` | No | API key to join existing tenant |

**Behavior**: Forwards to `POST {HIKYAKU_URL}/api/v1/agents`. Returns the registration response (agent_id, api_key, etc.).

##### `send`

Send a unicast message to another agent.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `to` | `str` | Yes | Recipient agent ID |
| `text` | `str` | Yes | Message text |

**Behavior**: Forwards JSON-RPC `SendMessage` to registry. Returns A2A Task JSON.

##### `broadcast`

Broadcast a message to all agents in the tenant.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | `str` | Yes | Message text |

**Behavior**: Forwards JSON-RPC `SendMessage` with `destination: "*"` to registry. Returns A2A Task JSON.

##### `poll`

Poll inbox for messages. Returns pre-buffered messages from the SSE stream.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `since` | `str` | No | Filter tasks since timestamp |
| `page_size` | `int` | No | Max number of tasks to return |

**Behavior**:
- Drains the internal buffer (up to `page_size` if specified) and returns buffered messages as a list of A2A Task objects
- If the buffer is empty, returns an empty list (same as current poll returning no messages)
- If `since` is specified, filters buffered messages by timestamp
- Response format is identical to the CLI's `hikyaku poll --json` output

##### `ack`

Acknowledge receipt of a message.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | `str` | Yes | Task ID to acknowledge |

**Behavior**: Forwards JSON-RPC `SendMessage` (multi-turn with taskId) to registry. Returns A2A Task JSON.

##### `cancel`

Cancel (retract) a sent message.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | `str` | Yes | Task ID to cancel |

**Behavior**: Forwards JSON-RPC `CancelTask` to registry. Returns A2A Task JSON.

##### `get_task`

Get details of a specific task.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | `str` | Yes | Task ID to retrieve |

**Behavior**: Forwards JSON-RPC `GetTask` to registry. Returns A2A Task JSON.

##### `agents`

List registered agents or get agent detail.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | `str` | No | Specific agent ID for detail view |

**Behavior**: Forwards to `GET {HIKYAKU_URL}/api/v1/agents` (or `/agents/{id}`). Returns agent list or detail.

##### `deregister`

Deregister this agent from the broker.

**Behavior**: Forwards to `DELETE {HIKYAKU_URL}/api/v1/agents/{agent_id}`. Returns deregistration confirmation.

#### Tool Behavior Summary

| Tool | Behavior | Target |
|------|----------|--------|
| `register` | Forward | `POST /api/v1/agents` |
| `send` | Forward | JSON-RPC `SendMessage` |
| `broadcast` | Forward | JSON-RPC `SendMessage` (destination `*`) |
| `poll` | **Local buffer** | Internal `asyncio.Queue` (SSE-populated) |
| `ack` | Forward | JSON-RPC `SendMessage` (multi-turn) |
| `cancel` | Forward | JSON-RPC `CancelTask` |
| `get_task` | Forward | JSON-RPC `GetTask` |
| `agents` | Forward | `GET /api/v1/agents` |
| `deregister` | Forward | `DELETE /api/v1/agents/{id}` |

#### SSE Client (Internal)

```python
MAX_BUFFER_SIZE = 1000

class SSEClient:
    """Internal SSE connection manager. Auto-connects on MCP server startup."""

    def __init__(self, broker_url: str, api_key: str, agent_id: str):
        self.broker_url = broker_url
        self.api_key = api_key
        self.agent_id = agent_id
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_BUFFER_SIZE)
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient()
        self._task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        async with aconnect_sse(
            self._client, "GET",
            f"{self.broker_url}/api/v1/subscribe",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Agent-Id": self.agent_id,
            },
        ) as event_source:
            async for event in event_source.aiter_sse():
                if event.event == "message":
                    task = json.loads(event.data)
                    if self.queue.full():
                        # Drop oldest message to make room
                        self.queue.get_nowait()
                    await self.queue.put(task)

    def drain(self, max_items: int | None = None) -> list[dict]:
        """Drain buffered messages. Used by poll tool."""
        items = []
        while not self.queue.empty():
            if max_items and len(items) >= max_items:
                break
            items.append(self.queue.get_nowait())
        return items

    async def disconnect(self) -> None:
        if self._task:
            self._task.cancel()
        if self._client:
            await self._client.aclose()
        while not self.queue.empty():
            self.queue.get_nowait()
```

**Buffer overflow**: The queue is capped at 1000 messages. If the buffer is full (agent not consuming), the oldest message is dropped. Dropped messages can be recovered via the `hikyaku poll --since` CLI command.

#### Claude Code Configuration Example

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

#### Agent Workflow

The agent's workflow is unchanged from the CLI-based approach. The MCP server is transparent.

```
Agent                    MCP Server (proxy)       Registry
  │                          │                      │
  │                          │◄── SSE stream ───────│ (auto-connected on startup)
  │                          │                      │
  │── send(to, text) ───────►│── SendMessage ──────►│
  │◄── {task: ...} ─────────│◄── response ─────────│
  │                          │                      │
  │── poll() ───────────────►│  (drain buffer)      │
  │◄── [{task}, {task}] ────│                      │
  │                          │                      │
  │── ack(task_id) ─────────►│── SendMessage ──────►│
  │◄── {task: ...} ─────────│◄── response ─────────│
  │                          │                      │
  │── poll() ───────────────►│  (buffer empty)      │
  │◄── [] ──────────────────│                      │
  │                          │                      │
```

### Not in Scope

- **CLI `hikyaku subscribe` command** — MCP server handles SSE internally; no user-facing subscribe
- **A2A `SubscribeToTask` / `SendStreamingMessage`** — Standard per-task streaming; orthogonal to inbox-level subscribe
- **SSE reconnection with server-side replay** — Buffer misses recovered via `hikyaku poll --since` CLI
- **Browser EventSource / query-parameter auth** — Out of scope; may be addressed in WebUI design doc (0000004)
- **Multi-server horizontal scaling** — Target is < 10 agents per tenant; single-process Redis Pub/Sub is sufficient
- **Replacing the hikyaku CLI** — The CLI continues to work independently; MCP server is an alternative interface

---

## Implementation

> Task format: `- [x] Done task <!-- completed: 2026-02-13T14:30 -->`
> When completing a task, check the box and record the timestamp in the same edit.

### Step 1: Documentation & Architecture Update

- [x] Update `ARCHITECTURE.md` with streaming subscribe architecture (SSE endpoint, Redis Pub/Sub notification, MCP server package) <!-- completed: 2026-03-29T08:35 -->
- [x] Update `docs/` directory with streaming subscribe usage and configuration <!-- completed: 2026-03-29T08:35 -->
- [x] Update `/hikyaku` skill documentation with subscribe workflow <!-- completed: 2026-03-29T08:35 -->
- [x] Add MCP server configuration example to README <!-- completed: 2026-03-29T08:36 -->
- [x] Update project rules (`.claude/rules/`) with streaming subscribe endpoint and MCP server details <!-- completed: 2026-03-29T08:36 -->

### Step 2: Redis Pub/Sub Integration

- [x] Add `PubSubManager` class to registry (`registry/src/hikyaku_registry/pubsub.py`) for subscribing/publishing to `inbox:{agent_id}` channels <!-- completed: 2026-03-29T08:40 -->
- [x] Add `pubsub` parameter to `BrokerExecutor.__init__` and wire it up in `create_app()` (`main.py`) <!-- completed: 2026-03-29T08:40 -->
- [x] Integrate publish call into `BrokerExecutor._handle_unicast()` after `task_store.save()` <!-- completed: 2026-03-29T08:40 -->
- [x] Integrate publish call into `BrokerExecutor._handle_broadcast()` for each recipient <!-- completed: 2026-03-29T08:40 -->
- [x] Add unit tests for `PubSubManager` (publish, subscribe, unsubscribe) <!-- completed: 2026-03-29T08:40 -->
- [x] Add unit tests for executor publish integration (verify publish called on delivery) <!-- completed: 2026-03-29T08:40 -->

### Step 3: Server-side SSE Endpoint

- [ ] Add `GET /api/v1/subscribe` FastAPI endpoint with `Authorization` + `X-Agent-Id` authentication <!-- completed: -->
- [ ] Implement SSE `StreamingResponse` that reads from `PubSubManager` subscription <!-- completed: -->
- [ ] Add 30-second keepalive ping via periodic `:keepalive` comment <!-- completed: -->
- [ ] Add connection cleanup on client disconnect (unsubscribe from Redis Pub/Sub) <!-- completed: -->
- [ ] Add tests for SSE endpoint (auth errors, message streaming, keepalive, disconnect cleanup) <!-- completed: -->

### Step 4: MCP Server Package

- [ ] Create `mcp-server/` package with `pyproject.toml`, `[project.scripts]` entry point, and add to root workspace members <!-- completed: -->
- [ ] Implement `SSEClient` connection manager with auto-connect on startup in `sse_client.py` <!-- completed: -->
- [ ] Implement registry API forwarder in `registry.py` (send, broadcast, ack, cancel, get_task, agents, register, deregister) <!-- completed: -->
- [ ] Implement `poll` MCP tool that drains local buffer with optional `since` filter and `page_size` limit <!-- completed: -->
- [ ] Implement forwarding MCP tools (send, broadcast, ack, cancel, get_task, agents, register, deregister) <!-- completed: -->
- [ ] Add unit tests for MCP server tools (poll from buffer, forwarding, SSE auto-connect, buffer overflow) <!-- completed: -->

---

## Changelog (optional -- spec revisions only, not implementation progress)

| Date | Changes |
|------|---------|
| 2026-03-29 | Initial draft |
