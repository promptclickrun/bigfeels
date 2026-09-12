# Agent installation guide

Install into the host the user asked to configure. Normal setup uses the host's
existing model access. Do not ask the user for an API endpoint, API key, local
pairing code, or browser login to bigfeels.

## Hermes

Use the supplied repository URL with Hermes's official installer:

```sh
hermes plugins install REPO_URL --enable
hermes memory setup bigfeels
hermes memory status
```

The root `plugin.yaml` and `__init__.py` are the native provider entry. Core Python
code is included in `src`; no separate pip install is needed inside Hermes.
For a local checkout, the complete repository can also live at
`$HERMES_HOME/plugins/bigfeels`. Do not copy only `adapters/hermes`.

Confirm the selected profile and read its current memory provider first. Selecting
bigfeels replaces the external provider slot; it does not migrate or delete that
provider's stored data. Preserve built-in `MEMORY.md` and `USER.md`. Use normal
Hermes plugin update commands for an existing installation; do not force-delete it.

No required fields follow provider selection. The next host session initializes
the local database and background worker. Calls use Hermes's official auxiliary
model API, which owns supported subscription authentication and credential refresh.
Optional overrides use `plugins.bigfeels`, described in the adapter README.

## OpenClaw

Clone the supplied repository and install its **root directory** using
`openclaw plugins install /absolute/path/to/bigfeels-mem`. Follow the
[native plugin instructions](adapters/openclaw/README.md) for the memory slot and
conversation-hook permissions. These are host settings, not bigfeels accounts.

Python 3.11+ must be available as `python3`; if it is elsewhere, set the optional
plugin `pythonPath` setting. The plugin starts its Python helper when needed. The
helper receives no model credentials; requests go back to OpenClaw's official
completion runtime. Do not configure a second provider or a local HTTP service.

## Any MCP host

Install this checkout with that host's available Python environment:

```sh
python3 -m pip install /absolute/path/to/bigfeels-mem
```

Add MCP command `bigfeels-mem`, arguments `["mcp"]`, using the executable's
absolute path if the host has a different PATH. Storage opens locally on startup.
The agent uses its current model to decide which explicit memories to save.
MCP alone cannot automatically observe an arbitrary host's conversation lifecycle.

## Verify and report honestly

Start a new host session, save a harmless unique test preference through the
memory tool, recall it in another session, then forget it. For a native host,
also check an automatically captured turn and confirm processing completes.
If model access fails, inspect the host's ordinary model/login status; never
copy its tokens into bigfeels or claim pending capture is successful learning.

Report which checks actually ran. A configured provider is not proof of live
capture. Do not restart an active gateway or log into an account without the
user's authorization. No browser is required for this installation flow.
