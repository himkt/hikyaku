# mise Task Full-Path Specification

**Status**: Approved
**Progress**: 7/7 tasks complete
**Last Updated**: 2026-03-30

## Overview

Switch all mise task invocations from short-form notation (`mise test`) to full-path notation (`mise //client:test`) across `.claude/settings.json`, `.claude/rules/commands.md`, and both `CLAUDE.md` files. This eliminates ambiguity in monorepo task resolution and enables explicit per-package task permissions.

## Success Criteria

- [ ] All `mise tasks --all` tasks (including mcp-server) are callable via full-path notation from the project root
- [ ] `.claude/settings.json` allows only full-path task invocations and denies short-form
- [ ] `.claude/rules/commands.md` documents full-path notation exclusively
- [ ] Both `CLAUDE.md` files reference full-path notation in their Commands sections
- [ ] No `cd` into package directories is required for running mise tasks

---

## Background

The project uses mise as a monorepo task runner with packages: `registry/`, `client/`, `mcp-server/`, and `admin/`. Currently, `.claude/settings.json` allows short-form task names (`mise test`, `mise lint`), and `.claude/rules/commands.md` instructs agents to `cd` into package directories before running tasks. This is error-prone because short-form names are ambiguous when multiple packages define the same task name (e.g., `test` exists in registry, client, and mcp-server).

Full-path notation (`mise //client:test`) resolves tasks unambiguously from the project root, removing the need to `cd` first.

---

## Specification

### Full-Path Notation

mise monorepo full-path format: `//[config_root]:[task_name]`

| Notation | Meaning |
|----------|---------|
| `//:lint` | Root-level `lint` task (defined in `mise.toml`) |
| `//client:test` | `test` task in `client/mise.toml` |
| `//registry:dev` | `dev` task in `registry/mise.toml` |

### Task Inventory

After adding `mcp-server` to `monorepo.config_roots`, the full task list:

| Full-Path | Package | Description |
|-----------|---------|-------------|
| `//:format` | root | Check code formatting with ruff |
| `//:lint` | root | Run ruff linter |
| `//:typecheck` | root | Run ty type checker |
| `//admin:build` | admin | Build admin app |
| `//admin:dev` | admin | Run admin dev server |
| `//admin:lint` | admin | Lint admin app |
| `//client:format` | client | Format client code |
| `//client:lint` | client | Lint client code |
| `//client:test` | client | Run client tests |
| `//mcp-server:format` | mcp-server | Format mcp-server code |
| `//mcp-server:lint` | mcp-server | Lint mcp-server code |
| `//mcp-server:dev` | mcp-server | Run mcp-server dev |
| `//mcp-server:test` | mcp-server | Run mcp-server tests |
| `//registry:dev` | registry | Run registry dev server |
| `//registry:format` | registry | Format registry code |
| `//registry:lint` | registry | Lint registry code |
| `//registry:test` | registry | Run registry tests |

### settings.json Design

**Allow list** — root tasks are individually listed, sub-packages use wildcards:

```json
{
  "permissions": {
    "allow": [
      "Bash(mise //:lint)",
      "Bash(mise //:lint *)",
      "Bash(mise //:format)",
      "Bash(mise //:format *)",
      "Bash(mise //:typecheck)",
      "Bash(mise //:typecheck *)",
      "Bash(mise //admin:*)",
      "Bash(mise //admin:* *)",
      "Bash(mise //client:*)",
      "Bash(mise //client:* *)",
      "Bash(mise //mcp-server:*)",
      "Bash(mise //mcp-server:* *)",
      "Bash(mise //registry:*)",
      "Bash(mise //registry:* *)"
    ],
    "deny": [
      "Bash(uv run pytest *)",
      "Bash(uv run python -m *)",
      "Bash(uv run --package *)",
      "Bash(mise run *)",
      "Bash(mise lint)",
      "Bash(mise lint *)",
      "Bash(mise format)",
      "Bash(mise format *)",
      "Bash(mise typecheck)",
      "Bash(mise typecheck *)",
      "Bash(mise test)",
      "Bash(mise test *)",
      "Bash(mise dev)",
      "Bash(mise dev *)",
      "Bash(mise build)",
      "Bash(mise build *)"
    ]
  }
}
```

