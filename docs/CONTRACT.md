# v1 interface contract

bigfeels exposes one scoped memory lifecycle through direct CLI, MCP stdio, and authenticated loopback HTTP. Native host adapters map onto the same core operations.

## Common rules

- Content and identifiers remain in the caller's allowed spaces.
- Responses are JSON objects. Direct CLI commands emit one compact JSON object per invocation.
- Memory is contextual evidence, never authorization.
- `remember` is synchronous. `observe` is durable queue ingestion, not completed extraction.
- Destructive deletion requires a fresh preview token whenever collateral records are involved. Public CLI, direct-local MCP, and UI flows always use preview plus token.
- v1 exports and schema version 1 remain the compatibility boundary.

HTTP operations use an `Authorization: Bearer <token>` header and `POST /v1/{operation}` with a JSON object. Static assets and `GET /health` do not require a credential and expose no stored data. Operation errors use 400 validation, 401 credential, 403 space, 404 missing, 409 stale revision/plan or identity conflict, and 413 request-size limits. Unexpected service failures use a content-free 500 response; the bundled client reports transport unavailability as 503.

## Operations

### remember

Input: `{space, content, kind?, basis?, evidence_ids?: [], key?, valid_from?, valid_until?, outcome?}`.

Kinds: `fact`, `preference`, `decision`, `episode`, `procedure`, `task`. Bases: `direct`, `observed`, `inferred`. Outcomes: `unspecified`, `proposed`, `attempted`, `attested`, `verified`, `failed`. New caller-supplied `verified` values are stored under the honest `attested` label; legacy values remain readable for v1 compatibility.

Returns the full memory record. A direct explicit save creates evidence when none is supplied. Arbitrary caller text is not independently verified merely because it references evidence.

### context and search

Input: `{query, spaces?: [], budget?: 800, as_of?: ISO}`.

Returns `{memories, tokens, status, trace}`. An empty space list means all spaces allowed to that principal. `search` also accepts `include_inactive: true`; `context` does not expose that switch.

### inspect

Input: `{id}`. Returns a scoped memory with provenance or an evidence record.

### correct

Input: `{id, revision, content, valid_from?}`. Atomically supersedes the current revision and returns the replacement. A stale revision returns 409.

### forget_preview

Input: `{id}`.

Returns `{id, plan_token, plan_expires_at, memories, counts: {memories, evidence}}`. Preview memories include at least `id` and `content` so a person or client can judge collateral deletion. The token binds the target, revisions, dependency membership, and issuance time, and expires after five minutes.

### forget

Input: `{id, plan_token}` on public CLI, MCP, native-tool, and UI surfaces. Executes only a current, unexpired, unchanged plan. Replayed deletion is harmless; stale or expired plans return 409 before deletion. The engine retains content-free replay tombstones after deleting content and derivatives.

### observe

Input: `{space, source, source_event_id, session_id, content, speaker, captured: true, origin_ids?, occurred_at?, expires_at?}`.

Returns `{id, status}`. `status: queued` means the source event committed to the queue. It does not mean extraction ran. Stable source identity deduplicates retries. Speakers are `user`, `assistant`, `tool`, and `document`.

### status

Input: `{}`. Returns scoped memory/evidence counts, `queue` state counts, provider configuration, schema version, and safe `processing` diagnostics. Standalone provider states are `configured`, `credential_missing`, or `not_configured`; `configured` does not claim that an endpoint is currently reachable. Processing diagnostics include `oldest_pending_at`, `next_retry_at`, and safe rejection/failure counters when supplied by the core. They never include source text or provider error bodies.

### process

Input: `{limit?: 8}`, where limit is 1 through 8. Returns `{processed, status}` on direct CLI/MCP and the standalone HTTP runtime. `processed` counts completed processing attempts according to the core contract. Zero is honest and may coexist with pending work when no extractor is configured, another worker owns processing, or retry timing has not arrived.

Direct-local MCP can invoke its configured extractor. HTTP-backed MCP forwards bounded processing and deletion preview through the authenticated loopback service.

### export

Input: `{}`. Returns the versioned scoped bundle, including evidence, memories, relationships, tombstones, and processing jobs, but no credentials. Restore is a local administrative CLI operation.

## MCP tool names

Direct-local MCP publishes `memory_observe`, `memory_remember`, `memory_context`, `memory_search`, `memory_inspect`, `memory_correct`, `memory_forget_preview`, `memory_forget`, `memory_process`, `memory_status`, and `memory_export`. The `memory_forget` handler requires a nonempty plan token even if an older core would permit a tokenless single-record deletion.

MCP cannot promise automatic conversation capture for a generic host. A native adapter must participate in that host's lifecycle, or the client must call `memory_observe` explicitly.

## Core Python boundary

`Store(path)` owns transaction and storage rules. `dispatch(principal, operation, payload)` handles scoped memory operations. `LocalClient` replaces network pairing with local filesystem authority while retaining an explicit principal and spaces. `process_pending(limit)` keeps its integer return contract; richer diagnostics live in `status.processing`.
