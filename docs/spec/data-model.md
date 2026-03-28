# Redis Data Model Specification

All core data structures — `Task`, `Message`, `Part`, `Artifact`, `AgentCard`, `TaskStatus`, `TaskState` — use types defined by the A2A specification via `a2a-sdk` Pydantic models. No Broker-specific data models are created. Redis stores `a2a-sdk` Task objects as JSON, deserialized back to Pydantic models on read. Broker-specific information (routing metadata etc.) is stored in the Task's `metadata` field.

## Redis Key Schema

| Key Pattern | Type | Description | TTL |
|---|---|---|---|
| `agent:{agent_id}` | Hash | Agent metadata + serialized Agent Card. The `api_key_hash` field serves as `tenant_id`. | None |
| ~~`apikey:{sha256(key)}`~~ | ~~String~~ | ~~Maps hashed API key → agent_id~~ | **Removed** — replaced by tenant set membership check. Multiple agents now share one API key, so this 1:1 mapping is no longer valid. |
| `agents:active` | Set | Set of active agent_id UUIDs. **Retained for cleanup scanning only** — application queries use tenant sets instead. | None |
| `tenant:{api_key_hash}:agents` | Set | **New.** Set of active agent_ids belonging to this tenant. Updated on registration (SADD) and deregistration (SREM). This is the canonical source for tenant membership. | None |
| `task:{task_id}` | Hash | Full A2A Task JSON + routing metadata | None |
| `tasks:ctx:{context_id}` | Sorted Set | task_ids scored by status timestamp (updated on state change) | None |
| `tasks:sender:{agent_id}` | Set | task_ids created by this sender | None |

## Agent Record (`agent:{agent_id}`)

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

## Task Record (`task:{task_id}`)

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

## Sorted Set Indexing

The `tasks:ctx:{context_id}` sorted set indexes tasks by status timestamp (not creation time). The score is updated on every state change, ensuring `ListTasks` returns results in the A2A-required descending status timestamp order.

This enables efficient inbox queries:
- `ListTasks(contextId=agentB-uuid)` → `ZREVRANGEBYSCORE tasks:ctx:agentB-uuid`
- Status filtering is applied after retrieval from the sorted set
- Pagination uses `pageSize` with cursor-based offset

## Tenant Lifecycle

Tenants are ephemeral. When the last agent in a tenant deregisters, the `tenant:{api_key_hash}:agents` set becomes empty. At that point, no new agent can join using that API key (the join flow rejects empty/missing tenant sets). The API key effectively dies with the last agent. To re-create the tenant, an agent must register without auth to get a fresh API key.

## Task Visibility Rules

| Caller | Can Access |
|---|---|
| Recipient (same-tenant agent matching `to_agent_id`) | `ListTasks` by contextId (= their agent_id), `GetTask`, `SendMessage` (ACK) |
| Sender (same-tenant agent matching `from_agent_id`) | `GetTask` by known taskId, `CancelTask` |

`ListTasks` enforces that `contextId` must equal the caller's `agent_id`. If a different `contextId` is provided, the Broker returns an error. This prevents inbox snooping — even within the same tenant. `GetTask` verifies that the task's `from_agent_id` or `to_agent_id` belongs to the caller's tenant; cross-tenant lookups return "not found".

## Deregistered Agent Cleanup

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

### Configuration

| Configuration | Default | Description |
|---|---|---|
| `DEREGISTERED_TASK_TTL_DAYS` | `7` | Retention period before cleanup |
| `CLEANUP_INTERVAL_SECONDS` | `3600` | How often the cleanup task runs |
