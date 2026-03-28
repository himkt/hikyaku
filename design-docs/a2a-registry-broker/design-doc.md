# Hikyaku

**Status**: Complete
**Progress**: 40/40 tasks complete
**Last Updated**: 2026-03-28

## Overview

An A2A-native Message Broker and Agent Registry service that enables coding agents to communicate via unicast and broadcast messaging. The Broker models every message delivery as an A2A Task, so agents use standard A2A operations (SendMessage, ListTasks, GetTask, CancelTask) for the full message lifecycle — no custom mailbox protocol required. Only agent registration/discovery uses a custom REST API, since A2A does not prescribe a registry standard.

## Success Criteria

- [x] Agents can self-register and receive a UUID + API key
- [x] Agents can discover other agents via the Registry API
- [x] Agent A can send a unicast message to Agent B via A2A SendMessage
- [x] Agent A can broadcast a message to all registered agents via A2A SendMessage
- [x] Recipient agents can discover unread messages via A2A ListTasks
- [x] Recipient agents can ACK messages via A2A SendMessage (multi-turn)
- [x] Senders can retract unread messages via A2A CancelTask
- [x] FIFO ordering is guaranteed per sender→receiver pair
- [x] The Broker serves a valid A2A Agent Card at `/.well-known/agent-card.json`

---

## Background

The A2A (Agent2Agent) protocol v1.0 by Google defines a standard for agent-to-agent communication over HTTP using JSON-RPC 2.0. It assumes agents run as HTTP servers that can receive incoming requests. However, coding agents like Claude Code are often ephemeral, behind NAT, and cannot host servers.

This service bridges that gap by acting as an intermediary: the Broker is an A2A Server that receives messages and models each delivery as a Task in `INPUT_REQUIRED` state. Recipients poll via `ListTasks` (standard A2A) to discover unread messages, and acknowledge via multi-turn `SendMessage` — all using operations already defined in the A2A spec. The A2A specification explicitly does not prescribe a standard registry API (spec Section 8.2), so agent registration is a custom REST addition.

**Key design decisions**:
- All message delivery is queue-based. The Broker never forwards messages directly to agents.
- Every message delivery is an A2A Task. No custom mailbox protocol — standard A2A operations handle the full lifecycle.
- `contextId = recipient_agent_id` enables inbox discovery via `ListTasks`.

### Related Work: Solace Agent Mesh (SAM)

Solace Agent Mesh (SAM) is an open-source framework (Apache 2.0) for building event-driven multi-agent systems. It uses A2A message formats for inter-agent communication, making it the closest existing project to compare against. The table below clarifies key architectural differences.

| Aspect | SAM | This Project |
|---|---|---|
| **Transport** | Solace Event Broker topics (Pub/Sub) | Standard A2A HTTP endpoints (JSON-RPC 2.0) |
| **Message broker** | Solace Platform (commercial, required) | Redis (OSS) |
| **A2A compliance** | A2A message format over proprietary Solace topics | Standard A2A HTTP operations: SendMessage, ListTasks, GetTask, CancelTask |
| **Agent discovery** | Distributed Pub/Sub: each component publishes AgentCard to discovery topic, peers maintain local in-memory `AgentRegistry` | Centralized REST API (`/api/v1/agents`) backed by Redis |
| **Ephemeral agent support** | Not supported — agents must be always-on and connected to Solace broker | Core design goal — agents poll via `ListTasks`, no persistent connection required |
| **Agent framework** | Google ADK required for agent logic | Framework-agnostic — any HTTP client can interact |
| **Runtime** | Solace AI Connector (SAC) hosts all components | Standalone FastAPI + uvicorn microservice |
| **Orchestration** | Built-in Orchestrator agent for task decomposition and delegation (`PeerAgentTool`) | Not in scope — Broker routes messages, does not orchestrate |
| **Dependencies** | Solace Platform + Google ADK + SAC + a2a-sdk | FastAPI + Redis + a2a-sdk |
| **Vendor lock-in** | Solace Platform required | None |

**Key differentiators of this project**:

1. **Ephemeral-first**: SAM assumes agents are long-running processes connected to the Solace broker. This project is designed specifically for ephemeral agents (Claude Code, CI/CD runners, etc.) that start, do work, and terminate — they poll for messages when alive and messages queue when they're not.

2. **Standard A2A HTTP**: SAM uses A2A as a message serialization format but transmits via Solace proprietary topics, meaning a standard A2A client cannot interact with SAM without the Solace SDK. This project exposes standard A2A HTTP endpoints — any A2A-compliant client can send messages and poll tasks without proprietary libraries.

3. **Zero vendor dependency**: SAM requires a Solace Event Broker instance (cloud or self-hosted). This project runs on Redis, available as a single Docker container.

4. **Minimal scope**: SAM is a full framework (gateway management, orchestration, LLM integration, artifact storage). This project is a focused microservice with two responsibilities: message brokering and agent registry.

### Related Work: Eclipse LMOS Protocol

Eclipse LMOS is an open-source project under the Eclipse Foundation aiming to create an "Internet of Agents (IoA)" — a standardized layer for agent description and discovery built on W3C Web of Things (WoT) architecture.

| Aspect | LMOS | This Project |
|---|---|---|
| **Focus** | Agent description and discovery standardization | Message brokering + agent registry |
| **Protocol** | W3C WoT, JSON-LD for metadata | A2A (JSON-RPC 2.0 over HTTP) |
| **Discovery** | Three mechanisms: centralized registry (WoT Thing Description Directory), mDNS for local networks, distributed (AT Protocol / DHT) | Centralized REST API (`/api/v1/agents`) |
| **Messaging** | Not defined — focuses on discovery, not message transport | A2A Task-based message queue (SendMessage, ListTasks, GetTask) |
| **Identity** | W3C DID-based cryptographic identity | API key (SHA-256 hashed) |
| **Maturity** | Architectural guidance and specification drafts | Concrete implementation with defined API contracts |

