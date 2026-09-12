# Hermes adapter

This repository is a native Hermes `MemoryProvider` plugin. Install the
repository link directly with Hermes, then activate it through the normal
memory command:

```sh
hermes plugins install OWNER/REPOSITORY --enable
hermes memory setup bigfeels
```

Setup has no required fields. The provider opens the shared local bigfeels
store in the primary Hermes process, defaults to the `owner` space, and uses
Hermes's configured subscription through `agent.auxiliary_client` when queued
evidence needs text extraction. There is no browser pairing, separate token,
memory service, or model-provider key to copy.

Optional profile settings live under `plugins.bigfeels` in Hermes config:

```yaml
plugins:
  bigfeels:
    data_dir: ~/Library/Application Support/bigfeels-mem
    project_spaces: [project:app]
    write_space: project:app
    auto_extract: true
```

`data_dir` uses the normal bigfeels default when omitted. `project_spaces` are
explicitly added to the owner scope, and capture goes to `write_space` (the
owner space by default). Set `auto_extract: false` to retain evidence in the
local queue until a later native processing call. Embeddings remain an
optional advanced service configuration and are not implied by Hermes model
access.

Hermes initializes subagents and background platforms without opening local
storage. The provider uses Hermes's lifecycle for same-turn recall, completed
turn capture, session switches, and the six scoped native memory tools:
`bigfeels_search`, `bigfeels_inspect`, `bigfeels_remember`, `bigfeels_correct`,
`bigfeels_forget`, and `bigfeels_status`.

For existing deployments that deliberately use the authenticated HTTP service,
construct the provider with an explicit `AdapterConfig`. The legacy
`BIGFEELS_MEM_*` environment variables remain an advanced compatibility path;
they are not required by native installation or setup.
