# Cloud sync protocol 1

The cloud API is opt-in and separate from the loopback API. Deployments belong to individual users. See [setup, trust boundaries, and operations](../cloud/README.md).

## Transport and discovery

Cloud origins require HTTPS with no userinfo, path prefix, query, or fragment. The CLI refuses redirects and proxy-environment overrides to avoid forwarding credentials elsewhere. `--allow-local` permits loopback HTTP only for development. `GET /v1/info` is public and returns `protocol`, implementation `version`, `vault_id`, `recommended_plan`, request-size limit, and features. Clients verify protocol and vault identity before every cycle.

Authenticated requests use `Authorization: Bearer TOKEN`. The credential supplies authorized spaces; a request cannot enlarge them. Error responses are JSON `{"error":"message"}` with HTTP 400 (invalid), 401 (credential), 403 (scope/origin), 409 (conflict/version/vault), 413 (size), 429 (rate), or 503 (unavailable/configuration). Clients persist controlled error codes rather than remote bodies. Network failure never acknowledges a local outbox item.

## Pairing and credentials

| POST endpoint | Request | Result |
| --- | --- | --- |
| `/v1/devices/connect` | `password`, `name`, `spaces` | New administrator device ID, token, exact spaces |
| `/v1/devices/invite` | Optional `spaces` subset | Random one-use code and ten-minute expiry |
| `/v1/devices/join` | `code`, `name`, optional `spaces` subset | New ordinary device ID/token/spaces; consumes invitation atomically |
| `/v1/devices/list` | `{}`; administrator device | IDs, names, spaces, roles, expiry; no token hashes |
| `/v1/devices/revoke` | `device_id`; self or administrator | Stops future access; does not delete remembered data |
| `/v1/devices/rotate` | `{}` | Replaces caller's token; old token stops working |

Passwords are checked with a constant-time digest comparison; device tokens and invitations are stored as SHA-256 hashes. Administrator pairing requires a secret of at least 32 characters configured by the account owner. Tokens do not inherit Cloudflare account permissions.

## Change envelope

`POST /v1/sync/push` accepts:

```json
{
  "protocol": 1,
  "changes": [
    {"table": "spaces", "row": {"name": "owner"}}
  ]
}
```

`GET /v1/sync/pull?since=0` returns the same `protocol` and `changes`, plus a nonnegative integer `cursor` and boolean `has_more`. `since` is a server cursor, not a timestamp or client sequence. Each client stores independent push and pull cursors. A changed vault identity requires deliberate reconnection and bootstrap.

Rows have exactly the columns of the version-one relational schema. Allowed tables: `spaces`, `evidence`, `memories`, `supports`, `relations`, `tombstones`. Credentials, extraction jobs, vectors, FTS, claims indexes, and local sync state never replicate. Envelope metadata may be extended; incompatible row/schema contracts require a protocol change.

A page contains up to 200 journal references plus their current dependencies: all supporting evidence and correction lineage needed to apply a memory. It does not expose historical row contents. The journal coalesces references by table/key and moves edited rows to a new sequence. Deleted row references disappear and permanent tombstone references remain. The complete envelope must fit 2 MB and 20,000 hydrated rows; a connected group larger than this is rejected explicitly rather than partially applied. Cursor progress and applying a received page commit together.

All push mutations execute in one durable SQL transaction, with no network I/O inside it. Local apply also executes in one SQLite transaction. Replaying identical rows is a no-op. Real derived changes are journaled, including local deletion closure; we do not globally suppress triggers during apply. A bounded echo can occur, then stabilizes. An acknowledgement only advances past rows sent from the snapshot; newer concurrent edits keep their newer sequence.

## Merge and deletion

