# Compatibility

## Portable engine

The primary package requires Python 3.11+ and SQLite FTS5. It has no mandatory third-party runtime dependencies. The supported first-class surfaces are:

- direct local JSON CLI;
- MCP 2025-06-18 over stdio;
- v1 authenticated loopback HTTP;
- the bundled same-origin browser workspace;
- v1 export/restore formats.

Core memory behavior does not require Node, Hermes, or OpenClaw. Node 22.19+ is used only by the optional OpenClaw package and its tests.

## MCP capability boundary

Generic clients can complete the explicit lifecycle without a native host: remember, search/context, inspect, correct, preview deletion, token-confirmed deletion, status, export, and direct-local bounded processing. `observe` is explicit queue ingestion. Automatic capture requires lifecycle events that generic MCP does not provide.

## Optional Hermes adapter

The adapter targets Hermes Agent's `MemoryProvider` lifecycle, provider registration, auxiliary model routing, session identity, and background sync contracts. The last recorded source-compatibility reference is Hermes commit `44ddc552f5e054759a6970af8997ea588a9d81c9`.

Hermes owns model selection, subscription authentication, credential refresh, and host policy. The adapter is not required for CLI, MCP, HTTP, or UI use.

## Optional OpenClaw adapter

The adapter uses `definePluginEntry`, lifecycle hooks, registered tools, and OpenClaw's host completion runtime. The declared minimum host/plugin API is `2026.6.11`; the last recorded source-compatibility reference is commit `826a97e7d79521341abbddf9c619787fef6b090d`.

OpenClaw is an optional npm peer dependency. The Python engine can be installed and used without it.

## Shared adapter behavior

Native adapters can add automatic capture and recall because the host supplies stable lifecycle identities. They use explicit owner/project spaces and exclude their own memory-tool echoes from fresh capture. Host completion APIs own credentials; bigfeels does not copy them.

Compatibility checks establish source contracts and fixture behavior, not live host activation, entitlement, model quality, gateway lifecycle, or future host-version support. Read [verification.md](verification.md) for the tested boundary.
