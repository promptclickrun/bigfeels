# v1 contract

All calls except the public static UI require `Authorization: Bearer TOKEN`.
POST `/v1/{operation}` accepts an object and returns JSON. Errors: 400 validation,
401 credential, 403 space, 404 missing, 409 revision/idempotency, 503 provider.
GET `/health` exposes only version/liveness, no stored data.

Operations (also MCP tools named `memory_{operation}`):

* observe: `{space, source, source_event_id, session_id, content, speaker,
  captured: true, origin_ids?: [evidence_id], occurred_at?: ISO, expires_at?: ISO}`.
  Returns `{id, status}`. captured false returns skipped; source event identity
  deduplicates in space. Content redacted before persistence. Speakers user,
  assistant, tool, document. Mirrored memory must supply origin_ids.
* remember: `{space, content, kind?, basis?, evidence_ids?: [], key?,
  valid_from?, valid_until?, outcome?}` returns full record.
  kind fact/preference/decision/episode/procedure/task. basis direct/observed/inferred.
  outcome unspecified/proposed/attempted/verified/failed. No outcome inferred by
  caller omission. Direct explicit saves create evidence when none supplied.
* context / search: `{query, spaces?: [], budget?: 800, as_of?: ISO}` returns
  `{memories: [], tokens, status, trace: {..}}`. Memory includes evidence IDs,
  source availability, status, reason. Empty spaces means all allowed spaces.
  Search additionally accepts `include_inactive: true` for inspection of candidates,
  expired and superseded records; context never includes them via this flag.
* inspect: `{id}` returns memory plus evidence, or evidence item; checks access.
* correct: `{id, revision, content, valid_from?}` atomically supersedes record;
  returns replacement. Revision is required. Previous evidence retained.
* forget: `{id}` deletes content and derivatives, retains content-free source
  identity markers. Returns counts. No other space can depend on private evidence.
* status: `{}` returns scoped counts, queue status, provider availability.
* export: `{}` returns versioned JSON with allowed spaces, evidence, memories,
  relationships, tombstones, processing jobs; excludes credentials. Restore via local admin CLI.

Core Python `Store(path)` owns connections per operation. `pair(name, spaces)`
returns random credential; `authenticate(token)` returns Principal(name, spaces).
`dispatch(principal, operation, payload)` provides all operations above.
`create_space(name)`, `restore(bundle)`, `maintenance()`, `process_one(extractor,
embedder=None)`, `rebuild_embeddings(embedder)` are local administrative operations.
`dispatch` raises MemoryError subclasses with `status` and safe message.
Worker extractor.extract(evidence_dict) returns list of candidates with content,
kind, basis, key, outcome. Only inferred promotion is automatic (candidate until
supported quote verification); extracted direct/observed items require an exact
`quote` substring from evidence and compatible speaker. Promotion preserves the
entire bounded evidence content so a substring cannot remove negation or context.
Longer synthesis stays candidate; automatic `verified` outcome labels become
unspecified because a model label is not verification. Every candidate is tied
to original evidence. Explicit verified saves require observed basis and tool evidence.

Service and MCP must not log request bodies or credentials. UI uses session-only
in-memory credential, safe text rendering, no external assets, origin checks,
loopback only. Service starts background processor only when configured.
