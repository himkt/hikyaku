# A2A Operations Specification

The Broker exposes standard A2A endpoints using the `a2a-sdk` Python library. All operations use JSON-RPC 2.0 over HTTP.

## Authentication

All A2A operations require two headers:

| Header | Purpose |
|---|---|
| `Authorization: Bearer <api_key>` | Authenticates the tenant (`SHA-256(api_key)` = `tenant_id`) |
| `X-Agent-Id: <agent_id>` | Identifies the specific agent within the tenant |

The server verifies that the agent record's `api_key_hash` matches `SHA-256(api_key)`. Missing or invalid credentials return HTTP 401 before JSON-RPC processing.

## SendMessage — Send a Message

The sender specifies routing in `Message.metadata`:

| metadata field | Value | Behavior |
|---|---|---|
| `destination` | `"<agent-uuid>"` | Unicast: create delivery Task for target agent |
| `destination` | `"*"` | Broadcast: create delivery Tasks for all active agents (except sender) |

### Tenant Isolation

All SendMessage operations enforce tenant isolation:

- **Unicast**: The Broker verifies the destination agent's `api_key_hash` matches the sender's tenant. If the destination is in a different tenant, the Broker returns an "agent not found" JSON-RPC error (indistinguishable from the agent not existing).
- **Broadcast (`*`)**: The Broker queries only the sender's tenant set (`tenant:{api_key_hash}:agents`) for the recipient list, instead of all active agents. Agents in other tenants never receive the broadcast.

### Unicast — Send

1. Agent A calls `SendMessage` with `metadata.destination = "agentB-uuid"`
2. Broker verifies Agent B belongs to the same tenant as Agent A
3. Broker creates Task: `contextId=agentB-uuid`, state=`INPUT_REQUIRED`, message content in Artifact
4. Broker returns the Task to Agent A (Agent A now has the `taskId` for tracking)

**Example** (JSON-RPC):

```json
{
  "jsonrpc": "2.0",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "msg-uuid-1",
      "role": "ROLE_USER",
      "parts": [
        {"text": "Did the API schema change?"}
      ],
      "metadata": {
        "destination": "agentB-uuid"
      }
    }
  },
  "id": "req-1"
}
```

**Response** (delivery Task returned):

```json
{
  "jsonrpc": "2.0",
  "result": {
    "task": {
      "id": "task-delivery-uuid",
      "contextId": "agentB-uuid",
      "status": {
        "state": "TASK_STATE_INPUT_REQUIRED",
        "timestamp": "2026-03-28T12:00:00.000Z"
      },
      "artifacts": [
        {
          "artifactId": "msg-content-uuid",
          "name": "message",
          "parts": [{"text": "Did the API schema change?"}],
          "metadata": {
            "fromAgentId": "agentA-uuid",
            "fromAgentName": "Agent A",
            "type": "unicast"
          }
        }
      ],
      "metadata": {
        "fromAgentId": "agentA-uuid",
        "toAgentId": "agentB-uuid",
        "type": "unicast"
      }
    }
  },
  "id": "req-1"
}
```

### Broadcast — Send

1. Agent A calls `SendMessage` with `metadata.destination = "*"`
2. Broker queries `tenant:{api_key_hash}:agents` for same-tenant agents (excluding sender)
3. Broker creates N delivery Tasks (one per same-tenant active agent, excluding sender), each with `contextId = recipient_agent_id`
4. Broker returns a summary Task to Agent A (state=`COMPLETED`) with Artifact listing `recipientCount` and `deliveryTaskIds`

**Response** (summary Task returned):

```json
{
  "jsonrpc": "2.0",
  "result": {
    "task": {
      "id": "task-broadcast-uuid",
      "contextId": "broadcast-ctx-uuid",
      "status": {
        "state": "TASK_STATE_COMPLETED",
        "timestamp": "2026-03-28T12:00:00.000Z"
      },
      "artifacts": [
        {
          "artifactId": "broadcast-receipt-uuid",
          "name": "broadcast_receipt",
          "parts": [
            {
              "data": {
                "recipientCount": 5,
                "deliveryTaskIds": ["task-1", "task-2", "task-3", "task-4", "task-5"]
              },
              "mediaType": "application/json"
            }
          ]
        }
      ],
      "metadata": {
        "fromAgentId": "agentA-uuid",
        "type": "broadcast"
      }
    }
  },
  "id": "req-2"
}
```

### Broadcast — Receive & ACK

Same as unicast — each recipient independently discovers and ACKs their own delivery Task.

## SendMessage — Acknowledge (Multi-Turn)

The recipient ACKs by sending a follow-up message referencing the delivery Task's `taskId`:

