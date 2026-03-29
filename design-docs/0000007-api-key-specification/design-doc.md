# API Key Specification: WebUI-Issued Keys with Auth0

**Status**: Complete
**Progress**: 43/43 tasks complete
**Last Updated**: 2026-03-29

## Overview

Move API key issuance from CLI-based agent registration to the WebUI, backed by Auth0 for user authentication. Users log into the WebUI via Auth0, create and manage API keys through a key management interface, and distribute keys to agents. The CLI `hikyaku register` command always requires a pre-existing API key — it no longer generates keys.

## Success Criteria

- [ ] WebUI login uses Auth0 (OIDC) instead of direct API key entry
- [ ] Users can create, list, and revoke API keys through the WebUI
- [ ] One Auth0 account can own multiple API keys (= multiple tenants)
- [ ] `hikyaku register` requires `--api-key` (no unauthenticated key generation)
- [ ] `POST /api/v1/agents` without `Authorization` header returns 401
- [ ] Revoking an API key deregisters all agents under that tenant
- [ ] Existing tenant isolation model (shared key = same tenant) is preserved unchanged
- [ ] Agent-to-broker authentication (`Authorization: Bearer <api_key>` + `X-Agent-Id`) is unchanged

---

## Background

The current system generates API keys inside `RegistryStore.create_agent` when an agent registers without an `Authorization` header. The API key is returned once in the registration response and serves as the shared tenant credential. The WebUI authenticates by accepting an API key directly (`POST /ui/api/login`), with no user accounts or sessions.

This has two problems:

1. **Key issuance is tied to the agent lifecycle.** Keys are created as a side effect of agent registration, making it impossible to pre-provision keys before agents exist or to manage keys independently of agents.
2. **No user accounts or centralized key management.** There is no way to list, audit, or revoke keys. The WebUI has no concept of "who owns this key."

This design introduces Auth0 for WebUI user authentication and moves API key CRUD into the WebUI. The tenant isolation model (shared key = same tenant) and agent-to-broker authentication flow (`Bearer <api_key>` + `X-Agent-Id`) are unchanged.

**Breaking change**: The "create new tenant" registration flow (no `Authorization` header) is removed. All existing agent registrations are invalidated. Agents must re-register using WebUI-issued API keys.

**Migration**: No data migration is provided. Existing Redis data (`agent:*`, `tenant:*:agents`, `agents:active`) will lack `apikey:{hash}` records, so old API keys will fail the new auth check (`apikey:{hash}` status lookup returns `nil` → 401). Deployment should start with a fresh Redis instance (`FLUSHDB` or new Redis database). This is acceptable because Hikyaku data is ephemeral by design — agents re-register on startup and messages have TTL-based cleanup.

---

## Specification

### Auth0 Integration

Auth0 provides user identity for the WebUI only. Agent-to-broker communication continues to use API keys.

| Concern | Mechanism |
|---|---|
| WebUI login | Auth0 SPA SDK (PKCE flow) → Auth0 JWT |
| WebUI API auth | `Authorization: Bearer <auth0_jwt>` validated via `PyJWKClient` + Auth0 JWKS |
| Agent-to-broker auth | `Authorization: Bearer <api_key>` + `X-Agent-Id` (unchanged) |
| User identity | Auth0 `sub` claim (stable, unique per user) |