Rationale:
- Root tasks (`//:lint`, `//:format`, `//:typecheck`) are individually listed — the root prefix `//:` is visually ambiguous with a "match-all" pattern when combined with `*`, so explicit enumeration avoids confusion
- Sub-package wildcards (`//client:*`) auto-permit new tasks added to a package without settings.json updates
- Argument patterns (`//client:* *`) allow passing flags (e.g., `mise //client:test -k test_name`)
- Short-form names (`mise test`, `mise lint`, `mise dev`, `mise build`, etc.) are explicitly denied to enforce full-path usage
- `mise run *` deny is maintained to prevent the redundant `run` subcommand

### commands.md Update

Replace all short-form commands with full-path notation. Remove instructions to `cd` into package directories since full-path tasks execute in the correct working directory automatically.

New content:

```markdown
# Commands

**IMPORTANT**: Always use mise full-path tasks. Run from the project root — do NOT `cd` into package directories.

- Run registry tests: `mise //registry:test`
- Run client tests: `mise //client:test`
- Run MCP server tests: `mise //mcp-server:test`
- Lint (root): `mise //:lint`
- Lint (registry): `mise //registry:lint`
- Lint (client): `mise //client:lint`
- Lint (admin): `mise //admin:lint`
- Lint (mcp-server): `mise //mcp-server:lint`
- Format check (root): `mise //:format`
- Type check: `mise //:typecheck`
- Sync workspace: `uv sync` (from project root)
- Start broker server: `mise //registry:dev`
- Start MCP server: `mise //mcp-server:dev`
- Start admin dev server: `mise //admin:dev`
- Build admin: `mise //admin:build`

## mise Tasks

- Use full-path notation: `mise //[package]:[task]`. Do NOT use short-form `mise <task>`.
- Do NOT use `mise run <task>` — the `run` subcommand is unnecessary.
- Run all tasks from the project root. No `cd` required.
```

### CLAUDE.md Updates

Both `CLAUDE.md` (root) and `.claude/CLAUDE.md` Commands sections will be updated to match the full-path notation documented in `commands.md`.

Key changes for root `CLAUDE.md`:
- Replace `uv run pytest tests/ -v` commands with `mise //registry:test` etc.
- Replace `uv run ty check` with `mise //:typecheck`
- Remove instructions to `cd` into package directories for mise tasks

Key changes for `.claude/CLAUDE.md`:
- Replace `mise test`, `mise lint` etc. with full-path equivalents
- Remove instructions to `cd` into package directories for mise tasks
- Reference `mise //registry:dev` instead of `uv run uvicorn`
- Reference `mise //mcp-server:dev` instead of `uv run hikyaku-mcp`

---

## Implementation

> Task format: `- [x] Done task <!-- completed: 2026-02-13T14:30 -->`
> When completing a task, check the box and record the timestamp in the same edit.

### Step 1: Update mise.toml monorepo config

- [x] Add `mcp-server` to `monorepo.config_roots` in root `mise.toml` <!-- completed: 2026-03-31T14:39 -->
- [x] Verify `mise tasks --all` shows mcp-server tasks (`//mcp-server:test`, etc.) <!-- completed: 2026-03-31T14:39 -->

> After verification, proceed to Steps 3-4 (documentation updates) before Step 2 (settings.json), following the project rule that documentation is updated first.

> Note: This change does not affect `ARCHITECTURE.md` or `README.md` — it is a developer tooling configuration change, not an architectural or user-facing change.

### Step 2: Update .claude/settings.json

- [x] Replace allow list with package-level full-path wildcard patterns <!-- completed: 2026-03-31T14:52 -->
- [x] Add short-form task names (`mise test`, `mise lint`, etc.) to deny list <!-- completed: 2026-03-31T14:52 -->

### Step 3: Update .claude/rules/commands.md

- [x] Rewrite commands.md with full-path notation and remove `cd` instructions <!-- completed: 2026-03-31T14:40 -->

### Step 4: Update CLAUDE.md files

- [x] Update root `CLAUDE.md` Commands section to use full-path notation <!-- completed: 2026-03-31T14:48 -->
- [x] Update `.claude/CLAUDE.md` Commands section to use full-path notation <!-- completed: 2026-03-31T14:48 -->

---

## Changelog

| Date | Changes |
|------|---------|
| 2026-03-30 | Initial draft |
| 2026-03-30 | Add `dev`/`build` short-form to deny list; add implementation order notes |
| 2026-03-30 | Enumerate root tasks individually instead of `//:*` wildcard |
| 2026-03-30 | Approved |
