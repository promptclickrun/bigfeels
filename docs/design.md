# Why bigfeels works this way

A useful agent memory system remembers across tasks without turning every old
sentence into a permanent fact. Its strongest qualities are selective capture,
retrieval relevant to the current task, traceable evidence, explicit uncertainty,
correction over time, controlled sharing, and inexpensive operation. Continuity
also requires stable identities: retries and switching agents must not multiply
claims or silently move information between projects.

The design addresses failure modes that a memory implementation needs to handle;
this is not a claim that every existing product has each defect.

| Failure mode | bigfeels response | Cost or remaining limit |
| --- | --- | --- |
| A summary drops a qualification or negation | Automatic promotion retains complete bounded source evidence | More context; longer synthesis stays a candidate |
| Similarity retrieves an obsolete fact | Validity intervals, revisions, explicit correction lineage | Unstructured contradictions need a shared subject key |
| Repeated recall becomes independent corroboration | Source identity, deduplication, memory-tool echo exclusion | Adapters cannot recognize arbitrary human paraphrases of recalled facts |
| An agent reports success without observing it | Source basis and outcome are separate; extraction cannot assert verification | Explicit verification still depends on trustworthy evidence |
| One project's secrets appear in another | Credential scopes are checked before ranking | Local credentials authorize all operations in their spaces |
| Deletion leaves a source that can recreate the fact | Delete dependency closure and retain content-free replay tombstones | External transcripts, exports, and provider copies remain external |
| A write blocks the conversation or disappears during failure | Durable leased processing with bounded provider calls and retries | Host capture must reach the local service before it is durable |
| A memory becomes a hidden instruction or permission | Retrieved context is labeled evidence, not authority | The consuming agent must maintain its own instruction boundaries |
| Infrastructure overwhelms a personal installation | Standard-library service and SQLite FTS/vector scan | Exact vector search targets modest corpora, not a large fleet |

## Three layers

**Evidence** records what was captured, where, when, and under which space. Stable
event identities make retries idempotent. Redaction and capture policy run before
persistence. Explicit saves also retain evidence; they do not bypass provenance.

**Knowledge** records derived or explicitly saved memories, including their kind,
source basis, status, validity, revision, and supporting evidence. Candidate,
active, disputed, and superseded states remain inspectable. Agreement adds source
lineage rather than artificial confidence votes. A correction changes the current
answer while retaining historical meaning until the lineage is forgotten.

**Task context** is a bounded retrieval product, not the database itself. Scoped
keyword and optional vector results are filtered by temporal validity and status.
The response includes evidence references, matching information, and uncertainty.
Abstaining is preferable to inserting an unrelated or unsupported memory.

## Integration and trust

Hermes and OpenClaw use their native lifecycle contracts for capture and recall.
MCP exposes explicit tools to other hosts; it cannot promise automatic capture
without access to that host's lifecycle. Shared owner memory and explicitly named
project spaces give participating agents continuity without inferring links from
folders or names.

The local service owns credentials, persistence, and mutation rules. Adapters are
thin clients. Hosted extraction and embedding models are optional, separately
configured consumers of redacted data. Provider output is untrusted structured
input and cannot grant permissions, verify its own success, or execute procedures.

The preview favors auditability and conservative promotion over autonomous
consolidation. Future improvements should be justified by realistic continuity
evaluations: answer correctness, stale-fact exposure, cross-scope leakage,
abstention, deletion durability, latency, and model cost. The bundled synthetic
replay is a regression fixture, not evidence of general superiority.
