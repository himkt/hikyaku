# Introduce Type Checking with ty

**Status**: Approved
**Progress**: 11/12 tasks complete
**Last Updated**: 2026-03-29

## Overview

Add static type checking to the Hikyaku project using [ty](https://docs.astral.sh/ty/), Astral's Rust-based Python type checker. This includes adding type annotations to all source files in both packages (registry and client), configuring ty in `pyproject.toml`, and integrating it into the existing CI lint job as a required gate.

## Success Criteria

- [ ] `uv run ty check` passes with zero errors on `registry/src/` and `client/src/`
- [ ] CI lint job runs ty check and blocks on failure
- [ ] All public functions in both packages have explicit type annotations (parameters and return types)

---

## Background

Most functions in the codebase already have type annotations on parameters and return types. The main gaps are: bare `dict` return types in `RegistryStore` methods (e.g., `create_agent -> dict` instead of a typed dict shape), untyped `store` parameters in `auth.py`, untyped `message` parameters in `executor.py`, missing return types on FastAPI endpoint functions, and a few untyped helpers in the client CLI. The project uses several third-party libraries (`a2a-sdk`, `redis`, `FastAPI`, `click`, `httpx`, `pydantic`) that may lack type stubs. ty is chosen over mypy/Pyright for its speed (10-100x faster) and alignment with the existing Astral toolchain (uv, ruff).

---

## Specification

### ty Configuration

Add to the root `pyproject.toml`:

```toml
[tool.ty]
python-version = "3.12"

[tool.ty.src]
include = ["registry/src", "client/src"]

[tool.ty.analysis]
# Suppress errors for third-party libraries without type stubs.
# Add more patterns as needed when new dependencies are introduced.
allowed-unresolved-imports = [
    "a2a.*",
    "redis.*",
    "fakeredis.*",
    "pydantic_settings.*",
    "sse_starlette.*",
]

[tool.ty.rules]
# Keep defaults (most rules are error-level).
# Override specific rules as needed during initial rollout.
```

**Key decisions:**

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Config location | Root `pyproject.toml` | Single config for the monorepo; consistent with `[tool.ruff]` |
| Python version | 3.12 | Matches `requires-python = ">=3.12"` |
| Check scope | `registry/src` + `client/src` | Source code only; tests excluded per requirements |
| Third-party imports | `allowed-unresolved-imports` | Suppresses unresolvable stubs without replacing types with `Any` |
| Rule severity | Default (all errors) | Required CI gate needs strict defaults |

### Type Annotations to Add

The annotation work focuses on adding or refining types on function signatures that are currently untyped or use bare `dict`. Internal variables do not need annotations where ty can infer types. Files that are already fully typed are listed as "No changes" for completeness.

**registry package** (13 files):

| File | Current State | Changes |
|------|---------------|---------|
| `__init__.py` | Empty file. | No changes. |
| `config.py` | Fully typed via Pydantic `BaseSettings`. | No changes. |
| `models.py` | Fully typed via Pydantic `BaseModel`. | No changes. |
| `redis_client.py` | Fully typed (`_pool` annotated, all functions have param and return types). | No changes. |
| `task_store.py` | Fully typed (all params and return types annotated). | No changes. |
| `agent_card.py` | Fully typed. | No changes. |
| `cleanup.py` | Fully typed. | No changes. |
| `registry_store.py` | All functions have return types, but `create_agent -> dict`, `get_agent -> dict | None`, `list_active_agents -> list[dict]` use bare `dict`. `deregister_agent -> bool` and `verify_agent_tenant -> bool` are precise. | Introduce `TypedDict` classes for agent record shapes. Refine `create_agent`, `get_agent`, `list_active_agents` return types from bare `dict` to typed dicts. |
| `executor.py` | Most methods fully typed. `_handle_unicast(message)` and `_handle_broadcast(message)` have an untyped `message` parameter. `_handle_ack` is fully typed (no `message` param). | Add `Message` type annotation to the `message` parameter in `_handle_unicast` and `_handle_broadcast`. |
| `auth.py` | Return types are annotated. `_extract_bearer_token` is fully typed. However, `get_authenticated_agent(store=None)` and `get_registration_tenant(store=None)` have untyped `store` parameter. | Add `RegistryStore | None` type to the `store` parameter in both functions. |
| `main.py` | Most helper functions are fully typed (`_task_to_dict`, `_jsonrpc_success`, `_jsonrpc_error`, `_handle_send_message`, `_handle_get_task`, `_handle_cancel_task`, `_handle_list_tasks` all have param and return types). Gaps: `_cleanup_loop(redis, ...)` and `create_app(redis=None)` have untyped `redis` params. Inner closures `_get_store` and `_get_auth` lack return types. | Add `aioredis.Redis` type to the `redis` parameter in `_cleanup_loop` and `create_app`. Add return types to inner closures `_get_store` and `_get_auth`. |
| `api/__init__.py` | Empty file. | No changes. |
| `api/registry.py` | `get_registry_store` is typed. All 4 endpoint functions (`register_agent`, `list_agents`, `get_agent_detail`, `deregister_agent`) lack return type annotations. | Add return type annotations to all endpoint functions (e.g., `-> RegisterAgentResponse`, `-> ListAgentsResponse`, `-> JSONResponse | dict`, `-> Response | JSONResponse`). |

**client package** (4 files):

| File | Current State | Changes |
|------|---------------|---------|
| `__init__.py` | Empty file. | No changes. |
| `api.py` | Fully typed (all async functions have explicit param and return types). | No changes. |
| `cli.py` | `_run(coro)` and `_require_auth(ctx)` are untyped. Click-decorated functions are handled by the framework. | Add type annotations to `_run` (param: `Coroutine`, return: `Any`) and `_require_auth` (param: `click.Context`, return: `None`). |
| `output.py` | Most functions typed. `format_json(data)` has an untyped `data` parameter. All other `format_*` functions have param and return types. | Add `Any` type annotation to `format_json`'s `data` parameter. |

### CI Integration

Add ty to the existing `lint` job in `.github/workflows/ci.yml`:

```yaml
lint:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v6
    - uses: astral-sh/setup-uv@v7
      with:
        enable-cache: true
    - run: uv python install 3.13
    - run: uv sync
    - run: uv run ruff check .
    - run: uv run ty check    # <-- new step
```

ty runs from the project root and uses `[tool.ty.src]` include paths to find the source files. No `working-directory` override is needed.

### Dependency Addition

Add `ty` to the dev dependency group in the root `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "hikyaku-registry",
    "hikyaku-client",
    "fakeredis>=2.34.1",
    "pytest>=9.0.2",
    "pytest-asyncio>=1.3.0",
    "ruff>=0.11.0",
    "ty>=0.0.26",         # <-- new
]
```

---

## Implementation

> Task format: `- [x] Done task <!-- completed: 2026-02-13T14:30 -->`
> When completing a task, check the box and record the timestamp in the same edit.

### Step 1: Documentation

- [x] Update `CLAUDE.md` commands section to include `uv run ty check` <!-- completed: 2026-03-29T11:52 -->

### Step 2: Add ty dependency and configuration

- [x] Add `ty` to dev dependencies in root `pyproject.toml` <!-- completed: 2026-03-29T11:52 -->
- [x] Add `[tool.ty]` configuration to root `pyproject.toml` <!-- completed: 2026-03-29T11:52 -->
- [x] Run `uv sync` to verify dependency resolution <!-- completed: 2026-03-29T11:52 -->

### Step 3: Add type annotations to registry package

- [x] Add `TypedDict` classes to `registry_store.py` and refine `dict` return types on `create_agent`, `get_agent`, `list_active_agents` <!-- completed: 2026-03-29T12:05 -->
- [x] Add `Message` type to `message` param in `executor.py` (`_handle_unicast`, `_handle_broadcast`) <!-- completed: 2026-03-29T12:05 -->
- [x] Add `RegistryStore | None` type to `store` param in `auth.py` (`get_authenticated_agent`, `get_registration_tenant`) <!-- completed: 2026-03-29T12:05 -->
- [x] Add `aioredis.Redis` type to `redis` param in `main.py` (`_cleanup_loop`, `create_app`) and return types to inner closures <!-- completed: 2026-03-29T12:05 -->
- [x] Add return type annotations to all endpoint functions in `api/registry.py` <!-- completed: 2026-03-29T12:05 -->

### Step 4: Add type annotations to client package

- [x] Add annotations to `cli.py` (`_run`, `_require_auth`) and `output.py` (`format_json` data param) <!-- completed: 2026-03-29T12:05 -->

### Step 5: Run ty and fix errors

- [x] Run `uv run ty check` locally, fix any remaining errors, and tune `allowed-unresolved-imports` as needed <!-- completed: 2026-03-29T12:10 -->

### Step 6: CI integration

- [ ] Add `uv run ty check` step to the `lint` job in `.github/workflows/ci.yml` <!-- completed: -->

---

## Changelog

| Date | Changes |
|------|---------|
| 2026-03-29 | Initial draft |
