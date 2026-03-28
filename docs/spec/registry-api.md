# Registry REST API Specification

Base path: `/api/v1`

## Authentication

All endpoints except `POST /api/v1/agents` (registration) and `GET /.well-known/agent-card.json` (Agent Card) require authentication.

- **Mechanism**: Two headers are required on all authenticated requests:

| Header | Purpose |
|---|---|
| `Authorization: Bearer <api_key>` | Authenticates the tenant (`SHA-256(api_key)` = `tenant_id`) |
| `X-Agent-Id: <agent_id>` | Identifies the specific agent within the tenant |

- **Flow**: Agent registers → receives `api_key` and `agent_id` → Broker stores `SHA-256(api_key)` as `api_key_hash` → on each request, Broker hashes provided key, verifies the agent record's `api_key_hash` matches, and confirms agent-tenant membership
- **API key format**: `hky_` prefix + 32 random hex characters (e.g., `hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4`)
- **Tenant model**: The API key is a shared tenant credential. All agents registered with the same API key belong to the same tenant and can discover and communicate with each other. Agents in different tenants are invisible to one another.
- **Registration exception**: `POST /api/v1/agents` does not require the `X-Agent-Id` header (the agent doesn't exist yet). It optionally accepts `Authorization: Bearer <api_key>` to join an existing tenant.

## Endpoints

### POST /api/v1/agents — Register Agent

Two registration modes, determined by presence of `Authorization` header:

#### Create New Tenant (no `Authorization` header)

A new API key and tenant are created automatically.

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
  "api_key": "hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "name": "My Coding Agent",
  "registered_at": "2026-03-28T12:00:00Z"
}
```

The `api_key` is shown only once at registration. The Broker stores only the SHA-256 hash. The Broker constructs a full A2A `AgentCard` from the registration data, setting `supportedInterfaces` to point back to the Broker itself.

#### Join Existing Tenant (`Authorization: Bearer <api_key>` provided)

The agent joins the tenant identified by the provided API key. The server verifies the tenant exists (i.e., at least one active agent is already registered with that key). If the tenant set is empty or missing, returns 401 "Invalid API key".

**Request** (with `Authorization: Bearer <existing_api_key>` header):

```json
{
  "name": "My Second Agent",
  "description": "Another agent in the same tenant"
}
```

**Response** (201 Created):

```json
{
  "agent_id": "660e8400-e29b-41d4-a716-446655440001",
  "api_key": "hky_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "name": "My Second Agent",
  "registered_at": "2026-03-28T12:01:00Z"
}
```

The `api_key` in the response is the same key that was provided in the `Authorization` header (echoed back for convenience).

### GET /api/v1/agents — List Agents

Requires authentication (`Authorization` + `X-Agent-Id` headers).

Returns only agents belonging to the caller's tenant. Agents in other tenants are not visible.

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

### GET /api/v1/agents/{agent_id} — Get Agent Detail

Requires authentication (`Authorization` + `X-Agent-Id` headers).

**Response** (200 OK): Full A2A `AgentCard` JSON as stored.

**Error**: 404 if `agent_id` not found, deregistered, or belongs to a different tenant. Cross-tenant lookups always return 404 (indistinguishable from "not found") to prevent information leakage.

### DELETE /api/v1/agents/{agent_id} — Deregister Agent

Requires authentication. Only the agent itself can deregister (API key must match).

**Behavior**:

1. Set agent status to `deregistered`, record `deregistered_at` timestamp
2. Remove `agent_id` from `agents:active` set
3. Remove `agent_id` from `tenant:{api_key_hash}:agents` set
4. Retain Tasks for 7 days (configurable via `DEREGISTERED_TASK_TTL_DAYS`)
5. A background cleanup task removes expired data

**Response**: 204 No Content.

**Error**: 403 if API key does not match `agent_id`. 404 if agent not found.

## Error Format

REST API errors use a consistent JSON format:

```json
{
  "error": {
    "code": "AGENT_NOT_FOUND",
    "message": "Agent with id '...' not found"
  }
}
```

## Error Codes

| Error Code | HTTP Status | Description |
|---|---|---|
| `UNAUTHORIZED` | 401 | Missing or invalid API key, missing `X-Agent-Id` header, or agent-tenant membership mismatch |
| `FORBIDDEN` | 403 | API key does not match the target resource |
| `AGENT_NOT_FOUND` | 404 | Agent does not exist, is deregistered, or belongs to a different tenant |
| `INVALID_REQUEST` | 400 | Missing required fields or invalid values |
