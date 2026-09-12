# Host adapter compatibility

The adapters are thin clients of the authenticated v1 HTTP contract. They do
not read the SQLite database, grant spaces, edit host configuration, or infer
that an assistant claim is a verified outcome.

## Hermes

Source compatibility was checked against Hermes Agent commit
`44ddc552f5e054759a6970af8997ea588a9d81c9` in the read-only
`upstream/hermes-agent-official` checkout.

The plugin implements `agent.memory_provider.MemoryProvider` and registers it
with `register_memory_provider`. Hermes discovers user memory providers under
`$HERMES_HOME/plugins/<name>`, selects one with `memory.provider`, initializes
it with `agent_context` and `platform`, calls `on_turn_start` before `prefetch`,
passes full messages to background `sync_turn`, and reports session changes
through `on_session_switch`. The adapter exposes no generic Hermes hook or core
patch. `get_tool_schemas` and `handle_tool_call` expose the six scoped v1 memory
operations through the native provider tool path.

Hermes does not give `sync_turn` its separate observer `turn_id`. The provider
captures bounded, locked per-session snapshots of Hermes's restored
`turn_number` at `on_turn_start`, then matches a later background `sync_turn`
to its immutable snapshot. A bounded completed-turn cache preserves the same
source keys on retries. This prevents a queued turn from adopting the next
turn's mutable identity. The snapshot normalizes expanded skill turns with
Hermes's canonical `extract_user_instruction_from_skill_message` helper, which
also excludes bare skill invocations exactly as `MemoryManager` does. A write
is skipped if no safe lifecycle identity can be resolved. Subagent providers
are normally omitted by Hermes and are also rejected when initialized with a
non-primary context. Cron and other background platforms are rejected.

## OpenClaw

Source compatibility was checked against OpenClaw commit
`826a97e7d79521341abbddf9c619787fef6b090d` in the read-only
`upstream/openclaw-openclaw-pr88172` checkout. The minimum declared host and
Plugin API version is `2026.6.11`.

The plugin uses `definePluginEntry`, `api.on`, `api.registerTool`, and the
public `openclaw/plugin-sdk/routing` `isSubagentSessionKey` helper. Automatic
recall runs at `before_prompt_build`; automatic capture runs at `agent_end`;
bounded per-run recall state is cleared at `session_end`. The same OpenClaw
`runId` is stable across a turn's model retries, and source keys include both
`sessionKey` and `runId`. Missing identity, non-primary agents, subagent keys,
scheduled jobs, and known background triggers are skipped. Installed plugins
must opt into `hooks.allowConversationAccess`; prompt injection remains under
OpenClaw's `allowPromptInjection` policy. Its manifest declares each registered
tool in `contracts.tools`, as required by the inspected plugin loader.

## Shared behavior

Both adapters send the configured owner space plus only explicitly listed
project spaces on recall. Capture is forced to one configured space that must
belong to that list. The service still enforces the bearer credential's scopes.
User, tool, and assistant events retain their roles and stable host identities.
Results from the adapters' own `bigfeels_*` tools are excluded from automatic
tool capture so recalled or inspected content cannot return as fresh evidence.
Exact recalled-content echoes carry their source evidence IDs, allowing the
service to suppress replay; mixed or novel assistant replies do not, so new
inferences are retained as candidates. Recalled context displays memory and
evidence IDs, basis, outcome, validity, status, and source availability when
the service supplies them.

Both clients reject credentials in URLs, reject non-loopback plaintext HTTP,
refuse redirects, cap JSON responses at 1 MiB, bound network time, and omit
credentials and response bodies from errors and logs.

## Verification boundary

The Python tests exercise the real Hermes `MemoryProvider` ABC when the
read-only upstream checkout is present, with a faithful test-only host contract
fallback for standalone package CI. They also exercise all provider tool schemas
and dispatch. The Node tests execute the adapter against a real loopback HTTP
fixture and verify the native hook/tool registration surface. These checks
verify request payloads, roles, session/run isolation, idempotent source keys,
pure-echo provenance, primary identity filtering, space allowlists, timeouts,
and secret-free error paths.

No adapter was installed into a live Hermes profile or OpenClaw Gateway. No
private conversation was captured. The results establish source-contract and
local HTTP behavior, not live host discovery, process lifecycle, delivery, or
upgrade compatibility beyond the two commits above.
