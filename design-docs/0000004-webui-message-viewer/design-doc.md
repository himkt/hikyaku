# WebUI Message Viewer

**Status**: Complete
**Progress**: 23/23 tasks complete
**Last Updated**: 2026-03-29

## Overview

Add a browser-based message viewer to the Hikyaku broker that lets tenant operators log in with their shared API key, browse agents, and view/send messages — all served from the existing FastAPI app in `registry/`.

## Success Criteria

- [ ] Login form accepts API key (password field), authenticates against Redis tenant data
- [ ] After login, tabs display all agents in the tenant (active and deregistered-with-messages)
- [ ] Each agent tab shows received (inbox) and sent messages in chronological order
- [ ] Each message displays: sender/recipient, send date, ACK status (Pending/Acknowledged/Canceled), and message body text
- [ ] Users can send unicast messages to other agents within the tenant from the WebUI
- [ ] Manual refresh button reloads message data without page reload
- [ ] Ephemeral data warning is visible in the UI
- [ ] WebUI is served as static files from the FastAPI app under `/ui/`

---

## Background

The Hikyaku broker currently exposes a REST API for agent management and a JSON-RPC endpoint for A2A messaging. Operators who want to inspect message history must use the CLI or craft raw API calls. A lightweight WebUI removes this friction by providing a visual dashboard for browsing message state per tenant.

All data lives in Redis. There is no persistent database. The cleanup process deletes tasks after deregistration TTL expires, and a Redis restart without persistence config loses all data. The WebUI reads directly from Redis — no additional storage layer.

---

## Specification

### Tech Stack

| Component | Version | Purpose |
|-----------|---------|---------|
| Vite | 8.x | Build tooling (Rolldown-based bundler) |
| React | 19.x | UI framework |
| TypeScript | 5.x | Type safety |
| Tailwind CSS | 4.x | Styling |

The SPA is built into static files and served by FastAPI via `StaticFiles` mount. No separate web server required.

### Architecture

```
browser (SPA)                          FastAPI (registry/)
──────────────                         ──────────────────
/ui/                                   StaticFiles mount → webui/dist/
  └─ index.html + JS bundle
                                       /ui/api/login     POST  (tenant auth)
  fetch("/ui/api/...")  ───────────→   /ui/api/agents    GET   (list agents)
                                       /ui/api/agents/:id/inbox   GET
                                       /ui/api/agents/:id/sent    GET
                                       /ui/api/messages/send      POST
```

The SPA communicates with dedicated WebUI API endpoints under `/ui/api/`. These endpoints read directly from Redis using existing store classes. The API key is stored in browser memory (JavaScript variable) and sent as `Authorization: Bearer <key>` on each request. Refreshing the page requires re-login.

### URL Structure

| Path | Description |
|------|-------------|
| `/ui/` | SPA entry point (serves `index.html`) |
| `/ui/api/login` | Validate API key, return tenant agent list |
| `/ui/api/agents` | List agents in tenant |
| `/ui/api/agents/{agent_id}/inbox` | Messages received by agent |
| `/ui/api/agents/{agent_id}/sent` | Messages sent by agent |
| `/ui/api/messages/send` | Send a unicast message |

All `/ui/api/*` endpoints require `Authorization: Bearer <api_key>` header. The server computes `SHA256(api_key)` to identify the tenant (same pattern as existing auth).

### Authentication Flow

1. User enters API key in login form (password input field)
2. SPA sends `POST /ui/api/login` with `Authorization: Bearer <api_key>`
3. Server computes `tenant_id = SHA256(api_key)`, checks for any agents associated with this tenant: active agents in `tenant:{tenant_id}:agents` OR deregistered agents with matching `api_key_hash` that still have messages in Redis. Login succeeds if at least one such agent exists.
4. On success: returns list of agents in tenant. SPA stores API key in memory, navigates to dashboard
5. On failure (no agents found at all): returns 401. SPA shows error message
6. Page refresh clears memory — user must re-login

No server-side session. No cookies. The raw API key lives only in a JavaScript variable.

### WebUI API Endpoints

