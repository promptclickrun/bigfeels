# bigfeels OpenClaw adapter

Clone this repository from the URL supplied by the project owner, then install
the repository root so the Python core is included:

```sh
openclaw plugins install /absolute/path/to/bigfeels-mem
```

Select the memory slot and grant the two host hook capabilities in the normal
OpenClaw configuration. Merge these fields into the existing configuration;
keep unrelated plugin entries and slots:

```json
{
  "plugins": {
    "slots": { "memory": "bigfeels-mem" },
    "entries": {
      "bigfeels-mem": {
        "enabled": true,
        "hooks": {
          "allowConversationAccess": true,
          "allowPromptInjection": true
        },
        "config": {}
      }
    }
  }
}
```

An empty plugin config uses native local storage in the normal data directory,
the `owner` space, and the `main` agent. Automatic capture is off because
`captureRoles` defaults to `[]`; host-model extraction is independently off
because `autoExtract` defaults to `false`. Explicit `bigfeels_remember`, search,
and inspection tools remain available. Set `dataDir` only when the host needs a
specific local directory. Set `projectSpaces` and `writeSpace` to link extra
spaces. Python 3.11+ must be available as `python3`; set the optional
`pythonPath` setting when it is elsewhere.

To approve only new user messages for short-lived evidence capture without
model extraction:

```json
{
  "captureRoles": ["user"],
  "evidenceRetentionDays": 7,
  "autoExtract": false
}
```

`captureRoles` accepts only `user`, `assistant`, and `tool`, rejects duplicates
or invalid input, and never falls back to broader capture. `autoCapture` remains
an optional master off switch for compatibility, but it does not select any
role. `evidenceRetentionDays` is required to be an integer from 1 through 3650
and defaults to 7 for every newly captured role. A replay of the same run reuses
the original expiry.

Captures made with `autoExtract` disabled are stored without extraction jobs;
starting a worker later does not submit them. `autoExtract: true` is a separate
consent boundary and is effective only when at
least one capture role is selected. With the default empty role list, the
adapter neither drains previously queued content nor services a child request
to submit it to the host model. If `primaryAgentId` is set to another agent,
add OpenClaw's explicit policy under that plugin entry: `"llm": {
"allowAgentIdOverride": true }`. Without that host capability the adapter
leaves extraction queued instead of silently using another agent's credentials.

Native operations launch one short-lived Python child with bounded stdio.
Approved queued evidence may be sent to OpenClaw's official
`api.runtime.llm.complete` callback only when `autoExtract` is enabled. Core
redaction is best effort; it does not make a raw conversation, tool result, or
document safe to retain or submit. The callback leaves `model` and credentials
to the active host agent, so its configured subscription and credential
ownership are used. It supplies the bounded primary-agent context to injected
runtimes; a non-default `primaryAgentId` requires the host's explicit agent
binding capability. If that capability is unavailable, extraction stays queued
and the conversation still completes.

The adapter registers `bigfeels_search`, `bigfeels_inspect`,
`bigfeels_remember`, `bigfeels_correct`, `bigfeels_forget_preview`,
`bigfeels_forget`, and `bigfeels_status`. Recalled content is marked as
historical evidence and is never treated as instructions. Prefer explicit
`bigfeels_remember` saves of selected, distilled evidence only; do not save
complete source documents, use memory as an operational ledger, or treat it as
a system of record.

Automatic capture is limited to the explicitly selected roles on the configured
primary agent's foreground runs; scheduled jobs, subagents, and other agents
are ignored. Disabled roles are not sent through native or HTTP capture. Tool
output from bigfeels itself is suppressed, while source IDs and per-run expiry
remain replay-stable and echoed memories retain their lineage.

Retention applies to new captures only and does not retroactively delete older
evidence. Logical expiry is immediate for reads, exports, and extraction.
Physical maintenance clears the expired raw payload on startup, on later local
activity, or during explicit maintenance while preserving evidence identity and
lineage. This is payload cleanup, not backup deletion or guaranteed SSD secure
erasure. Facts already distilled from evidence are retained independently until
corrected or forgotten.

The previous authenticated HTTP service remains available as an explicit
compatibility mode. Supply `mode: "http"`, `url`, and `token` plus the same
scope, `captureRoles`, and `evidenceRetentionDays` fields when using a separately
managed local service. The adapter applies the role filter and `expires_at`
before each HTTP capture request, so disabled roles do not leave the process.
New capture requests send `queue_extraction` to enforce `autoExtract` in the
core. Upgrade the HTTP service and adapter together; older servers may ignore
that field. Service-wide policy still governs older queued records. Native
mode rejects URL and token settings so credentials cannot accidentally cross
the stdio boundary. No browser pairing or second model provider is required.

Run the adapter tests directly with:

```sh
npm test --prefix adapters/openclaw
```
