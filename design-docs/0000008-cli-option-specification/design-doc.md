# CLI & MCP Server Option Specification Refactoring

**Status**: Complete
**Progress**: 14/14 tasks complete
**Last Updated**: 2026-03-31

## Overview

Standardize how the Hikyaku CLI (`client/`) and MCP server (`mcp-server/`) accept configuration parameters. API keys must come from environment variables only (never CLI arguments), Agent IDs from CLI arguments only (never environment variables in CLI), and sensitive values must not appear in command output.

## Success Criteria

- [ ] API key is accepted only via `HIKYAKU_API_KEY` environment variable (CLI option `--api-key` removed)
- [ ] Broker URL is accepted only via `HIKYAKU_URL` environment variable (CLI option `--url` removed)
- [ ] Agent ID is accepted only via `--agent-id` subcommand option (environment variable `HIKYAKU_AGENT_ID` removed from CLI)
- [ ] API key is never displayed in any command output (human-readable or JSON)
- [ ] MCP server `register` tool no longer accepts `api_key` parameter (uses env var)
- [ ] MCP server `register` tool output excludes API key
- [ ] All existing tests pass after migration to new option sources
- [ ] Error messages accurately reflect the new configuration sources

---

## Background

The current CLI uses a mix of global click options and environment variable fallbacks for all configuration. This creates security concerns (API keys visible in shell history via `--api-key`) and inconsistency (`--agent-id` is a global option but not needed by `register`). The `register` command also displays the API key in its output, which is a security risk.

---

## Specification

### Option Source Matrix

After this refactoring, each parameter has exactly one input source per package:

| Parameter | CLI (`client/`) | MCP Server (`mcp-server/`) |
|-----------|----------------|---------------------------|
| API Key | `HIKYAKU_API_KEY` env var | `HIKYAKU_API_KEY` env var |
| Broker URL | `HIKYAKU_URL` env var (default: `http://localhost:8000`) | `HIKYAKU_URL` env var (required) |
| Agent ID | `--agent-id` subcommand option | `HIKYAKU_AGENT_ID` env var |
| JSON output | `--json` global flag | N/A |

Rationale:
- **API key env-only**: Prevents secrets from appearing in shell history or `ps` output.
- **URL env-only**: Infrastructure config that rarely changes between invocations. Default `http://localhost:8000` preserved for local dev.
- **Agent ID CLI-only (in CLI)**: Operational parameter that identifies *which* agent is acting. Makes it explicit per invocation. MCP server retains env var because it's a long-running daemon process.

### CLI Global Options (After)

The `cli` group retains only `--json`:

```python
@click.group()
@click.option("--json", "json_output", is_flag=True, default=False, help="Output in JSON format")
@click.pass_context
def cli(ctx, json_output):
    ctx.ensure_object(dict)
    url = os.environ.get("HIKYAKU_URL", "http://localhost:8000")
    api_key = os.environ.get("HIKYAKU_API_KEY")
    ctx.obj["url"] = url
    ctx.obj["api_key"] = api_key
    ctx.obj["json_output"] = json_output
```

### Subcommand `--agent-id` Option

All subcommands except `register` receive a `--agent-id` option (required, no env var):

```python
@cli.command()
@click.option("--agent-id", required=True, help="Agent ID")
@click.pass_context
def send(ctx, agent_id, ...):
    ...
```

Commands that require `--agent-id`: `send`, `broadcast`, `poll`, `ack`, `cancel`, `get-task`, `agents`, `deregister`.

Command that does NOT require `--agent-id`: `register`.

### Validation Changes

`_require_auth` is updated to validate only `api_key` (from env var). Each subcommand validates `agent_id` via click's `required=True`.

```python
def _require_api_key(ctx: click.Context) -> None:
    """Validate that HIKYAKU_API_KEY is set."""
    if not ctx.obj.get("api_key"):
        click.echo("Error: HIKYAKU_API_KEY environment variable is required", err=True)
        ctx.exit(1)
```

The `register` command keeps its own validation (API key required, no agent_id needed):

```python
@cli.command()
@click.option("--name", required=True, help="Agent name")
@click.option("--description", required=True, help="Agent description")
@click.option("--skills", default=None, help="Skills as JSON string")
@click.pass_context
def register(ctx, name, description, skills):
    api_key = ctx.obj.get("api_key")
    if not api_key:
        click.echo(
            "Error: HIKYAKU_API_KEY environment variable is required. "
            "Create an API key at the Hikyaku WebUI.",
            err=True,
        )
        ctx.exit(1)
        return
    ...
```

### Register Output Changes

**Human-readable output** (`output.py:format_register`):

Before:
```
Agent registered successfully!
  agent_id:  <id>
  api_key:   <key>
  name:      <name>

# Set these environment variables for subsequent commands:
export HIKYAKU_URL=${HIKYAKU_URL:-http://localhost:8000}
export HIKYAKU_API_KEY=<key>
export HIKYAKU_AGENT_ID=<id>
```

After:
```
Agent registered successfully!
  agent_id:  <id>
  name:      <name>

# Use this agent ID for subsequent commands:
export HIKYAKU_AGENT_ID=<id>
```

**JSON output**: Strip `api_key` field from the response dict before outputting.

```python
if ctx.obj["json_output"]:
    sanitized = {k: v for k, v in result.items() if k != "api_key"}
    click.echo(output.format_json(sanitized))
```