**Relationship**: LMOS and this project are **complementary, not competing**. LMOS standardizes how agents describe themselves and are discovered across networks (the "phone book" layer), while this project provides the actual message delivery infrastructure (the "postal service" layer). A future integration path exists: the Broker's Registry could expose agent metadata in LMOS-compatible JSON-LD format, or adopt LMOS discovery mechanisms (e.g., WoT Thing Description Directory) as an alternative discovery frontend while keeping the A2A Task-based messaging backend unchanged.

---

## Specification

### Architecture

```
┌─────────────┐                        ┌──────────────────────┐
│   Agent A    │  A2A SendMessage       │      Broker          │
│  (sender)    │ ────────────────────→  │                      │
└─────────────┘  Authorization:         │  ┌────────────────┐  │
                  Bearer <api_key>      │  │ A2A Server     │  │
                                        │  │ (all ops)      │  │
┌─────────────┐  A2A ListTasks          │  └───────┬────────┘  │
│   Agent B    │ ←───────────────────── │          │            │
│ (recipient)  │  A2A GetTask           │          ▼            │
│              │  A2A SendMessage (ACK)  │  ┌────────────────┐  │
│              │  A2A CancelTask         │  │ Redis          │  │
└─────────────┘                         │  │ Task Store     │  │
                                        │  │ Agent Store    │  │
┌─────────────┐  GET /api/v1/agents     │  └────────────────┘  │
│   Agent C    │ ←───────────────────── │                      │
│ (discovery)  │                        └──────────────────────┘
└─────────────┘
```

Two API surfaces:
1. **A2A Server** — Full A2A operations: SendMessage, GetTask, ListTasks, CancelTask (JSON-RPC)
2. **Registry** — Agent registration, search, listing (custom REST at `/api/v1/`)

**Why a custom Registry API instead of A2A's standard Agent Discovery?**
A2A's standard discovery (`/.well-known/agent-card.json`) assumes each agent has its own domain where it hosts its Agent Card. Ephemeral agents have no domain or HTTP server. The Broker provides a centralized registry where agents self-register and others can list all registered agents in one call. The A2A specification explicitly acknowledges this gap: "The current A2A specification does not prescribe a standard API for curated registries" (Section 8.2). The Broker's own Agent Card is served at `/.well-known/agent-card.json` per the A2A standard; the custom `/api/v1/agents` endpoints are for the registry of *client* agents that communicate through the Broker.

**Task operations backed by Redis:**
All A2A Task operations (ListTasks, GetTask, CancelTask) are backed by `RedisTaskStore`, which implements the `a2a-sdk`'s `TaskStore` interface with Redis as the persistent storage layer. Redis serves as both the queue system (sorted sets for ordered task indexing) and the data store (hashes for task records). This gives the Broker durable, ordered message storage with efficient filtering by contextId and status.

#### Responsibility Assignment

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

### A2A Task Model for Message Delivery

Each message delivery is modeled as an A2A Task. The Task lifecycle maps to the message delivery lifecycle:

| Task State | Message Meaning |
|---|---|
| `TASK_STATE_INPUT_REQUIRED` | Message queued, awaiting recipient pickup (unread) |
| `TASK_STATE_COMPLETED` | Message acknowledged by recipient |
| `TASK_STATE_CANCELED` | Message retracted by sender before ACK |
| `TASK_STATE_FAILED` | Routing error (returned immediately to sender) |

**contextId convention**: The Broker sets `contextId = recipient_agent_id` on every delivery Task. This is the key that enables inbox discovery — recipients call `ListTasks(contextId=myAgentId)` to find all messages addressed to them. This trades per-conversation grouping (the typical contextId use case) for simple inbox discovery, which suits the fire-and-forget messaging pattern of coding agents. The A2A spec (Section 3.4.1) states that server-generated contextId values should be treated as opaque identifiers by clients, so this usage is compliant.

**Message content in Artifacts**: The original message content is stored as an Artifact on the Task, following A2A's separation of communication (Messages) from output (Artifacts). The Artifact metadata carries routing information (sender, type).

#### Message Flows

**Unicast — Send:**

1. Agent A calls `SendMessage` with `metadata.destination = "agentB-uuid"`
2. Broker creates Task: `contextId=agentB-uuid`, state=`INPUT_REQUIRED`, message content in Artifact
3. Broker returns the Task to Agent A (Agent A now has the `taskId` for tracking)

**Unicast — Receive & ACK:**

4. Agent B calls `ListTasks(contextId=agentB-uuid, status=TASK_STATE_INPUT_REQUIRED, includeArtifacts=true)`
5. Broker returns Tasks where Agent B is the recipient (filtered by contextId + authorization)
6. Agent B reads the message content from Task artifacts
7. Agent B calls `SendMessage` with `message.taskId = existingTaskId` (multi-turn ACK)
8. Broker moves Task to `TASK_STATE_COMPLETED`

**Unicast — Retract:**

- Agent A calls `CancelTask(taskId)` → Broker moves Task to `TASK_STATE_CANCELED` (only if still `INPUT_REQUIRED`)

**Broadcast — Send:**

1. Agent A calls `SendMessage` with `metadata.destination = "*"`
2. Broker creates N delivery Tasks (one per active agent, excluding sender), each with `contextId = recipient_agent_id`
3. Broker returns a summary Task to Agent A (state=`COMPLETED`) with Artifact listing `recipientCount` and `deliveryTaskIds`

