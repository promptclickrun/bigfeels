# Install bigfeels

Choose an interface first. The Python engine is the product; host plugins are optional adapters.

## Requirements

- Python 3.11 or newer
- SQLite with FTS5
- a private writable data directory
- Node only when using the optional OpenClaw adapter or running its tests

The Python core has no mandatory runtime dependencies. This preview is installed from a checkout, not a package registry.

## CLI, MCP, HTTP, and UI

Use an isolated environment:

```sh
python3 -m venv ~/.local/share/bigfeels/venv
~/.local/share/bigfeels/venv/bin/python -m pip install /absolute/path/to/bigfeels-mem
~/.local/share/bigfeels/venv/bin/bigfeels-mem --help
```

Initialize a chosen data directory and smoke-test an explicit save:

```sh
BIGFEELS=~/.local/share/bigfeels/venv/bin/bigfeels-mem
$BIGFEELS --data-dir ~/.local/share/bigfeels/data init
$BIGFEELS --data-dir ~/.local/share/bigfeels/data remember "Installation smoke test." --space owner
$BIGFEELS --data-dir ~/.local/share/bigfeels/data search "smoke test" --space owner
```

Use that executable as the MCP command with arguments:

```text
--data-dir /absolute/private/data/path mcp --space owner
```

No bearer token or HTTP server is needed for direct CLI or local MCP. The client decides which explicit memories to save. MCP alone cannot automatically observe a host's conversation lifecycle.

For HTTP and the browser workspace, pair a credential scoped to the spaces it needs, then run the service:

```sh
$BIGFEELS --data-dir ~/.local/share/bigfeels/data pair inspector --space owner
$BIGFEELS --data-dir ~/.local/share/bigfeels/data serve
```

Open `http://127.0.0.1:8765`. Keep the returned credential private. The service does not create an online account.

## Optional extraction provider

Explicit remember/search/context/inspect/correct/forget operations do not need a model. `observe` queues evidence for extraction. To process queued observations in standalone mode, configure an OpenAI-compatible provider:

```sh
$BIGFEELS --data-dir ~/.local/share/bigfeels/data configure \
  --base-url http://127.0.0.1:11434/v1 \
  --extraction-model MODEL_NAME
$BIGFEELS --data-dir ~/.local/share/bigfeels/data process --space owner --limit 8
```

For a remote endpoint, set the named key environment variable and explicitly add `--allow-remote`. Only the environment variable name is stored. Check `status` after processing. `processed: 0` with pending work means no job was ready, the extractor is absent/busy, or work is delayed; it is not successful learning.

## Optional Hermes adapter

The root `plugin.yaml` and `__init__.py` expose the adapter expected by Hermes. If you explicitly want native Hermes lifecycle capture, use Hermes's official plugin installer with the supplied repository URL, then select the provider:

```sh
hermes plugins install REPO_URL --enable
hermes memory setup bigfeels
hermes memory status
```

Start a new Hermes session for provider activation. Read [adapters/hermes/README.md](adapters/hermes/README.md) before configuring optional spaces. Do not migrate, delete, or overwrite another provider's data as part of installation.

## Optional OpenClaw adapter

Install the checkout root through OpenClaw's plugin installer only when native OpenClaw hooks are wanted:

```sh
openclaw plugins install /absolute/path/to/bigfeels-mem
```

Then follow [adapters/openclaw/README.md](adapters/openclaw/README.md) for the memory slot and conversation-hook permissions. OpenClaw is an optional peer dependency of the npm adapter package, not a dependency of the Python engine.

## Verify honestly

For every interface you install:

1. save a harmless unique memory in an explicit space;
2. search and inspect it;
3. correct it using the returned revision;
4. preview deletion and review all affected records;
5. delete with the returned plan token;
6. confirm inspect no longer finds it.

For automatic capture, also observe a unique event, process or wait for the configured worker, and confirm `status.queue` plus `status.processing`. Queued evidence is not completed learning. No live profile activation, gateway restart, or model login is implied by package installation.
