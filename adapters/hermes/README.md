# Hermes adapter

This repository is a native Hermes `MemoryProvider` plugin. Install the
repository link directly with Hermes, then activate it through the normal
memory command:

```sh
hermes plugins install promptclickrun/bigfeels --enable
hermes memory setup bigfeels
```

For an existing Mnemosyne provider, follow the separate
[migration and rollback guide](../../docs/mnemosyne-migration.md). Importing does
not switch profiles. Set the intended `HERMES_HOME` explicitly and preserve any
existing local plugin changes before installation or update.

Setup has no required fields. The provider opens the profile-local bigfeels
store at `$HERMES_HOME/bigfeels` in the primary Hermes process and defaults to
the `owner` space.
Automatic turn capture is off: `capture_roles` defaults to an empty list, and
`auto_extract` defaults to `false`. Explicit `bigfeels_remember` and search
operations remain available without enabling either setting. There is no
browser pairing, separate token, memory service, or model-provider key to copy.

Optional profile settings live under `memory.bigfeels` in Hermes config:

```yaml
memory:
  bigfeels:
    path: ${HERMES_HOME}/bigfeels
    project_spaces: [project:app]
    write_space: project:app
    capture_roles: [user]
    evidence_retention_days: 7
    auto_extract: false
```

`capture_roles` is the explicit automatic-capture allowlist. It accepts only
`user`, `assistant`, and `tool`; invalid values fail configuration rather than
falling back to broader capture. New automatic captures always receive a finite
expiry: `evidence_retention_days` defaults to 7 and accepts 1 through 3650.
The expiry is reused when Hermes replays a completed turn.

`auto_extract` is a separate opt-in. Captures made with it disabled are stored
without extraction jobs, so a later worker cannot submit those captures.
It must be `true` before queued evidence
can be submitted to Hermes's host-model extraction lane. When capture is
disabled, the adapter does not attach that extractor or process older queued
evidence through it. `path` defaults to `$HERMES_HOME/bigfeels` when Hermes supplies
the profile home. Outside a Hermes profile, the engine uses its normal default.
The older `plugins.bigfeels.data_dir` setting remains supported;
`memory.bigfeels` overrides matching legacy settings and `path` takes precedence
over `data_dir`. An explicitly configured `path` must be a nonempty string;
null and blank values fail initialization instead of selecting another store.
Explicit constructor settings still take precedence over both.

Before updating an existing shared-store installation, pin its current directory
with `memory.bigfeels.path` to keep using it. Updating does not move or reimport
any data. Separate profile defaults prevent accidental sharing; deliberate
sharing requires explicitly selecting the same directory and spaces.
`project_spaces` are explicitly added to the owner scope, and approved capture
goes to `write_space` (the owner space by default). Embeddings remain an
optional advanced service configuration and are not implied by Hermes model
access.

Prefer explicit `bigfeels_remember` saves containing selected, distilled
evidence only; do not save complete conversation turns or source documents.
Redaction is best effort and does not make a raw document safe to retain or
submit. Bigfeels memory is not a replacement for an operational ledger or
system of record.

The retention setting applies only to new automatic captures; it does not
retroactively remove existing evidence. Logical expiry is immediate for reads,
exports, and extraction. Physical maintenance clears an expired capture's raw
payload on startup, on later local activity, or during explicit maintenance
while preserving its evidence identity and lineage. This is payload cleanup,
not backup deletion or guaranteed SSD secure erasure. Facts already distilled
from that evidence have their own lifecycle and remain until corrected or
forgotten.

Hermes initializes subagents and background platforms without opening local
storage. The provider uses Hermes's lifecycle for same-turn recall, completed
turn capture, session switches, and the seven scoped native memory tools:
`bigfeels_search`, `bigfeels_inspect`, `bigfeels_remember`, `bigfeels_correct`,
`bigfeels_forget_preview`, `bigfeels_forget`, and `bigfeels_status`.

For existing deployments that deliberately use the authenticated HTTP service,
construct the provider with an explicit `AdapterConfig`. It enforces the same
role allowlist and finite `expires_at` payload before any capture request leaves
the process, so disabled roles are not sent to the service. The legacy
`BIGFEELS_MEM_*` environment path accepts `BIGFEELS_MEM_CAPTURE_ROLES`,
`BIGFEELS_MEM_EVIDENCE_RETENTION_DAYS`, and `BIGFEELS_MEM_AUTO_EXTRACT` with the
same defaults and validation. New capture requests also send `queue_extraction`
to enforce the extraction choice in the core. Upgrade the HTTP service and
adapter together; an older server may ignore that field. Service-wide policy
still governs older queued records. This remains an advanced compatibility path and is
not required by native installation or setup.