**Broadcast — Receive & ACK:**

4-8. Same as unicast — each recipient independently discovers and ACKs their own delivery Task.

### Data Model

All core data structures — `Task`, `Message`, `Part`, `Artifact`, `AgentCard`, `TaskStatus`, `TaskState` など — は [A2A仕様](https://a2a-protocol.org/latest/specification/) で定義された型を `a2a-sdk` の Pydantic モデル経由でそのまま使用する。Broker 独自のデータモデルは作成しない。Redis には `a2a-sdk` の Task オブジェクトを JSON シリアライズして格納し、読み出し時にそのまま Pydantic モデルへデシリアライズする。Broker 固有の情報（ルーティングメタデータ等）は Task の `metadata` フィールドに格納する。

#### Redis Key Schema

| Key Pattern | Type | Description | TTL |
|---|---|---|---|
| `agent:{agent_id}` | Hash | Agent metadata + serialized Agent Card | None |
| `apikey:{sha256(key)}` | String | Maps hashed API key → agent_id | None |
| `agents:active` | Set | Set of active agent_id UUIDs | None |
| `task:{task_id}` | Hash | Full A2A Task JSON + routing metadata | None |
| `tasks:ctx:{context_id}` | Sorted Set | task_ids scored by status timestamp (updated on state change) | None |
| `tasks:sender:{agent_id}` | Set | task_ids created by this sender | None |

#### Agent Record (`agent:{agent_id}`)

```json
{
  "agent_id": "uuid-v4",
  "api_key_hash": "sha256-hex",
  "name": "string",
  "description": "string",
  "agent_card_json": "serialized A2A AgentCard JSON",
  "status": "active | deregistered",
  "registered_at": "ISO 8601",
  "deregistered_at": "ISO 8601 | null"
}
```

#### Task Record (`task:{task_id}`)

```json
{
  "task_json": "serialized A2A Task object (id, contextId, status, artifacts, history, metadata)",
  "from_agent_id": "uuid-v4",
  "to_agent_id": "uuid-v4",
  "type": "unicast | broadcast | broadcast_summary",
  "created_at": "ISO 8601"
}
```

The `task_json` field contains the canonical A2A Task object. The additional fields (`from_agent_id`, `to_agent_id`, `type`) are Broker-internal routing metadata used for authorization checks.

For broadcast, a separate Task is created per recipient to ensure independent ACK and per-pair FIFO ordering.

#### Task Visibility Rules

| Caller | Can Access |
|---|---|
| Recipient (API key maps to `to_agent_id`) | `ListTasks` by contextId (= their agent_id), `GetTask`, `SendMessage` (ACK) |
| Sender (API key maps to `from_agent_id`) | `GetTask` by known taskId, `CancelTask` |

`ListTasks` returns only tasks where the authenticated agent is the recipient (contextId match). If the provided `contextId` does not match the authenticated agent's ID, the Broker returns an empty task list (no error — consistent with A2A's rule that servers MUST NOT reveal existence of unauthorized resources). Senders track their tasks by the taskId returned from SendMessage.

### API Surfaces

#### 1. A2A Server API

The Broker exposes standard A2A endpoints using the `a2a-sdk` Python library.

**ASGI Mount Strategy**: FastAPI is the parent ASGI application. The A2A SDK's `A2AStarletteApplication` is mounted at the root path. FastAPI routes (`/api/v1/*`) take priority; A2A protocol paths fall through to the mounted Starlette app.

```python
from fastapi import FastAPI
from a2a.server.apps.starlette import A2AStarletteApplication

fastapi_app = FastAPI()
fastapi_app.include_router(registry_router, prefix="/api/v1")

a2a_app = A2AStarletteApplication(agent_card=broker_card, http_handler=handler)
fastapi_app.mount("/", a2a_app.build())
```

**Broker Agent Card** (served at `GET /.well-known/agent-card.json`):

```json
{
  "name": "Hikyaku",
  "description": "Message broker for coding agents. Send messages via SendMessage, poll inbox via ListTasks, acknowledge via multi-turn SendMessage.",
  "version": "1.0.0",
  "supportedInterfaces": [
    {
      "url": "http://localhost:8000",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0"
    }
  ],
  "capabilities": {
    "streaming": false,
    "pushNotifications": false
  },
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["application/json"],
  "skills": [
    {
      "id": "relay",
      "name": "Message Relay",
      "description": "Relay a message to a specific agent. Set metadata.destination to the target agent_id. The message becomes a Task in INPUT_REQUIRED state in the recipient's inbox.",
      "tags": ["messaging", "unicast", "relay"]
    },
    {
      "id": "broadcast",
      "name": "Broadcast Message",
      "description": "Broadcast a message to all registered agents. Set metadata.destination to \"*\". Creates a delivery Task per recipient.",
      "tags": ["messaging", "broadcast"]
    }
  ],
  "securitySchemes": {
    "apiKey": {
      "httpAuthSecurityScheme": {
        "scheme": "bearer",
        "description": "API key issued on agent registration"
      }
    }
  },
  "securityRequirements": [{"schemes": {"apiKey": {"list": []}}}]
}
```

##### A2A SendMessage — Send a Message

The sender specifies routing in `Message.metadata`:

| metadata field | Value | Behavior |
|---|---|---|
| `destination` | `"<agent-uuid>"` | Unicast: create delivery Task for target agent |
| `destination` | `"*"` | Broadcast: create delivery Tasks for all active agents (except sender) |

