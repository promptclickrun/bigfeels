# Operations

## Local storage and scopes

CLI and direct-local MCP use filesystem authority. Each invocation still creates a principal with explicit allowed spaces; `owner` is the documented default. HTTP uses paired bearer credentials with explicit space grants.

Scopes constrain reads, writes, ranking, and model submissions. They do not sandbox another process running as the same OS user with direct database access. Separate people or identities should use separate private data directories or OS accounts.

## Routine commands

```sh
bigfeels-mem status --space owner
bigfeels-mem doctor
bigfeels-mem search "query" --space owner
bigfeels-mem process --space owner --limit 8
bigfeels-mem maintenance
bigfeels-mem export --space owner --output memory-export.json
```

All direct lifecycle and diagnostic commands print JSON. `doctor` checks local storage/config without returning memory content. `status` is the source of truth for queue and provider state.

## Queue state

`observe` returns after source evidence and its job commit. Extraction may happen later. Preserve source event IDs across retries.

Inspect:

- `status.queue` for state-to-count mapping;
- `status.processing.oldest_pending_at` for backlog age;
- `status.processing.next_retry_at` for delayed work;
- safe rejection/failure counters for invalid or failed extraction;
- `status.providers` for extraction and embedding availability.

`process --limit N` attempts a bounded local batch. `processed: 0` can mean no extractor, no ready job, a busy worker, or retry delay. Do not report capture as learned memory until status/search/inspect proves the intended result.

## Deletion

Always preview first:

```sh
bigfeels-mem forget-preview MEMORY_ID --space owner
bigfeels-mem forget MEMORY_ID --plan-token PLAN_TOKEN --space owner
```

Review every memory and evidence count in the preview. If the closure changes, the plan token is rejected with 409 and a new preview is required. Logical deletion commits transactionally. Maintenance handles physical FTS/database cleanup. Tombstones retain content-free identities to block replay.

Deletion cannot recall data already sent to a provider, retained in host histories, copied into exports, present in OS snapshots, or held by another process. Manage those systems separately.

## Backup and restore

Exports are plaintext and should be stored privately. They preserve v1 evidence, memories, relationships, tombstones, and processing jobs, but not HTTP credentials or derived vector indexes. Restore only into an empty store:

```sh
bigfeels-mem --data-dir /private/new-store init
bigfeels-mem --data-dir /private/new-store restore memory-export.json
```

## Service operation

`serve` binds to loopback only and starts the optional background processor. It does not install a daemon. Configuration changes require restarting that explicitly managed process. The browser UI is optional and static assets reveal no memory without a scoped credential.

No command in this guide installs a host plugin, changes a live profile, starts an OS service, or publishes a package.