#### `POST /ui/api/login`

Validates the API key and returns tenant agents.

Request:
```
Authorization: Bearer <api_key>
```

Response (200):
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

Response (401):
```json
{"error": "Invalid API key"}
```

The agent list includes both active and deregistered agents that still have tasks in Redis. Active agents are identified from `tenant:{tenant_id}:agents`. Deregistered agents are found by scanning `agent:*` records with matching `api_key_hash` and `status=deregistered`.

#### `GET /ui/api/agents`

Same as login response's agent list. Used for manual refresh.

#### `GET /ui/api/agents/{agent_id}/inbox`

Returns messages received by the agent (tasks where `context_id = agent_id`), excluding `broadcast_summary` type tasks.

Query parameters:
- None (returns all messages, newest first)

Response (200):
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

Status values map to display labels:

| Task State | Display Label | Badge Color |
|------------|--------------|-------------|
| `input_required` | Pending | Yellow |
| `completed` | Acknowledged | Green |
| `canceled` | Canceled | Gray |

#### `GET /ui/api/agents/{agent_id}/sent`

Returns messages sent by the agent (task IDs from `tasks:sender:{agent_id}`), excluding `broadcast_summary` type tasks.

Same response format as inbox.

#### `POST /ui/api/messages/send`

Sends a unicast message to a destination agent within the same tenant.

Request:
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

The server verifies both agents belong to the caller's tenant, then constructs a `Message` and `RequestContext` directly and calls `executor.execute()`. This does NOT reuse the `_handle_send_message` helper from `main.py` (which is tightly coupled to JSON-RPC request parsing); instead, the WebUI endpoint builds the A2A objects itself and invokes the executor directly. The `from_agent_id` must belong to the tenant (verified via `tenant:{tenant_id}:agents` membership).

Response (200):
```json
{
  "task_id": "uuid",
  "status": "input_required"
}
```

Error responses: 401 (invalid key), 400 (missing fields), 404 (agent not found or cross-tenant).

### UI Components

#### Login Page (`/ui/`)

- Centered card with Hikyaku branding
- Single password input field labeled "API Key"
- "Login" button
- Error message display area
- Footer note: "Data is ephemeral — stored in Redis only. Cleanup deletes tasks after deregistration TTL. Redis restart without persistence config loses all data."

#### Dashboard (post-login)

- **Header**: "Hikyaku — {tenant_id (first 8 chars)}" + Logout button
- **Agent tabs**: Horizontal tab bar. Each tab shows agent name. Active agents have normal styling. Deregistered agents are grayed out with "(deregistered)" suffix. Tabs are sorted: active agents first (alphabetical), then deregistered (alphabetical).
- **Selected agent panel**:
  - Two sub-tabs: "Inbox" and "Sent"
  - Refresh button (manual, reloads current view)
  - Message list (chronological, newest first)
  - "No messages yet" placeholder when empty
- **Send message form** (below message list, visible only on Inbox/Sent tab of active agents):
  - Dropdown: "To" agent (lists other active agents in tenant)
  - Text area: message body
  - "Send" button
  - Success/error feedback

#### Message Row

Each message row displays:

| Field | Source | Display |
|-------|--------|---------|
| Direction indicator | Inbox vs Sent tab | Arrow icon or "From:"/"To:" prefix |
| Counterpart agent | `from_agent_name` (inbox) or `to_agent_name` (sent) | Agent name |
| Date | `created_at` | Formatted datetime (e.g., "2026-03-29 10:00") |
| Status | Task state | Colored badge: Pending (yellow), Acknowledged (green), Canceled (gray) |
| Body | First text part from artifacts | Truncated preview (expandable on click) |

### Data Fetching from Redis

The WebUI API endpoints reuse existing store classes where possible:

| Endpoint | Redis Keys Used | Store Method |
|----------|----------------|--------------|
| Login / List Agents | `tenant:{hash}:agents`, `agent:{id}` | `RegistryStore.list_active_agents(tenant_id)` + scan for deregistered |
| Inbox | `tasks:ctx:{agent_id}` (sorted set) | `RedisTaskStore.list(context_id)` |
| Sent | `tasks:sender:{agent_id}` (set), `task:{id}` (hash) | New: iterate sender set, load each task |
| Send | Delegates to `BrokerExecutor` | Existing executor logic |