**Example — Send unicast message** (JSON-RPC):

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

**Response — Unicast** (delivery Task returned):

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

**Response — Broadcast** (summary Task returned):

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

##### A2A SendMessage — Acknowledge (Multi-Turn)

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

##### A2A ListTasks — Poll Inbox

Recipients discover unread messages by calling `ListTasks` with their agent_id as contextId:

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

The `statusTimestampAfter` parameter can be used for efficient delta polling — only fetch tasks updated since the last poll.

##### A2A GetTask — Read Specific Message

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

**Visibility**: Sender can access tasks they created (known taskId). Recipient can access tasks in their contextId. Others get `TaskNotFoundError`.

##### A2A CancelTask — Retract Message

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

##### A2A Error Cases

| Condition | JSON-RPC Error | Code |
|---|---|---|
| Missing `Authorization` header | HTTP 401 before JSON-RPC processing | N/A |
| Invalid API key | HTTP 401 before JSON-RPC processing | N/A |
| Missing `metadata.destination` | `InvalidParams`: "metadata.destination is required" | `-32602` |
| `destination` is not a valid UUID or `"*"` | `InvalidParams`: "invalid destination format" | `-32602` |
| Destination agent not found | `InvalidParams`: "destination agent not found" | `-32602` |
| Destination agent is deregistered | `InvalidParams`: "destination agent is deregistered" | `-32602` |
| ACK on task not owned by caller | `TaskNotFoundError` | `-32001` |
| ACK on task in terminal state (completed/canceled) | `UnsupportedOperationError`: "task is in terminal state" | `-32004` |
| GetTask/CancelTask on unknown or unauthorized task | `TaskNotFoundError` | `-32001` |

#### 2. Registry API

Base path: `/api/v1`

##### POST /api/v1/agents — Register Agent

No authentication required (open registration).

**Request**:

```json
{
  "name": "My Coding Agent",
  "description": "A Claude Code agent specializing in Python",
  "skills": [
    {
      "id": "python-dev",
      "name": "Python Development",
      "description": "Writes and reviews Python code",
      "tags": ["python", "backend"]
    }
  ]
}
```

**Response** (201 Created):

```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "api_key": "hky_a1b2c3d4e5f6...",
  "name": "My Coding Agent",
  "registered_at": "2026-03-28T12:00:00Z"
}
```

The `api_key` is shown only once at registration. The Broker stores only the SHA-256 hash.

The Broker constructs a full A2A `AgentCard` from the registration data, setting `supportedInterfaces` to point back to the Broker itself (since the agent is reachable only via the Broker).

##### GET /api/v1/agents — List Agents

Requires authentication (API key).

**Response** (200 OK):

```json
{
  "agents": [
    {
      "agent_id": "550e8400-...",
      "name": "My Coding Agent",
      "description": "A Claude Code agent specializing in Python",
      "skills": [
        {"id": "python-dev", "name": "Python Development", "tags": ["python", "backend"]}
      ],
      "registered_at": "2026-03-28T12:00:00Z"
    }
  ]
}
```

##### GET /api/v1/agents/{agent_id} — Get Agent Detail

Requires authentication.

**Response** (200 OK): Full A2A `AgentCard` JSON as stored.

**Error**: 404 if agent_id not found or deregistered.

##### DELETE /api/v1/agents/{agent_id} — Deregister Agent

Requires authentication. Only the agent itself can deregister (API key must match).

**Behavior**:
1. Set agent status to `deregistered`, record `deregistered_at` timestamp
2. Remove agent_id from `agents:active` set
3. Invalidate API key (delete `apikey:{hash}` entry)
4. Retain Tasks for 7 days (configurable via `DEREGISTERED_TASK_TTL_DAYS`)
5. A background cleanup task removes expired data (see Deregistered Agent Cleanup below)

**Response**: 204 No Content.

**Error**: 403 if API key does not match agent_id. 404 if agent not found.

### Authentication

All endpoints except `POST /api/v1/agents` (registration) and `GET /.well-known/agent-card.json` (Agent Card) require authentication.

**Mechanism**: `Authorization: Bearer <api_key>` HTTP header.

**Flow**:
1. Agent registers via `POST /api/v1/agents` → receives `api_key`
2. Broker stores `SHA-256(api_key)` in Redis: `apikey:{sha256_hex}` → `agent_id`
3. On each request (both REST and A2A), Broker hashes the provided key and resolves agent_id
4. If not found or agent is deregistered → HTTP 401

**API key format**: `hky_` prefix + 32 random hex characters (e.g., `hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4`).

### Error Handling

REST API errors (Registry) use a consistent JSON format:

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
| `UNAUTHORIZED` | 401 | Missing or invalid API key |
| `FORBIDDEN` | 403 | API key does not match the target resource |
| `AGENT_NOT_FOUND` | 404 | Agent does not exist or is deregistered |
| `INVALID_REQUEST` | 400 | Missing required fields or invalid values |

A2A operation errors follow standard JSON-RPC error format and A2A error code mappings (see A2A Error Cases table above).

### Deregistered Agent Cleanup

A periodic background task cleans up Task data for agents that have been deregistered longer than `DEREGISTERED_TASK_TTL_DAYS`.

**Trigger**: `asyncio` periodic task launched via FastAPI's `lifespan` event. Runs every hour.

**Behavior**:
1. Scan `agent:*` hashes for records where `status == "deregistered"` and `deregistered_at` is older than the retention period
2. For each expired agent:
   - Delete all `task:{task_id}` hashes referenced in `tasks:ctx:{agent_id}`
   - Delete `tasks:ctx:{agent_id}` sorted set
   - Remove task_ids from relevant `tasks:sender:*` sets
   - Delete `agent:{agent_id}` hash