**Server-side JWT validation** (following the project's Auth0 pattern from `sample/auth0_verifier.py`):

```python
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

class Auth0Verifier:
    _jwks_client: ClassVar[jwt.PyJWKClient | None] = None

    @classmethod
    def get_jwks_client(cls) -> jwt.PyJWKClient:
        if cls._jwks_client is None:
            jwks_url = f"https://{settings.auth0_domain}/.well-known/jwks.json"
            cls._jwks_client = jwt.PyJWKClient(
                jwks_url, cache_keys=True, lifespan=60 * 60 * 24
            )
        return cls._jwks_client

async def verify_auth0_user(
    request: Request,
    cred: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> None:
    """Validate Auth0 JWT, store decoded token in request.scope."""
    try:
        signing_key = Auth0Verifier.get_jwks_client().get_signing_key_from_jwt(
            cred.credentials
        )
        decoded_token = jwt.decode(
            jwt=cred.credentials,
            key=signing_key.key,
            algorithms=["RS256"],
            audience=settings.auth0_client_id,
        )
    except jwt.exceptions.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    request.scope["token"] = cred.credentials
    request.scope["auth0"] = decoded_token

def get_user_id(request: Request) -> str:
    """Extract Auth0 sub claim from request scope (set by verify_auth0_user)."""
    if (user_id := request.scope.get("auth0", {}).get("sub")) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user_id
```

The `verify_auth0_user` dependency is applied to all WebUI endpoints. Helper `get_user_id` extracts the `sub` claim from the decoded token stored in `request.scope["auth0"]`.

**New configuration** (added to `Settings` in `config.py`):

| Setting | Env Var | Description |
|---|---|---|
| `auth0_domain` | `AUTH0_DOMAIN` | Auth0 tenant domain (e.g., `myapp.auth0.com`) |
| `auth0_client_id` | `AUTH0_CLIENT_ID` | SPA client ID, also used as JWT audience for token validation |

### Redis Schema Changes

Two new key patterns for API key records and account-to-key mappings:

| Key Pattern | Type | Change | Description |
|---|---|---|---|
| `apikey:{api_key_hash}` | Hash | **New** | API key metadata: `owner_sub`, `created_at`, `status`, `key_prefix` |
| `account:{auth0_sub}:keys` | Set | **New** | Set of `api_key_hash` values owned by this Auth0 account |
| `agent:{agent_id}` | Hash | Unchanged | Agent metadata (still contains `api_key_hash`) |
| `tenant:{api_key_hash}:agents` | Set | Unchanged | Active agent_ids in this tenant |
| `agents:active` | Set | Unchanged | Global active agent set (cleanup scanning) |

**`apikey:{api_key_hash}` record fields**:

```
owner_sub    → Auth0 sub claim (e.g., "auth0|abc123")
created_at   → ISO 8601 timestamp
status       → "active" | "revoked"
key_prefix   → First 8 chars of raw key (e.g., "hky_a1b2") for display
```

The `apikey:{hash}` record is the source of truth for key existence and validity. This replaces the previous behavior where key existence was inferred from `tenant:{hash}:agents` set membership.

### WebUI API Changes

All WebUI endpoints switch from API key auth to Auth0 JWT auth. Tenant-scoped endpoints additionally require an `X-Tenant-Id` header to select which tenant's data to view.

**Authentication headers for WebUI API**:

| Header | Required | Purpose |
|---|---|---|
| `Authorization: Bearer <auth0_jwt>` | Always | Identifies the Auth0 user |
| `X-Tenant-Id: <api_key_hash>` | On tenant-scoped endpoints | Selects the tenant to operate on |

Backend validates that the `X-Tenant-Id` belongs to the authenticated Auth0 user by checking `account:{sub}:keys` set membership.

**New endpoints**:

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/ui/api/auth/config` | None | Returns Auth0 domain + client_id for SPA initialization |
| `POST` | `/ui/api/keys` | JWT | Create a new API key. Returns `{api_key, tenant_id, created_at}`. Raw key shown only once. |
| `GET` | `/ui/api/keys` | JWT | List API keys owned by the authenticated user. Returns `[{tenant_id, key_prefix, created_at, status, agent_count}]`. Does NOT return raw keys. |
| `DELETE` | `/ui/api/keys/{tenant_id}` | JWT | Revoke an API key. Deregisters all agents under the tenant. |

**Modified endpoints** (auth mechanism change only):

| Method | Path | Auth Change |
|---|---|---|
| `GET` | `/ui/api/agents` | API key → JWT + `X-Tenant-Id` |
| `GET` | `/ui/api/agents/{agent_id}/inbox` | API key → JWT + `X-Tenant-Id` |
| `GET` | `/ui/api/agents/{agent_id}/sent` | API key → JWT + `X-Tenant-Id` |
| `POST` | `/ui/api/messages/send` | API key → JWT + `X-Tenant-Id` |

**Removed endpoints**:

| Method | Path | Reason |
|---|---|---|
| `POST` | `/ui/api/login` | Replaced by Auth0 OIDC flow |

### Key Management Logic

**Create key** (`POST /ui/api/keys`):

1. Validate Auth0 JWT → extract `sub`
2. Generate `api_key = "hky_" + secrets.token_hex(16)`
3. Compute `api_key_hash = SHA256(api_key)`
4. Store `apikey:{hash}` record: `{owner_sub: sub, created_at: now, status: "active", key_prefix: api_key[:8]}`
5. Add `api_key_hash` to `account:{sub}:keys` set
6. Return `{api_key: "hky_...", tenant_id: hash, created_at: "..."}` — raw key shown once

**List keys** (`GET /ui/api/keys`):

1. Validate Auth0 JWT → extract `sub`
2. Get all hashes from `account:{sub}:keys`
3. For each hash, read `apikey:{hash}` and count `tenant:{hash}:agents`
4. Return list of `{tenant_id, key_prefix, created_at, status, agent_count}`

**Revoke key** (`DELETE /ui/api/keys/{tenant_id}`):

1. Validate Auth0 JWT → extract `sub`
2. Verify `tenant_id` is in `account:{sub}:keys` → 404 if not
3. Set `apikey:{tenant_id}` status to `"revoked"`
4. Get all agent_ids from `tenant:{tenant_id}:agents`
5. Deregister each agent (reuse `RegistryStore.deregister_agent`)
6. Return 204

### Agent Authentication Change

`get_authenticated_agent` in `auth.py` gains one additional check: verify the API key record exists and is active. This enables key revocation to take effect immediately.

```python
async def get_authenticated_agent(request, store) -> tuple[str, str]:
    token = _extract_bearer_token(request)
    tenant_id = hashlib.sha256(token.encode()).hexdigest()

    # NEW: Check API key is active
    key_status = await store._redis.hget(f"apikey:{tenant_id}", "status")
    if key_status != "active":
        raise HTTPException(status_code=401)

    agent_id = request.headers.get("x-agent-id")
    if not agent_id:
        raise HTTPException(status_code=401)

    agent_key_hash = await store._redis.hget(f"agent:{agent_id}", "api_key_hash")
    if agent_key_hash != tenant_id:
        raise HTTPException(status_code=401)

    return (agent_id, tenant_id)
```

This adds one extra Redis `HGET` per authenticated request. Redis `HGET` is O(1), so the impact is negligible.

### Registration Flow Change

The "create new tenant" flow (no `Authorization` header) is removed. Registration always requires a valid API key.

**Before**:

| `Authorization` header | Behavior |
|---|---|
| Absent | Create new tenant, generate fresh API key |
| Present | Join existing tenant, reuse provided key |

**After**:

| `Authorization` header | Behavior |
|---|---|
| Absent | 401 Unauthorized |
| Present | Join existing tenant (key must be active in `apikey:{hash}`) |

**Changes to `get_registration_tenant`**:

```python
async def get_registration_tenant(request, store) -> tuple[str, str]:
    """Extract API key for registration. Always required.

    Returns (api_key, api_key_hash).
    Raises HTTPException(401) if missing, malformed, or key is revoked.
    """
    token = _extract_bearer_token(request)  # raises 401 if missing
    api_key_hash = hashlib.sha256(token.encode()).hexdigest()

    # Check key exists and is active
    key_status = await store._redis.hget(f"apikey:{api_key_hash}", "status")
    if key_status != "active":
        raise HTTPException(status_code=401)

    return (token, api_key_hash)
```

**Changes to `RegistryStore.create_agent`**: Remove the `if api_key is None: api_key = ...` key generation logic. The `api_key` parameter becomes required (non-optional).

### CLI Changes

Currently there are two `--api-key` parameters: a global one on the `cli` group (line 33, env var `HIKYAKU_API_KEY`) and a register-specific one (`join_api_key`, line 65) used only for join-tenant flow. This design **removes the register-specific `--api-key`** and makes `register` use the global `--api-key` like all other commands. This is consistent — every command reads from the same `ctx.obj["api_key"]`.

The `register` command validates that the global `--api-key` is set before making the request (similar to `_require_auth` but only checking `api_key`, not `agent_id`).

```bash
# Before: created a new tenant (no --api-key needed)
hikyaku register --name "Agent A" --description "My agent"

# After: uses global --api-key (required)
hikyaku --api-key "hky_..." register --name "Agent A" --description "My agent"
# Or via env var (recommended):
export HIKYAKU_API_KEY="hky_..."
hikyaku register --name "Agent A" --description "My agent"
```

Error message when `--api-key` is missing: `"Error: --api-key is required for registration. Create an API key at the Hikyaku WebUI."`

### MCP Server Impact

The `hikyaku-mcp` package (documented in ARCHITECTURE.md, not yet implemented) is a transparent proxy that forwards all requests to the broker using the same `HIKYAKU_API_KEY` and `HIKYAKU_AGENT_ID` environment variables. Since agent-to-broker authentication (`Authorization: Bearer <api_key>` + `X-Agent-Id`) is unchanged by this design, **the MCP server requires no code changes**. Operators simply configure it with a WebUI-issued API key instead of a CLI-generated one.

### WebUI Frontend Changes

The `admin/` SPA gets three major changes:

1. **Auth0 login**: Replace `LoginPage` component with Auth0 SPA SDK (`@auth0/auth0-react`). Use `Auth0Provider` wrapping the app, `useAuth0()` hook for login/logout, and `getAccessTokenSilently()` to get JWTs for API calls.

2. **Key management page**: New page after login showing the user's API keys. Buttons to create and revoke keys. Created keys show the raw key value once in a copy-friendly format.

3. **Tenant selection**: After selecting a key, the existing Dashboard view loads with that tenant's agents and messages. The `X-Tenant-Id` header is sent on all subsequent API requests.

**Frontend API client changes** (`admin/src/api.ts`):

```typescript
// Before: Bearer API key
headers["Authorization"] = `Bearer ${apiKey}`;

// After: Bearer Auth0 JWT + tenant selection
headers["Authorization"] = `Bearer ${auth0Token}`;
if (tenantId) {
  headers["X-Tenant-Id"] = tenantId;
}
```

### Affected Files

| File | Changes |
|---|---|
| `ARCHITECTURE.md` | Add Auth0 integration, two auth surfaces, update WebUI section |
| `docs/spec/registry-api.md` | Remove "create new tenant" flow, document `--api-key` requirement |
| `docs/spec/data-model.md` | Add `apikey:{hash}` and `account:{sub}:keys` key patterns |
| `README.md` | Note Auth0 requirement, new WebUI login flow, key management |
| `registry/src/hikyaku_registry/config.py` | Add `auth0_domain`, `auth0_client_id` settings |
| `registry/src/hikyaku_registry/auth.py` | Add `Auth0Verifier` class, `verify_auth0_user` dependency, `get_user_id` helper for WebUI; update `get_authenticated_agent` to check key status; update `get_registration_tenant` to require auth always |
| `registry/src/hikyaku_registry/registry_store.py` | Make `api_key` required in `create_agent`; remove key generation; add `create_api_key`, `list_api_keys`, `revoke_api_key` methods |
| `registry/src/hikyaku_registry/webui_api.py` | Replace API key auth with JWT auth; add `X-Tenant-Id` handling; add key management endpoints; remove `POST /login`; add `GET /auth/config` |
| `registry/src/hikyaku_registry/api/registry.py` | Update `register_agent` to handle required auth |
| `registry/src/hikyaku_registry/cleanup.py` | No changes (cleanup still uses agent records) |
| `client/src/hikyaku_client/cli.py` | Remove register-specific `--api-key` (`join_api_key`); `register` uses global `--api-key`; validate presence before request |
| `client/src/hikyaku_client/api.py` | Update `register_agent` to always send `Authorization` header |
| `admin/package.json` | Add `@auth0/auth0-react` dependency |
| `admin/src/App.tsx` | Wrap with `Auth0Provider`; add routing for key management vs dashboard |
| `admin/src/api.ts` | Replace API key auth with Auth0 JWT + `X-Tenant-Id` |
| `admin/src/components/LoginPage.tsx` | Replace with Auth0 login (or remove entirely if using Auth0's Universal Login) |
| `admin/src/components/KeyManagement.tsx` | **New** — key list, create, revoke UI |
| `registry/pyproject.toml` | Add `PyJWT[crypto]` dependency |

---

## Implementation

> Task format: `- [x] Done task <!-- completed: 2026-02-13T14:30 -->`
> When completing a task, check the box and record the timestamp in the same edit.

### Step 0: Documentation Updates

- [x] Update `ARCHITECTURE.md`: add Auth0 integration to architecture, document two auth surfaces (Auth0 JWT for WebUI, API key for agents), update WebUI section <!-- completed: 2026-03-29T13:50 -->
- [x] Update `docs/spec/registry-api.md`: remove "create new tenant" registration flow, document `--api-key` requirement, add `apikey:{hash}` key status check <!-- completed: 2026-03-29T13:50 -->
- [x] Update `docs/spec/data-model.md`: add `apikey:{hash}` and `account:{sub}:keys` key patterns <!-- completed: 2026-03-29T13:50 -->
- [x] Update README: note Auth0 requirement, new WebUI login flow, key management <!-- completed: 2026-03-29T13:50 -->

### Step 1: Auth0 Configuration & JWT Validation

- [x] Add `auth0_domain`, `auth0_client_id` to `Settings` in `config.py` <!-- completed: 2026-03-29T13:58 -->
- [x] Add `PyJWT[crypto]` to `registry/pyproject.toml` <!-- completed: 2026-03-29T13:58 -->
- [x] Implement `Auth0Verifier` class in `auth.py` with `PyJWKClient` (24-hour key cache via `lifespan`) <!-- completed: 2026-03-29T13:58 -->
- [x] Implement `verify_auth0_user(request, cred)` FastAPI dependency: validates JWT, stores decoded token in `request.scope["auth0"]` <!-- completed: 2026-03-29T13:58 -->
- [x] Implement `get_user_id(request)` helper: extracts `sub` claim from `request.scope["auth0"]` <!-- completed: 2026-03-29T13:58 -->
- [x] Add tests for JWT validation (valid token, expired, wrong audience, invalid signature) <!-- completed: 2026-03-29T13:58 -->

### Step 2: Redis Schema for API Key Records

- [x] Add `create_api_key(owner_sub)` to `RegistryStore`: generates key, stores `apikey:{hash}` record, adds to `account:{sub}:keys` <!-- completed: 2026-03-29T14:02 -->
- [x] Add `list_api_keys(owner_sub)` to `RegistryStore`: reads `account:{sub}:keys` and `apikey:{hash}` records <!-- completed: 2026-03-29T14:02 -->
- [x] Add `revoke_api_key(tenant_id, owner_sub)` to `RegistryStore`: verifies ownership, sets status to "revoked", deregisters all tenant agents <!-- completed: 2026-03-29T14:02 -->
- [x] Add `get_api_key_status(tenant_id)` to `RegistryStore`: returns status from `apikey:{hash}` <!-- completed: 2026-03-29T14:02 -->
- [x] Add tests for key CRUD (create, list, revoke, revoke non-owned key) <!-- completed: 2026-03-29T14:02 -->

### Step 3: Key Management Endpoints (WebUI Backend)

- [x] Add `GET /ui/api/auth/config` endpoint returning Auth0 client config (no auth required) <!-- completed: 2026-03-29T14:06 -->
- [x] Add `POST /ui/api/keys` endpoint (JWT auth) <!-- completed: 2026-03-29T14:06 -->
- [x] Add `GET /ui/api/keys` endpoint (JWT auth) <!-- completed: 2026-03-29T14:06 -->
- [x] Add `DELETE /ui/api/keys/{tenant_id}` endpoint (JWT auth + ownership check) <!-- completed: 2026-03-29T14:06 -->
- [x] Add tests for key management endpoints <!-- completed: 2026-03-29T14:06 -->

### Step 4: WebUI Auth Migration (Backend)

- [x] Create `get_webui_tenant(request, store)` dependency: uses `verify_auth0_user` + `get_user_id` for JWT auth, extracts `X-Tenant-Id`, verifies user owns the tenant via `account:{sub}:keys` <!-- completed: 2026-03-29T14:11 -->
- [x] Update `GET /ui/api/agents` to use JWT + `X-Tenant-Id` auth <!-- completed: 2026-03-29T14:11 -->
- [x] Update `GET /ui/api/agents/{agent_id}/inbox` to use JWT + `X-Tenant-Id` auth <!-- completed: 2026-03-29T14:11 -->
- [x] Update `GET /ui/api/agents/{agent_id}/sent` to use JWT + `X-Tenant-Id` auth <!-- completed: 2026-03-29T14:11 -->
- [x] Update `POST /ui/api/messages/send` to use JWT + `X-Tenant-Id` auth <!-- completed: 2026-03-29T14:11 -->
- [x] Remove `POST /ui/api/login` endpoint <!-- completed: 2026-03-29T14:11 -->
- [x] Remove `_authenticate_tenant` helper (replaced by `get_webui_tenant`) <!-- completed: 2026-03-29T14:11 -->
- [x] Add tests for JWT-based WebUI auth (valid, invalid, wrong tenant ownership) <!-- completed: 2026-03-29T14:11 -->

### Step 5: Agent Auth & Registration Changes (Server)

- [x] Update `get_authenticated_agent` in `auth.py` to check `apikey:{hash}` status <!-- completed: 2026-03-29 -->
- [x] Update `get_registration_tenant` in `auth.py` to always require auth and check `apikey:{hash}` <!-- completed: 2026-03-29 -->
- [x] Make `api_key` parameter required in `RegistryStore.create_agent`, remove key generation <!-- completed: 2026-03-29 -->
- [x] Update `register_agent` endpoint to handle required auth (no more `None` from `get_registration_tenant`) <!-- completed: 2026-03-29 -->
- [x] Add tests for registration with revoked key, without auth, with valid key <!-- completed: 2026-03-29 -->

### Step 6: CLI Changes

- [x] Remove register-specific `--api-key` (`join_api_key`) from `register` command in `cli.py` <!-- completed: 2026-03-29 -->
- [x] Update `register` command to read global `ctx.obj["api_key"]` and validate it is set (error if missing) <!-- completed: 2026-03-29 -->
- [x] Update `register_agent` in `api.py` to always send `Authorization` header <!-- completed: 2026-03-29 -->
- [x] Update `test_cli.py` with tests for missing API key error and global key usage <!-- completed: 2026-03-29 -->

### Step 7: WebUI Frontend Changes

- [x] Add `@auth0/auth0-react` to `admin/package.json` <!-- completed: 2026-03-29 -->
- [x] Wrap `App` with `Auth0Provider`, fetch config from `/ui/api/auth/config` <!-- completed: 2026-03-29 -->
- [x] Replace `LoginPage` with Auth0-based login flow <!-- completed: 2026-03-29 -->
- [x] Update `api.ts`: replace API key auth with Auth0 JWT + `X-Tenant-Id` header <!-- completed: 2026-03-29 -->
- [x] Create `KeyManagement.tsx` component: list keys, create key (show raw key once), revoke key <!-- completed: 2026-03-29 -->
- [x] Update `App.tsx` routing: Auth0 login → key management → dashboard (with tenant selection) <!-- completed: 2026-03-29 -->

---

## Changelog (optional -- spec revisions only, not implementation progress)

| Date | Changes |
|------|---------|
| 2026-03-29 | Initial draft |
| 2026-03-29 | Approved after review |