**Deregistered agent discovery**: To find deregistered agents that still have messages, the login endpoint scans `agent:*` keys with matching `api_key_hash` and `status=deregistered`, then checks if `tasks:ctx:{agent_id}` has any entries. This is acceptable because the scan runs once at login, not on every request.

**Sent messages**: The `tasks:sender:{agent_id}` set contains task IDs but is unordered. The endpoint fetches each task hash to get `created_at`, then sorts by date descending. Broadcast summary tasks (type `broadcast_summary`) are filtered out.

**Agent name resolution**: Message responses include sender/recipient agent names for display. The endpoint batch-fetches `agent:{id}` records for all referenced agent IDs.

### Ephemeral Data Warning

A persistent banner or footer note on the dashboard:

> "Message data is stored in Redis only and is ephemeral. The cleanup process deletes tasks after the deregistration TTL expires. A Redis restart without persistence configuration will lose all data."

### File Structure

```
admin/                              # SPA source (Vite + React + TypeScript)
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/
    main.tsx
    App.tsx
    api.ts                          # API client (fetch wrapper with auth header)
    types.ts                        # TypeScript interfaces
    components/
      LoginPage.tsx
      Dashboard.tsx
      AgentTabs.tsx
      MessageList.tsx
      MessageRow.tsx
      SendMessageForm.tsx
  dist/                             # Built output (gitignored)
registry/
  src/hikyaku_registry/
    webui_api.py                    # New: FastAPI router for /ui/api/*
    main.py                         # Modified: mount webui router + static files
```

The `admin/dist/` directory is gitignored. The build step (`npm run build` in `admin/`) produces the static files. In development, Vite dev server proxies API calls to FastAPI.

### Affected Files

| File | Changes |
|------|---------|
| `registry/src/hikyaku_registry/webui_api.py` | **New**: FastAPI router with login, agents, inbox, sent, send endpoints |
| `registry/src/hikyaku_registry/main.py` | Include `webui_router` BEFORE mounting `StaticFiles` at `/ui` (router must take precedence over static file catch-all) |
| `admin/` | **New**: Entire SPA project (Vite + React + TypeScript + Tailwind) |
| `registry/pyproject.toml` | Add `jinja2` dependency (required by `StaticFiles` HTML mode for SPA fallback) |
| `.gitignore` | Add `admin/dist/`, `admin/node_modules/` |

### Error Handling

| Error Condition | Behavior |
|-----------------|----------|
| Invalid API key at login | 401 → SPA shows "Invalid API key" |
| Expired tenant (all agents deregistered, no messages) | 401 → same as invalid key |
| Agent not found (deleted by cleanup between requests) | 404 → SPA removes agent from tabs |
| Send to cross-tenant agent | 404 → SPA shows "Agent not found" |
| Send to deregistered agent | 400 → SPA shows "Agent is deregistered" |
| Redis connection failure | 500 → SPA shows "Server error, try again later" |
| Empty inbox/sent | 200 with empty array → SPA shows "No messages yet" |

---

## Implementation

> Task format: `- [x] Done task <!-- completed: 2026-02-13T14:30 -->`
> When completing a task, check the box and record the timestamp in the same edit.

### Step 1: WebUI API Backend

