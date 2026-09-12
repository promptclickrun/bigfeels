# Host adapter compatibility

The native adapters use each host's lifecycle and the local bigfeels store.
They do not grant memory access through browser pairing, copy model credentials,
or infer that an assistant claim is a verified outcome. The authenticated v1
HTTP contract remains available as an explicit advanced compatibility path.

## Hermes

Source compatibility was checked against Hermes Agent commit
`44ddc552f5e054759a6970af8997ea588a9d81c9` in the read-only
`upstream/hermes-agent-official` checkout.

The repository root contains the installable `plugin.yaml` and `__init__.py`,
so `hermes plugins install OWNER/REPOSITORY --enable` can load it through
Hermes's real directory loader without a pip install. The plugin implements
`agent.memory_provider.MemoryProvider` and registers it with
`register_memory_provider`. Hermes discovers it under
`$HERMES_HOME/plugins/<name>`, selects it with `memory.provider`, initializes it
with `agent_context` and `platform`, calls `on_turn_start` before `prefetch`,
passes full messages to background `sync_turn`, and reports session changes
through `on_session_switch`. The provider lazily resolves the bundled
`src/bigfeels_mem` package, opens `LocalClient` only for the primary context,
and uses `agent.auxiliary_client.call_llm(task="bigfeels_memory", ...)` for
host-owned extraction. `get_config_schema` has no required fields, so
`hermes memory setup bigfeels` only activates the provider.

The adapter still accepts an explicit `AdapterConfig` and complete
`BIGFEELS_MEM_*` environment configuration for existing authenticated HTTP
deployments. That path uses the same six scoped memory operations through the
native provider tool path.

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
belong to that list. Native local access enforces the same scope through the
`LocalClient` principal; explicit HTTP access additionally enforces bearer
credential scopes. User, tool, and assistant events retain their roles and
stable host identities.
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
private conversation was captured and no live model call was made. The results
establish source-contract and local behavior, not live host discovery, process
lifecycle, delivery, or upgrade compatibility beyond the two commits above.