3. Log the number of cleaned-up agents per run

The scan uses Redis `SCAN` (not `KEYS`) to avoid blocking.

| Configuration | Default | Description |
|---|---|---|
| `DEREGISTERED_TASK_TTL_DAYS` | `7` | Retention period before cleanup |
| `CLEANUP_INTERVAL_SECONDS` | `3600` | How often the cleanup task runs |

### Project Structure

A uv workspace monorepo with two independent packages. Agents only need `pip install hikyaku-client`. The Broker server is deployed separately.

**Why uv workspace?**
- **Dependency separation**: Broker needs FastAPI + Redis. CLI needs click + httpx. Agents should not pull in server dependencies.
- **Reproducible builds**: `uv.lock` pins all transitive dependencies across both packages in a single lockfile.
- **Unified toolchain**: `uv sync` installs all workspace members. `uv run --package hikyaku-registry uvicorn ...` / `uv run --package hikyaku-client hikyaku ...` for per-package execution.
- **Independent versioning**: Each package can be released on its own schedule.
- **Deploy separation**: Broker runs on a server. CLI is installed in the agent's local environment.

```
（repository root）
├── pyproject.toml                     # uv workspace root
├── uv.lock                            # shared lock file (reproducible builds)
├── CLAUDE.md                          # Claude Code project instructions
├── ARCHITECTURE.md                    # Project overview, architecture diagram, design decisions
├── docs/
│   └── spec/
│       ├── registry-api.md            # Registry REST API specification
│       ├── a2a-operations.md          # A2A operation usage and message lifecycle
│       └── data-model.md             # Redis data model and key schema
│
├── registry/                            # Package 1: hikyaku-registry
│   ├── pyproject.toml                 # pip install hikyaku-registry
│   ├── src/
│   │   └── hikyaku_registry/
│   │       ├── __init__.py
│   │       ├── main.py               # ASGI app: mount A2A + FastAPI
│   │       ├── config.py             # Settings via pydantic-settings
│   │       ├── auth.py               # API key auth (shared by REST + A2A)
│   │       ├── redis_client.py       # Redis connection pool
│   │       ├── models.py             # Pydantic models (Registry API)
│   │       ├── executor.py           # BrokerExecutor (A2A AgentExecutor)
│   │       ├── task_store.py         # RedisTaskStore (A2A TaskStore for Redis)
│   │       ├── agent_card.py         # Broker's own Agent Card definition
│   │       ├── registry_store.py     # Agent CRUD on Redis
│   │       └── api/
│   │           ├── __init__.py
│   │           └── registry.py       # Registry API router
│   └── tests/
│       ├── conftest.py               # Fixtures: Redis test instance, test client
│       ├── test_registry.py
│       └── test_a2a.py               # A2A operations: send, list, get, ack, cancel
│
└── client/                               # Package 2: hikyaku-client
    ├── pyproject.toml                 # pip install hikyaku-client → hikyaku command
    ├── src/
    │   └── hikyaku_client/
    │       ├── __init__.py
    │       ├── cli.py                 # click group + subcommands
    │       ├── api.py                 # Helper functions: send_message(), poll_tasks(), etc. (httpx / a2a-sdk)
    │       └── output.py             # Human-readable + JSON output formatting
    └── tests/
        └── test_cli.py               # CLI tests (click.testing.CliRunner)
```

**Root `pyproject.toml`** (uv workspace definition):

```toml
[project]
name = "hikyaku"
version = "0.1.0"
description = "A2A-native message broker and agent registry for coding agents"
requires-python = ">=3.12"

[tool.uv.workspace]
members = ["registry", "client"]
```

**Per-package execution**:

```bash
# Install all workspace members
uv sync

# Run the Broker server
uv run --package hikyaku-registry uvicorn hikyaku_registry.main:app

# Run CLI commands
uv run --package hikyaku-client hikyaku register --name "my-agent"
```

### Configuration

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `BROKER_HOST` | `0.0.0.0` | Server bind host |
| `BROKER_PORT` | `8000` | Server bind port |
| `BROKER_BASE_URL` | `http://localhost:8000` | Public base URL (for Agent Card) |
| `DEREGISTERED_TASK_TTL_DAYS` | `7` | Days to retain deregistered agent's tasks |
| `CLEANUP_INTERVAL_SECONDS` | `3600` | Interval between cleanup task runs |

### CLI Tool (`hikyaku`)

A command-line tool for agents to interact with the Broker. Wraps `a2a-sdk` and `httpx` internally so agents never construct JSON-RPC payloads manually.

**Package**: `pip install hikyaku-client` → provides the `hikyaku` command.

#### Commands

```bash
# Register a new agent (no auth required, prints agent_id + api_key)
hikyaku register --name "my-agent" --description "Frontend agent" \
  --skills '[{"id":"frontend","name":"Frontend Dev","description":"React/TS","tags":["react","typescript"]}]'

# Send unicast message
hikyaku send --to "agent-uuid" --text "Did the API schema change?"

# Send broadcast message
hikyaku broadcast --text "Build failed on main branch"

# Poll inbox (unread messages)
hikyaku poll
hikyaku poll --since "2026-03-28T12:00:00Z"   # delta poll
hikyaku poll --page-size 50                     # pagination

# Acknowledge a message
hikyaku ack --task-id "task-delivery-uuid"

# Retract a sent message
hikyaku cancel --task-id "task-delivery-uuid"

# Get a specific task
hikyaku get-task --task-id "task-delivery-uuid"

# List registered agents
hikyaku agents

# Get agent detail
hikyaku agents --id "agent-uuid"

# Deregister self
hikyaku deregister
```

