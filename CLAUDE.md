# mu-sdk-python

**Open (Apache-2.0).** The Product-B **developer SDK for Python** — the library backend and
agentic-framework developers (LangGraph, CrewAI, custom agents) import to use Memory Universe. See
`../CLAUDE.md` for project-wide rules.

## What it is — a wire client, nothing else

- **No engine, no stores, no strategies, no embedder.** It only speaks the versioned wire contract.
- Depends **only on the published `mu-contracts`** (pinned version) — never on `mu-engine` or
  `mu-server` internals. Contract drift is caught by the conformance suite.
- Toolchain: `uv` + `pytest`, published to **PyPI**.

## Where it connects (the request surface)

Talks to **`mu-server`'s public surface, through the gateway edge** — never to the engine directly:
- **REST** (Streamable-HTTP) — memories, sessions, context/recall, rooms, devices, sync, persona,
  conflict inbox, subscriptions.
- **MCP** — the same operations exposed as agent tools (so an agent framework calls them as tools).
- **Centrifugo (SSE / streaming)** — live push: `SyncStatusView`, room events, conflict-found +
  other notifications.
- Auth via bearer / device token; every call is namespace-scoped (Product B = namespace-per-end-user,
  metered per-MAU).

## Features it exposes

Memory ops (add / get / search / recall / update — invalidate-don't-delete) · live session + context
(compose/retrieve, lean context) · rooms (live human+agent, and local agent-to-agent) · device
enroll + sync-status · persona · **agent + subagent identity registration** (auto `checkpoint_ns`
for LangGraph nodes) · conflict inbox (auto/manual) · trust surfaces + notifications · governed
subscriptions.

> Detailed surface is designed in `../docs/superpowers/design/api-sdk-mcp-surface-design.md`; keep
> this SDK a faithful typed mirror of it.