- [x] Create `registry/src/hikyaku_registry/webui_api.py` with `webui_router = APIRouter(prefix="/ui/api")` <!-- completed: 2026-03-29T12:00 -->
- [x] Implement `POST /ui/api/login` endpoint: extract Bearer token, compute SHA256, verify tenant exists, return agent list (active + deregistered-with-messages) <!-- completed: 2026-03-29T12:00 -->
- [x] Implement `GET /ui/api/agents` endpoint: same logic as login agent list, requires auth <!-- completed: 2026-03-29T12:00 -->
- [x] Implement `GET /ui/api/agents/{agent_id}/inbox` endpoint: use `RedisTaskStore.list(agent_id)`, filter out broadcast_summary, extract body text, resolve agent names <!-- completed: 2026-03-29T12:00 -->
- [x] Implement `GET /ui/api/agents/{agent_id}/sent` endpoint: read `tasks:sender:{agent_id}` set, load each task, filter broadcast_summary, sort by date desc, resolve agent names <!-- completed: 2026-03-29T12:00 -->
- [x] Implement `POST /ui/api/messages/send` endpoint: validate tenant membership for both from/to agents, delegate to BrokerExecutor <!-- completed: 2026-03-29T12:00 -->
- [x] Add shared auth dependency for WebUI endpoints (extract Bearer, compute tenant_id, verify tenant exists) <!-- completed: 2026-03-29T12:00 -->

### Step 2: Mount WebUI in FastAPI

- [x] Modify `main.py`: import and include `webui_router` BEFORE `StaticFiles` mount so API routes take precedence <!-- completed: 2026-03-29T12:30 -->
- [x] Mount `StaticFiles` at `/ui` pointing to `admin/dist/` with `html=True` for SPA fallback (must come AFTER router inclusion) <!-- completed: 2026-03-29T12:30 -->
- [x] Add `jinja2` to `registry/pyproject.toml` dependencies (required by Starlette's `StaticFiles` html mode) <!-- completed: 2026-03-29T12:30 -->

### Step 3: SPA Project Setup

- [x] Set up `admin/` with `package.json` (Vite 8.x, React 19.x, TypeScript, Tailwind CSS 4.x) <!-- completed: 2026-03-29T13:00 -->
- [x] Configure `vite.config.ts` with API proxy to `http://localhost:8000` for dev mode <!-- completed: 2026-03-29T13:00 -->
- [x] Set up Tailwind CSS 4.x (CSS-first config via `@import "tailwindcss"`) <!-- completed: 2026-03-29T13:00 -->
- [x] Create `src/api.ts`: fetch wrapper that injects `Authorization: Bearer` header from in-memory API key <!-- completed: 2026-03-29T13:00 -->
- [x] Create `src/types.ts`: TypeScript interfaces for Agent, Message, API responses <!-- completed: 2026-03-29T13:00 -->

### Step 4: SPA UI Components

- [x] Implement `LoginPage.tsx`: password input, login button, error display, ephemeral data footer note <!-- completed: 2026-03-29T13:30 -->
- [x] Implement `Dashboard.tsx`: header with tenant ID + logout, agent tabs, content area <!-- completed: 2026-03-29T13:30 -->
- [x] Implement `AgentTabs.tsx`: horizontal tabs with active/deregistered styling, sub-tabs for Inbox/Sent <!-- completed: 2026-03-29T13:30 -->
- [x] Implement `MessageList.tsx` + `MessageRow.tsx`: chronological message display with status badges and expandable body <!-- completed: 2026-03-29T13:30 -->
- [x] Implement `SendMessageForm.tsx`: destination dropdown, text area, send button with feedback <!-- completed: 2026-03-29T13:30 -->

### Step 5: Build and Gitignore

- [x] Add `admin/dist/` and `admin/node_modules/` to `.gitignore` <!-- completed: 2026-03-29T14:00 -->
- [x] Verify build produces `admin/dist/` that FastAPI serves correctly at `/ui/` <!-- completed: 2026-03-29T14:00 -->

### Step 6: Backend Tests

- [ ] Write tests for `webui_api.py`: login (valid/invalid key), agent list, inbox, sent, send message, cross-tenant rejection, deregistered agent handling <!-- completed: -->

---

## Changelog (optional -- spec revisions only, not implementation progress)

| Date | Changes |
|------|---------|
| 2026-03-29 | Initial draft |
| 2026-03-29 | Approved after review. Fixed auth flow for deregistered tenants, added to_agent_name, clarified mount order and send endpoint. |
| 2026-03-29 | Implementation complete. SPA moved from registry/webui/ to admin/. Added cross-tenant isolation fix for inbox/sent endpoints. |