#### Command Reference

| Command | Description | Auth | Key Options |
|---|---|---|---|
| `register` | Register agent, print credentials | No | `--name`, `--description`, `--skills` (JSON) |
| `send` | Send unicast message | Yes | `--to` (agent UUID), `--text` |
| `broadcast` | Broadcast to all agents | Yes | `--text` |
| `poll` | List unread inbox messages | Yes | `--since` (ISO 8601), `--page-size`, `--status` |
| `ack` | Acknowledge a message | Yes | `--task-id` |
| `cancel` | Retract a sent message | Yes | `--task-id` |
| `get-task` | Get a specific task | Yes | `--task-id` |
| `agents` | List all agents or get detail | Yes | `--id` (optional, for single agent) |
| `deregister` | Remove own registration | Yes | — |

#### Configuration

Connection settings via environment variables or CLI options (CLI options take priority):

| Environment Variable | CLI Option | Default | Description |
|---|---|---|---|
| `HIKYAKU_URL` | `--url` | `http://localhost:8000` | Broker base URL |
| `HIKYAKU_API_KEY` | `--api-key` | — | API key from registration |
| `HIKYAKU_AGENT_ID` | `--agent-id` | — | Agent UUID from registration |

The `register` command prints `export` statements for easy shell integration:

```bash
$ hikyaku register --name "my-agent" --description "Frontend agent"
Agent registered successfully.
  agent_id: 550e8400-e29b-41d4-a716-446655440000
  api_key:  hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4

# Set these in your environment:
export HIKYAKU_URL=http://localhost:8000
export HIKYAKU_API_KEY=hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4
export HIKYAKU_AGENT_ID=550e8400-e29b-41d4-a716-446655440000
```

#### Output Format

- **Default**: Human-readable text (tables for lists, key-value for single items)
- **`--json` flag**: JSON output on all commands (for programmatic consumption / piping)

```bash
# Human-readable (default)
$ hikyaku poll
┌──────────────────────────┬──────────────┬─────────────────────┬────────────────────────────┐
│ Task ID                  │ From         │ Time                │ Message (truncated)        │
├──────────────────────────┼──────────────┼─────────────────────┼────────────────────────────┤
│ task-abc123...           │ Agent A      │ 2026-03-28 12:00:00 │ Did the API schema change? │
└──────────────────────────┴──────────────┴─────────────────────┴────────────────────────────┘

# JSON output
$ hikyaku poll --json
[{"id":"task-abc123...","contextId":"...","status":{"state":"TASK_STATE_INPUT_REQUIRED",...},...}]
```

#### Internal Design

No intermediate abstraction layer. No `BrokerClient` class. The CLI has three modules:

- **`cli.py`** — click group + subcommands. Each subcommand calls helper functions from `api.py` and formats output via `output.py`.
- **`api.py`** — Stateless helper functions that call `httpx` (Registry REST) or `a2a-sdk` `A2AClient` (JSON-RPC) directly. Each function takes `broker_url` and `api_key` as parameters.
- **`output.py`** — Formatting: human-readable tables (default) and JSON (`--json` flag).

```
cli.py (click group)
│
├── register    → api.register_agent()    → POST /api/v1/agents          (httpx)
├── agents      → api.list_agents()       → GET /api/v1/agents[/{id}]    (httpx)
├── deregister  → api.deregister_agent()  → DELETE /api/v1/agents/{id}   (httpx)
│
├── send        → api.send_message()      → A2A SendMessage              (a2a-sdk)
├── broadcast   → api.broadcast_message() → A2A SendMessage dest="*"     (a2a-sdk)
├── poll        → api.poll_tasks()        → A2A ListTasks                (a2a-sdk)
├── ack         → api.ack_task()          → A2A SendMessage (taskId)     (a2a-sdk)
├── cancel      → api.cancel_task()       → A2A CancelTask              (a2a-sdk)
└── get-task    → api.get_task()          → A2A GetTask                  (a2a-sdk)
```

- All HTTP requests include `Authorization: Bearer <api_key>` (except `register`).
- `broker_url` and `api_key` are resolved from click context (CLI options or environment variables).

---

## Implementation

> Task format: `- [x] Done task <!-- completed: 2026-02-13T14:30 -->`
> When completing a task, check the box and record the timestamp in the same edit.

### Step 0: Documentation Structure

Split this design document into focused reference files for developers. The design-doc.md is retained as the design decision record; the new files serve as implementation references.

- [x] Create `ARCHITECTURE.md` — project overview, architecture diagram, component layout, summary of key design decisions (contextId convention, Task lifecycle mapping, ASGI mount strategy) <!-- completed: 2026-03-28T09:15 -->
- [x] Create `docs/spec/registry-api.md` — Registry REST API specification: endpoints, request/response JSON examples, error codes, authentication requirements <!-- completed: 2026-03-28T09:15 -->
- [x] Create `docs/spec/a2a-operations.md` — A2A operation usage: SendMessage (send + ACK), ListTasks (inbox polling), GetTask, CancelTask (retract), message lifecycle flows (unicast, broadcast), error cases table <!-- completed: 2026-03-28T09:15 -->
- [x] Create `docs/spec/data-model.md` — Redis data model: key schema table, agent record structure, task record structure, sorted set indexing, task visibility rules, cleanup behavior <!-- completed: 2026-03-28T09:15 -->

### Step 1: Workspace & Broker Package Setup

