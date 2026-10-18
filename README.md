# bigfeels

Portable, private memory for tools and agents.

bigfeels is a host-neutral Python engine with these interfaces:

- `bigfeels-mem` for direct, JSON-producing local commands
- MCP over stdio for generic clients
- an authenticated loopback HTTP API
- a local browser workspace for save, search, inspection, correction, processing status, and deletion preview
- optional cloud sync and remote MCP deployed into your own Cloudflare account

Hermes and OpenClaw integrations are optional lifecycle adapters. The engine, CLI, MCP server, HTTP API, and UI do not require either host.

## Install from this checkout

Python 3.11+ with SQLite FTS5 is required. The Python runtime has no mandatory third-party dependencies.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install /absolute/path/to/bigfeels-mem
bigfeels-mem --help
```

The package has not been published to a registry. See [INSTALL.md](INSTALL.md) for isolated install options and optional adapters.

## Explicit lifecycle from the CLI

Every command prints one JSON object. Direct commands use the local store and an explicit space, with `owner` as the documented default.

```sh
bigfeels-mem remember "Use SQLite for the portable build." --space project:portable --kind decision
bigfeels-mem search "portable database" --space project:portable --budget 1600
bigfeels-mem context "What database did we choose?" --space project:portable
bigfeels-mem inspect MEMORY_ID --space project:portable
bigfeels-mem correct MEMORY_ID "Use SQLite with WAL." --revision REVISION --space project:portable
bigfeels-mem forget-preview MEMORY_ID --space project:portable
bigfeels-mem forget MEMORY_ID --plan-token PLAN_TOKEN --space project:portable
bigfeels-mem status --space project:portable
bigfeels-mem process --space project:portable --limit 8
```

Deletion is deliberately two-step. `forget-preview` returns every affected memory, evidence counts, and a five-minute plan token. `forget` requires that token and rejects stale, expired, or changed plans.

`remember` is synchronous. `process` handles queued observations only when an extraction provider is configured for the standalone engine. Its JSON response includes the processed count and current status. A zero count is not a claim that queued evidence became memory.

## MCP for any compatible client

Configure the MCP command as `bigfeels-mem` with arguments `mcp`. Use the executable's absolute path when the client has a different PATH. Add repeated `--space` arguments to constrain local access.

```json
{
  "command": "/absolute/path/to/bigfeels-mem",
  "args": ["--data-dir", "/private/path/bigfeels", "mcp", "--space", "owner"]
}
```

MCP exposes explicit remember, context, search, inspect, correct, deletion preview, token-confirmed deletion, status, export, observe, and bounded process tools in direct-local and authenticated loopback HTTP modes. Explicit saves and recall work without a model provider. `memory_observe` only queues source evidence. MCP cannot see an arbitrary host's conversation lifecycle, so it does not promise universal automatic capture. `memory_process` needs a configured extractor; inspect `memory_status` for pending, failed, retry, and provider state. See [docs/advanced.md](docs/advanced.md).

Agents recall best by searching for themselves: a few focused `memory_context` calls of 8,000–16,000 bytes each. On BEAM, that beat a single 96,000-byte context by 2.5 points at about two-thirds of the cost ([results](benchmarks/results/2026-10-08-beam-answers-agent.md)).

For a capture-free integration, add `--explicit-only` after `mcp`. That opt-in mode hides and rejects observe/process, rejects `--url`, and bypasses provider configuration so recall remains local. A host running an older Python can launch a separate Python 3.11+ stdio process instead of importing the engine. The bounded Scout deployment pattern, Windows paths-with-spaces JSON, customer isolation, migration boundary, and rollback procedure are in [docs/scout-pilot.md](docs/scout-pilot.md).

## HTTP API and browser workspace

The standalone service is optional. It binds only to loopback and requires a scoped bearer credential.

```sh
bigfeels-mem init
bigfeels-mem pair inspector --space owner
bigfeels-mem serve
```

Open `http://127.0.0.1:8765`, enter the paired credential, and use the local workspace. The credential stays in page memory. The HTTP API is `POST /v1/{operation}` with JSON bodies. It supports the same explicit lifecycle, including `forget_preview`, token-confirmed `forget`, `status`, and bounded `process` while the service worker is running.

See [docs/CONTRACT.md](docs/CONTRACT.md) and [docs/advanced.md](docs/advanced.md).

## Optional native adapters

- [Hermes adapter](adapters/hermes/README.md): native capture, recall, and host-owned extraction through the Hermes memory-provider lifecycle.
- [OpenClaw adapter](adapters/openclaw/README.md): native hooks, tools, and host-owned extraction through OpenClaw.

Adapters can provide automatic capture because they participate in a host's lifecycle. They remain adapters, not prerequisites for the core product. If host model access is unavailable, observations stay queued and explicit memory operations continue to work.

## Optional cloud sync in your own Cloudflare account

**We recommend the Cloudflare Workers Paid plan** when choosing cloud setup, so larger sync batches have more CPU headroom. You own the account, deployment, data, and billing; Bigfeels does not operate a cloud service for you. Local-only use remains the default.

The [cloud README](cloud/README.md) is the complete setup guide for **people and agents**: prerequisites, account verification, manual deployment, secret handling, explicit space selection, first sync, second-device invitations, background sync, remote MCP login, updates, backup/recovery, and troubleshooting.

After deploying the Worker using that guide:

```sh
bigfeels-mem --data-dir /private/bigfeels cloud connect https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev --name laptop --space owner
bigfeels-mem --data-dir /private/bigfeels cloud invite --space owner
bigfeels-mem --data-dir /private/bigfeels cloud run
```

On another device, run `cloud join URL --name desktop --space owner` and enter the one-time invitation at the private prompt. `sync` performs a single cycle; `cloud status` reports pending rows and sync health. Saves and searches stay local and work offline. Each user deploys one Worker with a durable SQLite database. The cloud runs the same Python memory/search engine.

Remote clients connect to `https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev/mcp` using browser OAuth login or a dedicated device bearer token. Local Hermes/OpenClaw adapters continue using their existing data directories; a persistent `cloud run` process handles short-lived adapter processes.

Generate an optional Obsidian projection locally:

```sh
bigfeels-mem --data-dir /private/bigfeels wiki export /path/to/ObsidianVault --space owner
```

Selected data is readable in your Cloudflare account. Cloud mode is not end-to-end encrypted. The wiki and exports are plaintext copies; their external history and Cloudflare recovery history have separate retention. See the [trust and deletion details](cloud/README.md#before-setup) before enabling sync.

## Data and trust boundary

Storage is local SQLite by default. Optional cloud sync copies explicitly selected spaces into the user’s own Cloudflare account. Memories retain source evidence, validity, revision history, and uncertainty. Retrieval is scoped before ranking. Remembered text is contextual evidence, never authorization.

Local scopes constrain integrations and model submissions, but they are not an OS sandbox against another process that can read the same database. Use separate private data directories or OS accounts for separate people. Exports are plaintext. `doctor` reports runtime and permission observations, but it cannot verify Windows ACLs, filesystem locality, synchronization, backups, or copies.

[Design](docs/design.md) · [Operations](docs/operations.md) · [Compatibility](docs/compatibility.md) · [Verification](docs/verification.md)

The product is **bigfeels**; the Python package and command are **`bigfeels-mem`**. Apache-2.0 local preview.
