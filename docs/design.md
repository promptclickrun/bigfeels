# Design

bigfeels is a host-neutral local memory engine. Its durable model and mutation rules do not belong to Hermes, OpenClaw, MCP, or the browser. Interfaces are replaceable clients of the same scoped core.

## Layers

1. **Evidence** records what was captured, where, when, and in which space. Stable event identities make retries idempotent.
2. **Knowledge** records explicit or derived memories with kind, basis, outcome, status, validity, revision, and supporting evidence.
3. **Context** is a bounded retrieval product. It is not a dump of the database and never carries authorization.

The Python core uses SQLite, standard-library HTTP, and no mandatory third-party runtime dependency. Keyword retrieval is always available; embeddings and extraction providers are optional.

## First-class interfaces

- The CLI performs the complete explicit lifecycle directly against local storage and emits JSON.
- MCP gives generic clients the same explicit lifecycle over stdio.
- HTTP gives scoped loopback clients a stable v1 JSON contract.
- The browser workspace is an HTTP client for visible inspection and mutation.
- Native host plugins add lifecycle capture, recall injection, and host-owned extraction. They are optional adapters.

MCP cannot automatically observe arbitrary host conversations. `observe` queues evidence only. A native adapter or explicit client call must provide lifecycle events, and extraction needs a configured model route. This boundary is intentional rather than papered over with a universal-capture claim.

## Safety choices

| Failure mode | Response | Remaining limit |
| --- | --- | --- |
| Summary drops qualification or negation | Keep bounded source evidence and lineage | Long synthesis may remain candidate |
| Obsolete fact wins recall | Validity, revisions, correction lineage | Unstructured contradictions benefit from keys |
| Recalled content becomes fresh corroboration | Stable source identity and origin links | Arbitrary human paraphrases remain hard |
| Model claims success | Basis and outcome stay separate | Trustworthy tool evidence is still required |
| One project influences another | Scope before ranking/extraction | Same-OS-user filesystem access is not sandboxed |
| Deletion silently expands | Preview full dependency closure and bind a plan token | External transcripts/exports remain external |
| Capture is mistaken for learning | Durable queue plus explicit processing diagnostics | Provider quality and availability remain external |
| Memory becomes an instruction | Label recall as contextual evidence | Consuming hosts must enforce their own policy |

## Deletion

Forgetting closes over supporting evidence, dependent memories, and correction lineage. Public interfaces preview all affected memories and evidence counts before execution. The token binds the actual closure and revisions for five minutes, so a concurrent dependency change or delayed confirmation becomes stale rather than broadening deletion silently.

## Processing

`remember` commits a memory synchronously. `observe` commits source evidence and a job. Workers use leases and bounded batches. Status reports queue states and safe retry/failure information without source text or provider error bodies. A zero process count never implies success.

The system favors auditable behavior, abstention, and explicit capability boundaries over autonomous magic. The bundled replay is a regression fixture, not proof of general superiority.