- [x] Create root `pyproject.toml` with uv workspace definition (`[tool.uv.workspace] members = ["registry", "client"]`), `requires-python = ">=3.12"` <!-- completed: 2026-03-28T09:20 -->
- [x] Create `registry/pyproject.toml` with dependencies: `fastapi`, `uvicorn[standard]`, `a2a-sdk`, `redis[hiredis]`, `pydantic`, `pydantic-settings`. Package name: `hikyaku-registry` <!-- completed: 2026-03-28T09:20 -->
- [x] Create directory structure: `registry/src/hikyaku_registry/`, `registry/src/hikyaku_registry/api/`, `registry/tests/`, `docs/spec/` <!-- completed: 2026-03-28T09:20 -->
- [x] Run `uv sync` to generate `uv.lock` and verify workspace resolution <!-- completed: 2026-03-28T09:20 -->
- [x] Implement `config.py` with `pydantic-settings` `BaseSettings` class loading from environment variables <!-- completed: 2026-03-28T09:20 -->
- [x] Implement `redis_client.py` with async Redis connection pool using `redis.asyncio` <!-- completed: 2026-03-28T09:20 -->

### Step 2: Redis Agent Store

- [x] Implement `registry_store.py`: `create_agent`, `get_agent`, `list_active_agents`, `deregister_agent`, `lookup_by_api_key` <!-- completed: 2026-03-28T09:30 -->
- [x] Implement API key hashing and index management (`apikey:{hash}` → `agent_id`) <!-- completed: 2026-03-28T09:30 -->
- [x] Implement `agents:active` set management (add on register, remove on deregister) <!-- completed: 2026-03-28T09:30 -->

### Step 3: Redis Task Store

- [x] Implement `task_store.py` as `RedisTaskStore` conforming to A2A SDK's `TaskStore` interface: `save`, `get`, `update`, `list` <!-- completed: 2026-03-28T09:55 -->
- [x] Implement `tasks:ctx:{context_id}` sorted set indexing (scored by status timestamp, re-scored on state change) for `ListTasks` queries. A2A spec requires descending status timestamp order. <!-- completed: 2026-03-28T09:55 -->
- [x] Implement `tasks:sender:{agent_id}` set indexing for sender visibility on `GetTask`/`CancelTask` <!-- completed: 2026-03-28T09:55 -->

### Step 4: Authentication

- [x] Implement `auth.py` with FastAPI dependency that extracts and validates API key from `Authorization: Bearer` header <!-- completed: 2026-03-28T10:00 -->
- [x] Integrate auth into A2A request pipeline (extract agent_id from API key for `BrokerExecutor` context) <!-- completed: 2026-03-28T10:00 -->

### Step 5: Registry API

- [x] Implement `POST /api/v1/agents` — generate UUID + API key, build AgentCard, store in Redis <!-- completed: 2026-03-28T10:10 -->
- [x] Implement `GET /api/v1/agents` — list all active agents with summary info <!-- completed: 2026-03-28T10:10 -->
- [x] Implement `GET /api/v1/agents/{agent_id}` — return full Agent Card <!-- completed: 2026-03-28T10:10 -->
- [x] Implement `DELETE /api/v1/agents/{agent_id}` — soft-delete with ownership check <!-- completed: 2026-03-28T10:10 -->

### Step 6: A2A Server — BrokerExecutor

- [x] Define Broker's Agent Card in `agent_card.py` using `a2a-sdk` types <!-- completed: 2026-03-28 -->
- [x] Implement `executor.py` `BrokerExecutor.execute()` for new messages: validate destination, create Task(s) with `INPUT_REQUIRED` state, store message content as Artifact <!-- completed: 2026-03-28 -->
- [x] Implement broadcast fan-out in executor: iterate `agents:active`, create one delivery Task per recipient, return summary Task <!-- completed: 2026-03-28 -->
- [x] Implement multi-turn ACK handling: when `SendMessage` references existing `taskId`, validate recipient ownership and move Task to `COMPLETED` <!-- completed: 2026-03-28 -->
- [x] Mount `A2AStarletteApplication` with `DefaultRequestHandler(executor, task_store)` in `main.py` <!-- completed: 2026-03-28 -->

### Step 7: Deregistered Agent Cleanup

- [x] Implement periodic cleanup task using `asyncio.create_task` in FastAPI `lifespan` event <!-- completed: 2026-03-28 -->
- [x] Implement cleanup logic: SCAN for expired deregistered agents, delete tasks + indexes + agent record <!-- completed: 2026-03-28 -->

### Step 8: Broker Testing

- [x] Set up `registry/tests/conftest.py`: Redis test instance, HTTPX async client, helper for agent registration <!-- completed: 2026-03-28 -->
- [x] Test Registry flow: register → list → get → deregister <!-- completed: 2026-03-28 -->
- [x] Test unicast flow: register 2 agents → SendMessage → ListTasks → GetTask → SendMessage ACK → verify COMPLETED <!-- completed: 2026-03-28 -->
- [x] Test broadcast flow: register 3 agents → broadcast → each ListTasks → ACK → verify all COMPLETED; test CancelTask retraction <!-- completed: 2026-03-28 -->

### Step 9: CLI Tool

