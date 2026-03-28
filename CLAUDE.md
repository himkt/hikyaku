# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## A2A Protocol Reference

When working on this project, always reference the A2A protocol specification files:

- **Protobuf definition**: `A2A/specification/a2a.proto` — normative source for all protocol data objects and request/response messages
- **Full specification**: `A2A/docs/specification.md` — detailed technical specification including operations, data model, protocol bindings, and security
- **Agent discovery**: `A2A/docs/topics/agent-discovery.md` — discovery strategies (Well-Known URI, Registries, Direct Configuration)

These files are the authoritative reference. Always verify design decisions and implementations against them.

## Related Codebases

- `A2A/` — Google A2A protocol specification repository (reference only, do not modify)
- `solace-agent-mesh/` — Solace Agent Mesh framework (reference for related work comparison, do not modify)

## Project: Hikyaku

A2A-native message broker + agent registry for coding agents.

- **Design document**: `design-docs/a2a-registry-broker/design-doc.md` (Status: Approved)
- **Monorepo structure** (uv workspace):
  - `registry/` — `hikyaku-registry` (FastAPI + Redis + a2a-sdk)
  - `client/` — `hikyaku-client` (click + httpx + a2a-sdk)
- **CLI command**: `hikyaku`

## Tech Stack

- Python 3.12+ with uv workspace
- Server: FastAPI + Redis + a2a-sdk
- CLI: click + httpx + a2a-sdk

## Commands

**IMPORTANT**: Always `cd` into the package directory before running tests. Running from the project root causes module-not-found errors.

- Run registry tests: `cd registry` then `uv run pytest tests/ -v`
- Run client tests: `cd client` then `uv run pytest tests/ -v`
- Run specific test file: `cd registry` then `uv run pytest tests/test_executor.py -v`
- Start broker server: `cd registry` then `uv run uvicorn hikyaku_registry.main:app`
- Sync workspace: `uv sync` (from project root)