```json
{
  "jsonrpc": "2.0",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "ack-uuid-1",
      "role": "ROLE_USER",
      "taskId": "task-delivery-uuid",
      "parts": [
        {"text": "ack"}
      ]
    }
  },
  "id": "req-3"
}
```

**Behavior**: Broker validates that the authenticated agent is the recipient of this Task, then moves it to `TASK_STATE_COMPLETED`. Returns the updated Task.

The ACK message content (parts) is optional — an empty parts array or a simple `"ack"` text suffices. The Broker records the ACK in the Task's history.

## ListTasks — Poll Inbox

Recipients discover unread messages by calling `ListTasks` with their `agent_id` as `contextId`:

```json
{
  "jsonrpc": "2.0",
  "method": "ListTasks",
  "params": {
    "contextId": "agentB-uuid",
    "status": "TASK_STATE_INPUT_REQUIRED",
    "includeArtifacts": true,
    "pageSize": 20
  },
  "id": "req-4"
}
```

**Response**: Standard A2A `ListTasksResponse` with Tasks containing message Artifacts.

**Authorization**: The `contextId` parameter **must** equal the caller's `agent_id`. If a different `contextId` is provided, the Broker returns an error. This prevents agents from snooping on other agents' inboxes — even within the same tenant.

The `statusTimestampAfter` parameter can be used for efficient delta polling — only fetch tasks updated since the last poll.

## GetTask — Read Specific Message

Either sender or recipient can read a specific Task by ID:

```json
{
  "jsonrpc": "2.0",
  "method": "GetTask",
  "params": {
    "id": "task-delivery-uuid"
  },
  "id": "req-5"
}
```

**Visibility**: The Broker verifies that the task's `from_agent_id` or `to_agent_id` belongs to the caller's tenant. If neither matches, returns "not found" (indistinguishable from the task not existing). This enforces cross-tenant isolation for task access.

## CancelTask — Retract Message

Sender can retract an unread message:

```json
{
  "jsonrpc": "2.0",
  "method": "CancelTask",
  "params": {
    "id": "task-delivery-uuid"
  },
  "id": "req-6"
}
```

**Behavior**: Only the sender can cancel. Only tasks in `INPUT_REQUIRED` state can be canceled. Returns `TaskNotCancelableError` (JSON-RPC code `-32002`) if the task is already completed or canceled.

## Message Lifecycle

### Task State Mapping

| Task State | Message Meaning |
|---|---|
| `TASK_STATE_INPUT_REQUIRED` | Message queued, awaiting recipient pickup (unread) |
| `TASK_STATE_COMPLETED` | Message acknowledged by recipient |
| `TASK_STATE_CANCELED` | Message retracted by sender before ACK |
| `TASK_STATE_FAILED` | Routing error (returned immediately to sender) |

### Unicast Flow

1. Sender calls `SendMessage` with `metadata.destination = "<agent-uuid>"`
2. Broker creates Task (`INPUT_REQUIRED`), returns to sender
3. Recipient calls `ListTasks(contextId=own_id, status=INPUT_REQUIRED)`
4. Recipient reads message from Task artifacts
5. Recipient calls `SendMessage(taskId=existing)` to ACK
6. Broker moves Task to `COMPLETED`

### Broadcast Flow

1. Sender calls `SendMessage` with `metadata.destination = "*"`
2. Broker creates N delivery Tasks (one per active agent excluding sender)
3. Broker returns summary Task (`COMPLETED`) to sender
4. Each recipient independently discovers and ACKs their delivery Task

## Error Cases

| Condition | JSON-RPC Error | Code |
|---|---|---|
| Missing `Authorization` header | HTTP 401 before JSON-RPC processing | N/A |
| Missing `X-Agent-Id` header | HTTP 401 before JSON-RPC processing | N/A |
| Invalid API key or agent-tenant mismatch | HTTP 401 before JSON-RPC processing | N/A |
| Missing `metadata.destination` | `InvalidParams`: "metadata.destination is required" | `-32602` |
| `destination` is not a valid UUID or `"*"` | `InvalidParams`: "invalid destination format" | `-32602` |
| Destination agent not found or in different tenant | `InvalidParams`: "destination agent not found" | `-32602` |
| Destination agent is deregistered | `InvalidParams`: "destination agent is deregistered" | `-32602` |
| `ListTasks` with `contextId` != caller's `agent_id` | `InvalidParams`: "contextId must match caller's agent_id" | `-32602` |
| `GetTask` for task in different tenant | `TaskNotFoundError` | `-32001` |
| ACK on task not owned by caller | `TaskNotFoundError` | `-32001` |
| ACK on task in terminal state (completed/canceled) | `UnsupportedOperationError`: "task is in terminal state" | `-32004` |
| GetTask/CancelTask on unknown or unauthorized task | `TaskNotFoundError` | `-32001` |