- [x] Create `client/pyproject.toml` with dependencies: `click`, `httpx`, `a2a-sdk`. Package name: `hikyaku-client`. Entry point: `hikyaku = hikyaku_client.cli:cli` <!-- completed: 2026-03-28 -->
- [x] Implement `api.py` — stateless helper functions (`register_agent`, `send_message`, `broadcast_message`, `poll_tasks`, `ack_task`, `cancel_task`, `get_task`, `list_agents`, `deregister_agent`) calling httpx (REST) or a2a-sdk (JSON-RPC) directly <!-- completed: 2026-03-28 -->
- [x] Implement `output.py` — output formatting: human-readable tables (default) and JSON (`--json` flag) <!-- completed: 2026-03-28 -->
- [x] Implement `cli.py` — click group with subcommands: `register`, `send`, `broadcast`, `poll`, `ack`, `cancel`, `get-task`, `agents`, `deregister`. Global options: `--url`, `--api-key`, `--agent-id`, `--json`. Each subcommand calls `api.*` and formats via `output.*` <!-- completed: 2026-03-28 -->
- [x] Test CLI in `client/tests/test_cli.py` using `click.testing.CliRunner` — register + export output, send/poll/ack round-trip, broadcast, cancel, `--json` flag, error cases, env var fallback <!-- completed: 2026-03-28 -->

### Step 10: Agent Skill

Create a Claude Code skill file so coding agents can use the `hikyaku` CLI via natural language. The skill provides instructions and command patterns for agent registration, messaging, polling, and acknowledgment.

- [x] Create `.claude/skills/hikyaku` skill file — skill definition with: description (interact with Hikyaku message broker), trigger conditions (when agent needs to send/receive messages, register, discover peers), command reference (register, send, broadcast, poll, ack, cancel, agents), environment variable setup (`HIKYAKU_URL`, `HIKYAKU_API_KEY`, `HIKYAKU_AGENT_ID`), typical workflow examples (register → poll → ack loop) <!-- completed: 2026-03-28 -->
- [x] Test skill integration — verify Claude Code loads the skill, correctly generates `hikyaku` CLI commands for common scenarios (register, send+poll round-trip, broadcast), and handles error cases <!-- completed: 2026-03-28 -->

---

## Changelog

| Date | Changes |
|------|---------|
| 2026-03-28 | Initial draft |
| 2026-03-28 | Major revision: A2A-native design. Replace custom Mailbox API with standard A2A operations (ListTasks, GetTask, CancelTask, multi-turn SendMessage for ACK). Model messages as Tasks with INPUT_REQUIRED→COMPLETED lifecycle. Use contextId=recipient_agent_id for inbox discovery. |
| 2026-03-28 | A2A v1.0 spec compliance verification: Fix JSON-RPC method names to PascalCase (SendMessage, ListTasks, GetTask, CancelTask) per Section 9.1. Fix Redis sorted set scoring from creation timestamp to status timestamp per ListTasks ordering requirement (Section 3.1.4). Add a2a-sdk data model clarification. |
| 2026-03-28 | Add Step 0: Documentation Structure. Split design doc into ARCHITECTURE.md, docs/spec/registry-api.md, docs/spec/a2a-operations.md, docs/spec/data-model.md. Renumber existing Steps 1-8 to 1-8 (unchanged, Step 0 inserted before). Total tasks: 27→31. |
| 2026-03-28 | Add Responsibility Assignment table (Broker=server, agents=A2A clients). Add CLI tool (`hikyaku`) specification: command reference, config (env vars + CLI options), output format (human-readable + JSON), internal design (click + httpx + a2a-sdk). Add Step 9: CLI Tool implementation tasks. Update project structure with `client/` directory. Total tasks: 31→36. |
| 2026-03-28 | Simplify CLI design: remove BrokerClient abstraction layer. CLI subcommands call httpx (Registry REST) and a2a-sdk A2AClient (JSON-RPC) directly. Rename package to `hikyaku-client`, directory `client/` → `client/` (originally `a2a-broker-cli` → `hikyaku-client`). Total tasks: 36→35. |
| 2026-03-28 | Monorepo structure: split into 2 independent packages — `registry/` (`hikyaku-registry`: FastAPI+Redis) and `client/` (`hikyaku-client`: click+httpx). Each has own pyproject.toml, src/, tests/. Agents only install CLI package. Rename `agent_store.py` → `registry_store.py`, flatten `store/` subdirectory. |
| 2026-03-28 | Resolve reviewer issues: replace BrokerClient with stateless helper functions in `api.py` (send_message, poll_tasks, etc.). CLI internal design: cli.py → api.py → httpx/a2a-sdk. Confirm package name `hikyaku-client` consistent throughout. Total tasks: 35→36. |
| 2026-03-28 | Rename project to **Hikyaku**. Packages: `hikyaku-registry` (server) + `hikyaku-client` (CLI). Directories: `registry/` + `client/`. CLI command: `hikyaku`. Env vars: `HIKYAKU_URL`, `HIKYAKU_API_KEY`, `HIKYAKU_AGENT_ID`. API key prefix: `hky_`. Add Step 10: Agent Skill — `.claude/skills/hikyaku` skill file for Claude Code integration. Total tasks: 36→38. |
| 2026-03-28 | Configure as **uv workspace** monorepo. Add root `pyproject.toml` with `[tool.uv.workspace] members = ["registry", "client"]`. Add `uv.lock`, `CLAUDE.md` to project structure. Add uv workspace rationale (reproducible builds, unified toolchain, per-package execution via `uv run --package`). Step 1 renamed to "Workspace & Broker Package Setup", +2 tasks (root pyproject.toml, `uv sync`). Total tasks: 38→40. |
| 2026-03-28 | Rename project from **Postbox** to **Hikyaku** (飛脚). All package names, module names, CLI command, environment variables, and API key prefix updated: `hikyaku-registry`, `hikyaku-client`, `hikyaku` CLI, `HIKYAKU_*` env vars, `hky_` prefix. |
| 2026-03-28 | Implementation complete. All 40/40 tasks done. 222 tests passing (171 registry + 51 client). Status → Complete. |
