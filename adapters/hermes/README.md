# Hermes adapter

This directory is a native Hermes `MemoryProvider` plugin. It uses Hermes's
memory lifecycle for same-turn recall, completed-turn capture, session switches,
and explicit memory tools. It does not patch Hermes or install a sidecar.

Copy this directory to the active profile's plugin directory as `bigfeels`:

```sh
cp -R adapters/hermes "$HERMES_HOME/plugins/bigfeels"
```

Configure the running Hermes process with the scoped credential created by
`bigfeels-mem pair`, then select the provider through Hermes's normal memory
configuration:

```sh
bigfeels-mem init
bigfeels-mem space project:app
bigfeels-mem pair hermes --space owner --space project:app
bigfeels-mem serve

export BIGFEELS_MEM_URL=http://127.0.0.1:8765
export BIGFEELS_MEM_TOKEN='the-paired-token'
export BIGFEELS_MEM_OWNER_SPACE='owner'
export BIGFEELS_MEM_PROJECT_SPACES='project:app'
export BIGFEELS_MEM_WRITE_SPACE='project:app'
hermes memory setup
```

Choose `bigfeels` when `hermes memory setup` asks for the provider. The setup
flow records `memory.provider: bigfeels`; this repository never edits a live
Hermes configuration. The token's server-side scopes must cover the owner
space, every listed project space, and the write space. Omit project spaces to
use owner memory only. The write space defaults to the owner space.

Optional `BIGFEELS_MEM_TIMEOUT` (seconds, default `0.75`) and
`BIGFEELS_MEM_BUDGET` (tokens, default `800`) bound recall. Plain HTTP endpoints
must be loopback. HTTPS endpoints may be used when a separately secured local
transport requires it.

Hermes initializes subagents without the primary provider, and this adapter
also rejects non-primary `agent_context` values plus cron, flush, background,
and kanban platforms. Capture uses Hermes's normally background `sync_turn`
path, applies a bounded HTTP timeout, and reports service failures without
exposing the bearer token. The provider exposes `bigfeels_search`,
`bigfeels_inspect`, `bigfeels_remember`, `bigfeels_correct`,
`bigfeels_forget`, and `bigfeels_status` through Hermes's native memory tools.
