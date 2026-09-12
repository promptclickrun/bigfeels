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
the `owner` space, and the `main` agent. Set `dataDir` only when the host needs
a specific local directory. Set `projectSpaces` and `writeSpace` to link extra
spaces. Python 3.11+ must be available as `python3`; set the optional
`pythonPath` setting when it is elsewhere.

The default `main` primary agent needs no LLM policy. If `primaryAgentId` is
set to another agent, add OpenClaw's explicit policy under that plugin entry:
`"llm": { "allowAgentIdOverride": true }`. Without that host capability the
adapter leaves extraction queued instead of silently using another agent's
credentials.

Native operations launch one short-lived Python child with bounded stdio.
Queued extraction sends only redacted evidence to OpenClaw's official
`api.runtime.llm.complete` callback. The callback leaves `model` and
credentials to the active host agent, so its configured subscription and
credential ownership are used. It supplies the bounded primary agent context
to injected runtimes; a non-default `primaryAgentId` requires the host's
explicit agent binding capability. If that capability is unavailable,
extraction stays queued and the conversation still completes.

The adapter registers `bigfeels_search`, `bigfeels_inspect`,
`bigfeels_remember`, `bigfeels_correct`, `bigfeels_forget`, and
`bigfeels_status`. Recalled content is marked as historical evidence and is
never treated as instructions. Automatic capture is limited to the configured
primary agent's foreground runs; scheduled jobs, subagents, and other agents
are ignored. Tool output from bigfeels itself is suppressed, while source IDs
remain replay-stable and echoed memories retain their lineage.

The previous authenticated HTTP service remains available as an explicit
compatibility mode. Supply `mode: "http"`, `url`, and `token` (plus the same
scope fields) when using a separately managed local service. Native mode
rejects URL and token settings so credentials cannot accidentally cross the
stdio boundary. No browser pairing or second model provider is required.

Run the adapter tests directly with:

```sh
npm test --prefix adapters/openclaw
```
