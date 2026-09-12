# bigfeels

**Memory that travels with your agents, and shows its work.**

Give your agent this repository link and say:

> Install bigfeels as my memory provider. Use my agent’s existing model login.

bigfeels stores memory on your device. Native Hermes and OpenClaw integrations
handle capture, recall, and background learning. Your agent manages its model
credentials; bigfeels does not need a second login, API key, endpoint, browser,
pairing code, or manually started server.

## For the installing agent

Follow [INSTALL.md](INSTALL.md). The repository root is an installable native
plugin for both Hermes and OpenClaw. Use the host’s own plugin installer and
configuration commands. Preserve unrelated settings and existing memory.

For Hermes, using the actual repository URL the user supplied:

```sh
hermes plugins install REPO_URL --enable
hermes memory setup bigfeels
hermes memory status
```

That selects the provider. Start a new Hermes session for it to load. bigfeels
uses Hermes’s supported auxiliary model routing, including subscription logins
that Hermes supports. Provider selection, token refresh, and credential storage
remain with Hermes. No model credentials are copied into bigfeels.

For OpenClaw, the installing agent installs this repository through the native
plugin installer, selects the memory slot, and enables its conversation hooks.
The plugin uses OpenClaw’s model-completion runtime and starts its local storage
helper automatically. See [OpenClaw setup](adapters/openclaw/README.md).

## Defaults

- Local SQLite storage, shared owner memory, and automatic native-host capture.
- Background extraction through the host’s configured model and auth policy.
- Keyword retrieval, with corrections, historical validity, and evidence links.
- No required configuration fields. Projects are linked only when explicitly set.

Background extraction uses your existing model’s allowance and follows the host’s
provider policies. Subscription chat access does not imply access to an embedding
API; semantic embeddings are optional. If host model access is unavailable,
captured events stay queued and explicit memory tools continue to work.

Optional Hermes settings belong in the usual `config.yaml` under
`plugins.bigfeels`; see the [Hermes adapter](adapters/hermes/README.md). Hosts using
the same local data directory share owner memory. Use different data directories
for separate people or identities. Explicit project spaces control which project
knowledge an integration recalls and submits for extraction.

## Other agents

Install the Python package from this checkout, then configure the host’s MCP
command as `bigfeels-mem`, with arguments `["mcp"]`. It opens local storage
directly. No bearer token or HTTP server is needed. The host can search, remember,
inspect, correct, and forget through its own model and MCP tools. Automatic
conversation capture requires a native lifecycle integration.

Python 3.11+ with SQLite FTS5 is required; the Python runtime has no mandatory
third-party dependencies. OpenClaw additionally requires its supported Node runtime.

## Your memory stays inspectable

Ask your agent to inspect a memory, correct it, or forget it. Memories keep their
source evidence and history; inferred claims do not automatically become facts.
Forgetting deletes the supporting lineage and prevents replay of those source
identities. The browser inspection UI is optional.

```sh
bigfeels-mem status
bigfeels-mem doctor
bigfeels-mem export --output memory-export.json
```

The CLI commands above use local filesystem access. Linked projects can be
included with repeated `--space` arguments on `status`, `export`, or `mcp`.
The local database and host plugins run as your OS user; scopes are not an OS
sandbox against another process with the same filesystem access.

[Design and tradeoffs](docs/design.md) · [Operations](docs/operations.md) ·
[Advanced HTTP and model setup](docs/advanced.md) ·
[Compatibility](docs/compatibility.md) · [Verification](docs/verification.md)

The product is **bigfeels**; the package and command are **`bigfeels-mem`**.
This is an Apache-2.0 local preview. It has not been published to a package registry.
