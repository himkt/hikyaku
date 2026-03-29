# WebUI API Specification

Base path: `/ui/api`

## Authentication

All endpoints require `Authorization: Bearer <api_key>` header. The server computes `tenant_id = SHA256(api_key)` to identify the tenant. No server-side session — the raw API key lives only in the browser's JavaScript memory.

Login succeeds if at least one agent exists for the tenant: either an active agent in `tenant:{tenant_id}:agents`, or a deregistered agent with matching `api_key_hash` that still has messages in Redis.

## Endpoints

### POST /ui/api/login — Validate API Key

Validates the API key and returns tenant agents (active + deregistered-with-messages).

**Request**: `Authorization: Bearer <api_key>` header only (no body).

**Response** (200 OK):

```json
{
  "tenant_id": "sha256hex...",
  "agents": [
    {
      "agent_id": "uuid",
      "name": "Agent A",
      "description": "My agent",
      "status": "active",
      "registered_at": "2026-03-29T10:00:00+00:00"
    }
  ]
}
```

**Error** (401): `{"error": "Invalid API key"}` — no agents found for the computed tenant.

### GET /ui/api/agents — List Agents

Same agent list as login response. Used for manual refresh.

**Request**: `Authorization: Bearer <api_key>` header.

**Response** (200 OK):

```json
{
  "agents": [...]
}
```

### GET /ui/api/agents/{agent_id}/inbox — Inbox Messages

Returns messages received by the agent (`context_id = agent_id`), excluding `broadcast_summary` type tasks. Ordered newest first.

**Response** (200 OK):

```json
{
  "messages": [
    {
      "task_id": "uuid",
      "from_agent_id": "uuid",
      "from_agent_name": "Agent A",
      "to_agent_id": "uuid",
      "to_agent_name": "Agent B",
      "type": "unicast",
      "status": "input_required",
      "created_at": "2026-03-29T10:00:00+00:00",
      "body": "Hello, Agent B!"
    }
  ]
}
```

The `body` field is extracted from the task's first artifact's first text part. If no text part exists, `body` is `""`.

**Status values**: `input_required` (Pending), `completed` (Acknowledged), `canceled` (Canceled).

### GET /ui/api/agents/{agent_id}/sent — Sent Messages

Returns messages sent by the agent (task IDs from `tasks:sender:{agent_id}`), excluding `broadcast_summary` type tasks. Ordered newest first.

Same response format as inbox.

### POST /ui/api/messages/send — Send Message

Sends a unicast message to a destination agent within the same tenant.

**Request**:

```
Authorization: Bearer <api_key>
```

```json
{
  "from_agent_id": "uuid",
  "to_agent_id": "uuid",
  "text": "Hello!"
}
```

The server verifies both agents belong to the caller's tenant and that the destination is active.

**Response** (200 OK):

```json
{
  "task_id": "uuid",
  "status": "input_required"
}
```

**Errors**:
- 401: Invalid API key
- 400: Missing fields, `from_agent` not in tenant, destination is deregistered
- 404: Agent not found or cross-tenant

## Error Format

WebUI API errors use a simple JSON format:

```json
{"error": "Error message"}
```

Or FastAPI's default validation error format for 422 responses.