### MCP Server Changes

**`config.py`**: No changes needed. MCP server is a long-running daemon; all three env vars (`HIKYAKU_URL`, `HIKYAKU_API_KEY`, `HIKYAKU_AGENT_ID`) remain required.

**`register` tool**: Remove `api_key` parameter. Registration always uses the env var API key configured at startup.

Before:
```python
Tool(
    name="register",
    inputSchema={
        "properties": {
            "name": ...,
            "description": ...,
            "skills": ...,
            "api_key": {"type": "string", "description": "API key to join existing tenant"},
        },
        "required": ["name", "description"],
    },
)
```

After:
```python
Tool(
    name="register",
    inputSchema={
        "properties": {
            "name": ...,
            "description": ...,
            "skills": ...,
        },
        "required": ["name", "description"],
    },
)
```

**`RegistryForwarder.register`**: Remove `api_key` parameter. Always use `self._api_key` (from env var).

Before:
```python
async def register(self, *, name, description, skills=None, api_key=None):
    headers = {}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    ...
```

After:
```python
async def register(self, *, name, description, skills=None):
    headers = {"Authorization": f"Bearer {self._api_key}"}
    ...
```

**`register` tool output**: Strip `api_key` from the response before returning to the MCP client.

```python
elif name == "register":
    result = await handle_register(...)
    sanitized = {k: v for k, v in result.items() if k != "api_key"}
    return [TextContent(type="text", text=json.dumps(sanitized, default=str))]
```

### Error Messages

| Context | Before | After |
|---------|--------|-------|
| Missing API key (general) | `Error: --api-key is required (or set HIKYAKU_API_KEY)` | `Error: HIKYAKU_API_KEY environment variable is required` |
| Missing API key (register) | `Error: --api-key is required for registration. Create an API key at the Hikyaku WebUI.` | `Error: HIKYAKU_API_KEY environment variable is required. Create an API key at the Hikyaku WebUI.` |
| Missing agent ID | `Error: --agent-id is required (or set HIKYAKU_AGENT_ID)` | Click's built-in `Error: Missing option '--agent-id'.` (via `required=True`) |

---

## Implementation

> Task format: `- [x] Done task <!-- completed: 2026-02-13T14:30 -->`
> When completing a task, check the box and record the timestamp in the same edit.

### Step 1: Update Documentation

- [x] Update `ARCHITECTURE.md` with new option specification <!-- completed: 2026-03-31T10:00 -->
- [x] Update `docs/` with CLI usage changes (env var setup, removed options) <!-- completed: 2026-03-31T10:00 -->

### Step 2: Refactor CLI Global Options

- [x] Remove `--url`, `--api-key`, `--agent-id` from `cli()` group; read `url` and `api_key` from `os.environ` in `cli()`; keep only `--json` as a click option <!-- completed: 2026-03-31T11:48 -->
- [x] Rename `_require_auth` to `_require_api_key`; remove `agent_id` check (now handled by click `required=True` on each subcommand) <!-- completed: 2026-03-31T11:48 -->
- [x] Update `register` command error message to reference `HIKYAKU_API_KEY` env var <!-- completed: 2026-03-31T11:48 -->

### Step 3: Move `--agent-id` to Subcommands

- [x] Add `@click.option("--agent-id", required=True, help="Agent ID")` to each subcommand: `send`, `broadcast`, `poll`, `ack`, `cancel`, `get-task`, `agents`, `deregister` <!-- completed: 2026-03-31T11:48 -->
- [x] Update each subcommand to use the local `agent_id` parameter instead of `ctx.obj["agent_id"]` <!-- completed: 2026-03-31T11:48 -->

### Step 4: Sanitize Register Output

- [x] Update `output.format_register` to remove API key display and `export HIKYAKU_API_KEY` line; keep only `agent_id`, `name`, and `export HIKYAKU_AGENT_ID` <!-- completed: 2026-03-31T11:57 -->
- [x] Strip `api_key` from JSON output in `register` command before calling `format_json` <!-- completed: 2026-03-31T11:57 -->

### Step 5: Refactor MCP Server

- [x] Remove `api_key` parameter from `RegistryForwarder.register()`; always use `self._api_key` with `Authorization` header <!-- completed: 2026-03-31T11:57 -->
- [x] Remove `api_key` from `register` tool's `inputSchema` and `handle_register()` signature in `server.py` <!-- completed: 2026-03-31T11:57 -->
- [x] Strip `api_key` from `register` tool output in `call_tool()` <!-- completed: 2026-03-31T11:57 -->

### Step 6: Update Tests

- [x] Rewrite CLI tests in `tests/test_cli.py` and `tests/test_cli_register.py`: replace all `--api-key` and `--url` CLI args with `env={}` on `runner.invoke`; replace global `--agent-id` with subcommand-level `--agent-id`; update `_auth_opts()` helper in both files; add tests for missing env var errors; verify API key is absent from register output <!-- completed: 2026-03-31T11:57 -->
- [x] Rewrite MCP server tests in `tests/test_server.py`: remove `api_key` parameter from `handle_register` calls and `register` tool mock assertions; add test that register output excludes `api_key` <!-- completed: 2026-03-31T11:57 -->

---

## Changelog (optional -- spec revisions only, not implementation progress)

| Date | Changes |
|------|---------|
| 2026-03-30 | Initial draft |
| 2026-03-31 | Implementation complete |
