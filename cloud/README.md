# Your own Bigfeels cloud

Deploy Bigfeels into **your Cloudflare account**. There is no Bigfeels-hosted service, shared database, subscription, or central credential server. You own the Worker, durable database, access credentials, billing, and updates. Your computers keep local SQLite stores for fast search and offline writes.

**We recommend Cloudflare Workers Paid for every cloud setup**, particularly for larger initial uploads and sync batches. It provides more CPU headroom than Workers Free. The base subscription is currently $5/month, with usage charges possible; check [current pricing](https://developers.cloudflare.com/workers/platform/pricing/) and [CPU limits](https://developers.cloudflare.com/workers/platform/limits/) before subscribing. Bigfeels does not change your billing plan. Paid does not remove Bigfeels' request-size limits.

## What this includes

- One Python Worker and one SQLite-backed Durable Object per deployment. No D1, R2, KV, external model provider, or Node installation is needed on client devices.
- The same Python memory engine for local and cloud search, correction, deletion, and merge. Keyword search remains the default; no embeddings or Vectorize.
- Per-device credentials, ten-minute one-time invitations, rotation, revocation, and explicit space selection.
- Offline-safe sync with persisted cursors, deletion markers, dependency closure, retry, and a single sync lease per database.
- Authenticated `/v1/` HTTP operations and remote `/mcp` with bearer credentials or OAuth browser login using PKCE.
- Local Obsidian-compatible wiki export, including memories without keys.
- Optional Workers AI reranking, disabled unless you enable its binding and request it.

Cloud capture stores source evidence but does **not** run an extraction model. A remote agent can call `memory_observe`, then `memory_remember` with the evidence ID. Existing local extraction stays on the originating device; imported evidence does not create duplicate extraction jobs.

## Before setup

Choose the Cloudflare account, a unique Worker name, the local Bigfeels data directory on each computer, and the spaces allowed in the cloud. Default: `owner` only. Use a separate deployment for another person. This is a personal vault, not a multi-user sharing service.

Selected evidence and memories are **readable by your Cloudflare account and Cloudflare's infrastructure**. This is not end-to-end encrypted sync. Device credentials are stored as hashes on the server. Your local `cloud.json` contains your device token and is written with owner-only permissions on POSIX systems; secure its ACL on Windows. The administrator password is used for pairing/browser consent and is not saved on a client device.

The deployment machine needs Git, Node 22+, Python 3.13+, and [uv](https://docs.astral.sh/uv/getting-started/installation/). Local memory clients need only Python 3.11+ with SQLite FTS5. Cloudflare's [Python deployment tooling](https://developers.cloudflare.com/workers/languages/python/packages/) is isolated in `cloud/`.

## Manual deployment

1. Clone or fork the **whole repository**. The build copies the current engine from `src/bigfeels_mem`; copying only `cloud/` leaves out the shared engine.

   ```sh
   git clone https://github.com/promptclickrun/bigfeels.git
   cd bigfeels
   python3 cloud/build.py
   cd cloud
   npm ci
   uv sync --locked
   uv run --locked pywrangler login
   uv run --locked pywrangler whoami
   ```

2. Verify that `whoami` identifies your intended account. Set `account_id` in `cloud/wrangler.jsonc` if you have multiple accounts. Change `name` to a unique Worker name before the first deployment. Keep that name and the `MemoryVault` class stable during updates, because renaming can create a different database.

3. In `cloud/wrangler.jsonc`, set `vars.SYNC_SPACES` to the comma-separated names you want to allow, such as `owner,project:personal`. Spaces containing commas are not supported in this configuration. Leave `ALLOWED_ORIGINS` empty unless a browser-based client needs cross-origin access; then list exact trusted HTTPS origins separated by commas. CLI and native MCP clients do not need CORS configuration.

4. Deploy and set a unique, random administrator password of at least 32 characters. Generate it in your password manager. The secret command prompts privately; do not put the password in a command argument, commit, chat, or deployment log.

   ```sh
   uv run --locked pywrangler deploy --dry-run
   uv run --locked pywrangler deploy
   uv run --locked pywrangler secret put ADMIN_PASSWORD
   ```

   The first deployment creates the Durable Object namespace. Tables initialize automatically; no manual SQL migration command is needed. Pairing refuses to work until the password is configured. Keep the generated `https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev` URL.

5. Check the public, content-free version endpoint:

   ```sh
   curl --fail https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev/v1/info
   ```

   Expect `protocol: 1`, a `vault_id`, and `recommended_plan: "Workers Paid"`. Never publish a local database to test deployment.

For Git-based automatic deployment, import your fork into Workers Builds. Use the repository root, build command `python3 cloud/build.py && npm ci --prefix cloud`, and deploy command `cd cloud && uv run --locked pywrangler deploy`. Ensure Python 3.13+, Node 22+, and uv are available in the build image. Keep `ADMIN_PASSWORD` as a Worker secret. The shared engine requires the full repository; this is not a standalone subfolder template.

## Connect the first computer

Install the CLI from the same checkout, using a separate virtual environment:

```sh
cd /path/to/bigfeels
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
bigfeels-mem --data-dir /private/bigfeels cloud connect https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev --name laptop --space owner
```

Enter the administrator password at the private prompt. The CLI recommends Workers Paid and discloses readable cloud storage before pairing. It saves a device token, enables change tracking, uploads existing data from selected spaces, and pulls cloud changes. This uses an additive schema extension; the existing local database/export version remains 1.

Pairing remains saved if the first sync is interrupted. Resume with:

```sh
bigfeels-mem --data-dir /private/bigfeels sync
bigfeels-mem --data-dir /private/bigfeels cloud status
```

Use your existing adapter's **actual data directory**, not a new default directory. Hermes and OpenClaw can have profile-specific paths. Back up that directory before an upgrade; do not replay an older migration over corrections or deletions.

## Add another computer

On a connected computer:

```sh
bigfeels-mem --data-dir /private/bigfeels cloud invite --space owner
```

The JSON result contains a secret invitation code. It works once and expires after ten minutes. Transfer it privately. On the other computer:

```sh
bigfeels-mem --data-dir /private/bigfeels cloud join https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev --name desktop --space owner
```

Enter the invitation at the prompt. A joined device cannot gain spaces beyond its invitation. Use a new pairing to change scopes; a token cannot enlarge its own scope. An administrator-paired device can list/revoke devices; joined devices can revoke themselves and invite devices with equal or narrower access.

## Agent-led setup

An agent follows the same path as a person. It should establish the account, Worker name, intended local directories, selected spaces, and the user's billing choice before deploying. Recommend **Workers Paid** explicitly. Do not select the first account returned by `whoami` or upload all spaces by default.

1. Inspect the checkout and `cloud/wrangler.jsonc`; preserve existing unrelated settings and secrets.
2. Verify prerequisites, account identity, and the paid-plan recommendation. Run the dry run and the isolated runtime test below.
3. Use the authorized Cloudflare account and a narrowly scoped deployment API token or Wrangler login. Cloudflare API credentials authorize deployment only; they are not Bigfeels device tokens.
4. Set `ADMIN_PASSWORD` through Wrangler's private prompt or protected stdin. Do not print it. Cloudflare API tokens should be supplied through the deployment environment, never committed.
5. Check `/v1/info`, then pair the specific local store and chosen spaces. For noninteractive pairing, put the password or invite in a temporary environment variable and use `--secret-env BIGFEELS_PAIRING_SECRET`. Remove it immediately afterward. No secret is accepted as a positional CLI argument.
6. Sync two disposable test records across two devices, verify cloud/local search and deletion, then remove the test records through `forget-preview`/`forget` and sync again. Inspect `cloud status`; success requires no error, a recent sync, and an empty outbox after another cycle if necessary.
7. Report the Worker URL, selected spaces, running sync owner, and validation performed. Do not include tokens, administrator passwords, private exports, or memory content in the handoff.

All noninteractive setup commands return JSON. `cloud connect` and `cloud join` print pairing and initial-sync results as separate JSON lines. Warnings go to stderr so they cannot corrupt stdio MCP.

## Keep sync running

Long-lived `mcp`, local `serve`, and native Python `LocalClient` sessions start background sync when `cloud.json` exists. SQLite leases keep multiple clients from syncing at the same time. Local writes only touch SQLite; they never wait for a cloud request. Sync starts on launch, wakes after local writes, polls every 30 seconds by default, and backs off after failures.

Short-lived CLI and plugin subprocesses need a persistent sync owner. The reliable cross-platform option is:

```sh
bigfeels-mem --data-dir /private/bigfeels cloud run
```

Keep this running in a terminal, or configure it as a user-level launchd, systemd, or Windows scheduled task. Use the absolute CLI executable path and the same `--data-dir` as the adapter. Do not start a system-wide service that can read unrelated users' databases. Closing the process stops syncing; all pending writes remain on disk.

`mcp --explicit-only` does not start cloud sync. If another process syncs that same database, its changes can still appear there; use a separate directory for a truly isolated local store.

Status commands:

```sh
bigfeels-mem --data-dir /private/bigfeels cloud status
bigfeels-mem --data-dir /private/bigfeels status --space owner
```

Status exposes counts and controlled error codes, not credentials or remembered text. `busy` means another process holds the sync lease. After an interrupted process, the lease expires within 60 seconds. A failed upload remains pending. Cloud outages do not prevent local recall or saves.

## Remote MCP and HTTP

Configure a compatible remote MCP client with:

```text
https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev/mcp
```

For clients supporting OAuth, connect in the client's UI. The browser page shows the requesting app, callback URL, and spaces. Enter your administrator password **only on your own Worker origin**, choose spaces, and approve. The service supports discovery, dynamic public-client registration, authorization code with S256 PKCE, one-use codes, one-hour access tokens, and rotating refresh tokens valid for 30 days. Revoking the device also disables refresh. Reusing a consumed refresh token revokes that grant's active credential.

For clients supporting custom headers, use `Authorization: Bearer DEVICE_TOKEN`. Create a dedicated device with an invitation and protect its token like a password. Tokens belong in the client's secret configuration, not a checked-in MCP file. OAuth tokens are audience-bound to `/mcp` and cannot call device-management or sync endpoints.

The MCP transport is stateless Streamable HTTP, with JSON responses to POST. Notifications return 202; GET/SSE and session deletion return 405. Every call authenticates independently. It exposes remember, observe, search, context, inspect, correct, deletion preview, forget, status, and export. `memory_process` is not advertised because there is no cloud extractor. Exact compatibility with every third-party client is not implied by transport tests.

The cloud also supports `POST /v1/{operation}` using the existing [operation contract](../docs/CONTRACT.md). The same scoped Python engine implements both transports. The original loopback HTTP client's restrictions remain in place; use the explicit cloud configuration for remote access.

### Optional AI reranking

For clients without an agent search loop, optionally add `"ai": {"binding": "AI"}` to `cloud/wrangler.jsonc` and set `vars.RERANK_ENABLED` to `"true"`, then redeploy. Send `"rerank": true` on `/v1/search` or `/v1/context`. The top 20 already-authorized keyword hits are submitted to [Cloudflare's reranker](https://developers.cloudflare.com/workers-ai/models/bge-reranker-base/), limited to 2,000 characters per hit. It only reorders these hits; it does not create memories or embeddings. Errors/timeouts preserve keyword order and report `unavailable_keyword_fallback`. This incurs Workers AI usage and sends selected text to that service. No AI call happens in the default configuration. Agent-driven keyword search remains the recommended default.

## Obsidian wiki

```sh
bigfeels-mem --data-dir /private/bigfeels wiki export /path/to/ObsidianVault --space owner
```

This writes a managed `Bigfeels/` folder with an index, topic pages grouped by space/kind/key, an `Unkeyed` grouping, source links, memory IDs, revisions, statuses, and validity windows. Evidence pages omit source text by default; add `--include-evidence` to include available source text. No cloud connection is required.

Run export again after sync to update the wiki and remove pages for deleted records. A manifest tracks only generated files; unrelated files are not removed and manually changed generated files cause a refusal. Resolve those edits before rerunning. Wiki edits do not write back to memory. The exporter never starts git, Obsidian Sync, or rclone for you.

The wiki is a **plaintext copy**. Git history, Obsidian Sync history, exports, screenshots, and backups are outside Bigfeels' deletion control. Removing a record from memory does not erase those histories.

## Device management and disconnect

```sh
bigfeels-mem --data-dir /private/bigfeels cloud devices
bigfeels-mem --data-dir /private/bigfeels cloud revoke DEVICE_ID
bigfeels-mem --data-dir /private/bigfeels cloud rotate
bigfeels-mem --data-dir /private/bigfeels cloud disconnect
```

Rotation updates the local credential file. Revocation stops future access but cannot erase a device's existing local copy. Disconnect revokes the current token and removes `cloud.json`, preserving local memory and deletion history. When offline, `cloud disconnect --offline` removes local credentials only; revoke the old device from an administrator device later. Stopping sync does not delete cloud data.

To change spaces, disconnect and reconnect with explicit spaces. Existing cloud data remains until deliberately forgotten. Revocation, removal from `SYNC_SPACES`, and deletion are different operations.

## Updates, backups, and recovery

Update your checkout/fork, reinstall the Python CLI, rebuild the cloud bundle, run tests, and redeploy. Preserve your account ID, Worker name, secret, spaces, and Durable Object binding/migration history. Review changes to those files before accepting an upstream update. Protocol 1 is checked before every sync cycle; incompatible Workers fail clearly instead of guessing how to merge.

```sh
python3 cloud/build.py
cd cloud
npm ci
uv sync --locked
uv run --locked pywrangler deploy --dry-run
uv run --locked pywrangler deploy
```

Use `bigfeels-mem export --output /private/backup.json --space owner` for a portable local backup. Restore into a **new empty data directory** with `bigfeels-mem --data-dir /private/recovery restore /private/backup.json`, inspect it, and only then connect that store. Exports are plaintext and contain tombstones, which must survive recovery. Never edit out deletion markers to make an import succeed.

Cloudflare provides [point-in-time recovery for durable SQLite](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/). That platform history can retain deleted data for up to 30 days. This release does not create nightly R2 copies. Each synced local store is an additional copy, not protection against a deletion that correctly propagates everywhere.

After a cloud rollback, stop syncing devices, preserve a current local copy including tombstones, then disconnect/reconnect that current copy to reset cursors and replay deletion history. Sync it fully before reconnecting older devices. Do not treat a historical cloud restore as a privacy-safe recovery by itself. If the cloud database was replaced, its new `vault_id` causes clients to refuse sync until they reconnect deliberately.

Deleting a deployment requires removing its Durable Object data/namespace as well as the Worker using Cloudflare's account tools; deleting a Worker alone is not a guarantee that stored data or platform recovery history vanished. That destructive account operation is separate from disconnect.

## Limits and troubleshooting

| Symptom | Action |
| --- | --- |
| HTTP 401 | Token was revoked/expired, or password/invitation is wrong. Re-pair; invitations expire after ten minutes and work once. |
| HTTP 403 | Check device spaces and exact browser `Origin`; do not widen scopes automatically. |
| HTTP 409 | Check protocol/version or a changed vault. Immutable same-ID content conflicts need investigation, not forced overwrite. |
| HTTP 413 | Sync requests are capped at 2 MB and pages at 200 journal references. A single connected provenance group must fit 2 MB/20,000 rows; Paid does not remove this cap. Ordinary larger datasets use multiple pages. Custom clients must send missing endpoints before exceeding 1,000 unresolved links per space or 10,000 total. OAuth payloads are limited to 16 KB. |
| HTTP 429 | Wait a minute; pairing/OAuth allows 20 requests per IP per minute, other routes 600. Background sync backs off. |
| HTTP 503 during pairing | Ensure `ADMIN_PASSWORD` has at least 32 characters and was deployed as a secret. |
| Pending rows after a successful sync | A pull discovered additional local changes or a write occurred concurrently. Run another cycle. |
| Worker CPU/storage limits | Use Workers Paid as recommended, inspect Cloudflare usage, and reduce workload before changing limits. |
| No sync while an adapter is idle | Run `cloud run` persistently against the adapter's actual directory. |
| Wiki export refuses a file | It was manually edited or is unrelated; preserve it outside the managed path before retrying. |

Expired evidence is masked on reads and removed from live storage on maintenance, including hourly cloud maintenance. Sync history stores IDs only, never old content. Permanent tombstones and apply-time closure stop stale devices restoring deleted records. Independent explicit saves can retain separate IDs and provenance; equivalent claims are not interpreted as contradictions. Arbitrary external copies cannot be recalled.

## Development verification

From the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
npm test
python3 scripts/test-cloud.py
python3 cloud/build.py
cd cloud
uv run --locked pywrangler deploy --dry-run
```

The isolated runtime harness runs on macOS/Linux; the ordinary CLI test matrix also covers Windows. The cloud test creates an isolated temporary Worker, two real SQLite clients, and a random test password. It verifies initial upload, space exclusion, offline deletion closure, remote save/search parity, MCP, browser consent, PKCE token exchange, refresh, and revocation. It stops its runtime and deletes its test data. Unit tests cover merge ordering, no-op replay, field conflicts, retention, and wiki ownership. These are local-runtime checks, not a claim that your account or third-party MCP app has been tested.

The Python Worker imports a generated copy of the canonical engine via `cloud/build.py`; that directory, dependency environments, secrets, and local runtime state are ignored by git. Remove `.venv-workers`, `python_modules`, and `.wrangler` when no longer needed. Keep `uv.lock` and `package-lock.json` for repeatable deployments.