- Tombstones form a union and always beat rows arriving later. Deleting evidence removes every memory using it, all that memory's supporting evidence, and the complete `supersedes` lineage, matching the local engine's privacy closure.
- Deletions discovered while applying a remote tombstone become new journal entries. A late support or supersedes edge touching a tombstone deletes its remaining endpoints and their closure. A contradiction edge alone does not cause unrelated deletion.
- Evidence identity is derived from space/source/source-event ID. New evidence IDs are deterministic from that identity. Legacy aliases map equivalent evidence to the existing row. Different fingerprints for one event are rejected. Content cannot be changed; null source text is monotone and never hydrated back from an older replica. Earliest recorded/occurred timestamps settle independently captured copies.
- Memories keep immutable content/space/kind/basis/outcome/key/start/recorded fields. Status uses the order `candidate < active < disputed < superseded`; revision takes the maximum; validity end takes the earliest non-null value. A lower-revision correction therefore cannot be undone by a higher-revision dispute. Distinct concurrent corrections retain their own IDs and the normal keyed-conflict retrieval behavior.
- Supports and relations are sets. Edges awaiting endpoints persist as ID-only pending rows, scoped to their owning memory's space. Endpoint IDs must have the correct `mem_`/`ev_` prefixes; relation kinds are `contradicts` or `supersedes`. Cross-space edges fail atomically. Unresolved edges are capped at 1,000 per space and 10,000 per database after resolving available endpoints. Exceeding either cap rejects the entire batch with HTTP 413; send missing endpoints first. Existing pending links are never silently expired. Derived claims and FTS stay local projections; imported evidence does not schedule extraction.
- Independent explicit saves may retain separate IDs and provenance. The protocol does not deduplicate separate source events by text or discard independent evidence.

Expiry masks source text immediately in inspect/export/sync responses and clears live content on maintenance. Expiry is not forgetting the derived claim. Forget is a separate confirmed operation. Cloudflare recovery history and external exports/wiki copies have separate retention.

## Local lifecycle

`cloud connect` enables additive sync tables/triggers without changing schema version 1 and bootstraps existing records in selected spaces. A database lease selects one sync owner across processes. Sync pulls first, pushes pending references, then pulls once more; newly discovered changes remain pending for another cycle. Leases renew between bounded HTTP requests and expire after 60 seconds. Background loops start only when configured, debounce startup, and back off to at most five minutes on failure. Short-lived hosts use the persistent `cloud run` command.

`cloud.json` is private, per data directory, and stores URL, exact spaces, device token, vault ID, interval, and development transport opt-in. Disconnect revokes the token and removes this file while retaining memory/deletion state. Offline disconnect explicitly skips server revocation. No default remote endpoint exists.

## Remote memory and MCP

Cloud `/v1/{operation}` uses the [existing operation contract](CONTRACT.md), except observe stores source evidence without extraction and process is unavailable. Forget requires a current preview token. `/mcp` implements stateless Streamable HTTP POST with JSON responses; notifications return 202 and SSE/session deletion return 405. All requests authenticate; tool errors retain the existing MCP structure. The cloud tools list excludes process and explains observe's capture-only behavior.

OAuth discovery follows MCP's protected-resource metadata convention. Supported flow: public clients, authorization code, S256 PKCE, explicit browser consent with a one-use request and HttpOnly SameSite cookie, exact registered redirect match, resource audience binding, one-use two-minute codes, one-hour access tokens, rotating 30-day refresh tokens. Redirects must be HTTPS or literal loopback HTTP. Client registration expires after one year. A used refresh token replay disables its grant's current credential. OAuth access tokens are only accepted at their `/mcp` audience, never device-management/sync endpoints.

OAuth payloads are limited to 16 KB of UTF-8 JSON. Registration accepts at most 10 redirect URIs, each at most 2,048 bytes without whitespace or control characters. Authorization state is at most 2,048 bytes. Oversized payloads return HTTP 413 before creating any registration or grant.

Optional reranking only changes order of already scoped keyword results when the deployment enables Workers AI and the HTTP request opts in. Default retrieval is exactly the canonical Python keyword engine. A failure falls back to the keyword result without modifying data.
