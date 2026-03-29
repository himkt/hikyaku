# Streaming Subscribe Specification

Real-time inbox notification via Server-Sent Events (SSE) and a transparent MCP server proxy.

## SSE Endpoint

### `GET /api/v1/subscribe`

Subscribe to real-time inbox notifications. Messages are pushed as SSE events as they arrive.

**Authentication**: Same as all `/api/v1/` endpoints — requires both headers:

| Header | Purpose |
|---|---|
| `Authorization: Bearer <api_key>` | Authenticates the tenant |
| `X-Agent-Id: <agent_id>` | Identifies the subscribing agent |

**Response**: `text/event-stream`

### Event Format

```
event: message
id: <task_id>
data: <A2A Task JSON>

: keepalive
```

- **`message` events**: Sent when a new message arrives in the agent's inbox. The `data` field contains the full A2A Task JSON (same format as individual items in `ListTasks` response). The `id` field is the `task_id`.
- **Keepalive comments**: Sent every 30 seconds to prevent proxy/load-balancer timeouts. SSE comment lines (starting with `:`) are ignored by compliant clients.

### Error Responses

| Condition | HTTP Status |
|---|---|
| Missing/malformed Authorization header | 401 |
| Missing X-Agent-Id header | 401 |
| Agent not found or tenant mismatch | 401 |

### Connection Lifecycle

| Aspect | Behavior |
|---|---|
| Keepalive | `: keepalive\n\n` comment every 30 seconds |
| Idle timeout | None — connection stays open until client disconnects |
| Server shutdown | Graceful close via ASGI lifespan; unsubscribe all Pub/Sub channels |
| Client disconnect | Server detects closed connection, unsubscribes from Redis Pub/Sub, cleans up |
| Reconnection | No server-side replay; client uses `poll --since` to catch up on missed messages |

## Redis Pub/Sub Integration

When `BrokerExecutor` delivers a message (unicast or broadcast), it publishes the `task_id` to the recipient's Redis Pub/Sub channel.

- **Channel pattern**: `inbox:{agent_id}`
- **Payload**: `task_id` only (lightweight)
- **Flow**: Executor saves Task to Redis, then publishes `task_id` to `inbox:{recipient_agent_id}`. The SSE endpoint subscriber receives the notification, fetches the full Task from Redis, and streams it as an SSE event.

Publishing only the `task_id` keeps Pub/Sub payloads small. Fetching the full Task from Redis ensures the SSE event reflects the current stored state.

## MCP Server (Transparent Proxy)

The `hikyaku-mcp` package is a transparent MCP server proxy for `hikyaku-registry`. It exposes the same tool interface as the `hikyaku` CLI — agents call `poll`, `send`, `ack`, etc. with the same parameters and get the same response format. The only behavioral difference is that `poll` returns from a local buffer pre-populated via SSE.

### Configuration

| Env Var | Required | Description |
|---|---|---|
| `HIKYAKU_URL` | Yes | Broker URL (e.g., `http://localhost:8000`) |
| `HIKYAKU_API_KEY` | Yes | API key for authentication |
| `HIKYAKU_AGENT_ID` | Yes | Agent UUID |

### MCP Tools

| Tool | Behavior | Target |
|---|---|---|
| `register` | Forward | `POST /api/v1/agents` |
| `send` | Forward | JSON-RPC `SendMessage` |
| `broadcast` | Forward | JSON-RPC `SendMessage` (destination `*`) |
| `poll` | **Local buffer** | Internal `asyncio.Queue` (SSE-populated) |
| `ack` | Forward | JSON-RPC `SendMessage` (multi-turn) |
| `cancel` | Forward | JSON-RPC `CancelTask` |
| `get_task` | Forward | JSON-RPC `GetTask` |
| `agents` | Forward | `GET /api/v1/agents` |
| `deregister` | Forward | `DELETE /api/v1/agents/{id}` |

### SSE Client (Internal)

The MCP server maintains a background SSE connection to `/api/v1/subscribe`. Incoming Task objects are buffered in an `asyncio.Queue` (max 1000 messages). When the buffer is full, the oldest message is dropped. Dropped messages can be recovered via the `hikyaku poll --since` CLI command.

### Claude Code Configuration

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
