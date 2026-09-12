# Operating bigfeels

## Storage and trust boundary

One service owns one SQLite database for an OS user. SQLite WAL and per-operation
transactions coordinate concurrent requests; ingestion is acknowledged only after
commit. The database is created with mode 0600. Use a private data directory and
OS disk encryption for at-rest protection; the database is not application-encrypted.

The service binds to loopback and validates browser origins and Host headers.
Bearer tokens are high-entropy and only SHA-256 digests persist. Every token has
explicit space grants. A paired agent is trusted to write in those spaces;
provenance is audit evidence, not cryptographic proof of a host's honesty.
Run untrusted agents with separate spaces and credentials.

`config.json` stores model identifiers, endpoint, timeout, key environment-variable
name, and the remote-transfer choice. Never put key values in config. HTTP proxies
and provider redirects are disabled to keep credentials on the configured endpoint.
Model requests have bounded timeouts and response sizes. Model errors are generic;
upstream bodies and private inputs do not appear in diagnostics.

## Queue recovery and degraded operation

Each captured event has a stable identity and an exact redacted-payload fingerprint.
An identical retry returns its prior event; a changed payload under the same identity
is a 409 conflict. Origin references identify mirrored memory and avoid recursive
ingestion. Agents must preserve event IDs across retries.

Workers claim events with transactional 120-second leases. Model work happens
outside database transactions. Completion checks that the lease and source evidence
still exist. Deletion or expiry during processing prevents stale results from
reappearing. Failed extraction is requeued with a 30-second retry delay. Invalid
candidates do not discard good candidates from the same response.

Embedding versions are stored with vectors. A changed model produces a separate
index; retrieval only uses the configured model. Missing vectors use keyword
retrieval while background indexing catches up. Query embedding failures fall back
to keyword matches and report `embedding_status: unavailable`.

The initial vector implementation performs a scoped exact scan of vectors stored
as JSON in SQLite. FTS ranking uses a temporary permitted corpus to prevent private
documents influencing ranking statistics. Both favor auditability over large-corpus
throughput. Measure your corpus before increasing workload; neither ANN scale nor
a latency SLA is claimed.

## Retention, deletion, and backup

Set `expires_at` on observations to expire source content at a known time. Sources
without an expiry remain until forgotten. Maintenance removes expired content,
marks linked knowledge as lacking available source content, and discards unfinished
extraction for those sources. It does not silently erase independent derived knowledge.

Forget closes over supporting evidence, dependent memories, and correction lineage.
All affected records are immediately excluded within the transaction. Tombstones
retain IDs, source identity digests, and space names, never deleted text. Periodic
maintenance rebuilds the text index and VACUUMs/checkpoints the database after a
deletion or expiry. `bigfeels-mem maintenance` forces that local maintenance pass.

Physical purge cannot recall data from an in-flight network request, another process's
memory, OS snapshots, provider retention, host histories, or previous exports. Manage
those separately. A replay with the same source identity is blocked; a fresh unrelated
source manually reintroducing the same fact is not content-censored.

Exports are plaintext and should be stored privately. They preserve evidence,
knowledge, relationships, tombstones, and processing state, but no credentials or
derived vector indexes. Restore into a fresh data directory; grants must be paired
again. Restore validates relational scope boundaries and rejects a snapshot containing
both deleted records and their tombstones. Concurrent snapshots are transactionally
consistent; restored processing leases restart as pending work.

## Evidence and promotion

User statements can become direct memories; tool output can become observations.
Assistant prose and document text remain inferred unless explicitly saved or corrected.
Automatic promotion preserves the complete source message up to 16,000 characters
to avoid turning an extracted substring into a misleading claim. Longer synthesis
is kept as a candidate. A provider's `verified` label is downgraded automatically;
the tool evidence itself remains available for inspection. Explicit verified saves
require observed basis and a tool evidence reference.

Remembered instructions have no special authority. Native integrations frame recall
as untrusted contextual evidence. Host approval/sandbox policies still govern actions.

## Diagnostics

Use `doctor` for database/configuration status and the UI for queue counts and
provider availability. Pending work with no extraction model is expected. Check the
service environment for the named key variable if requests fail. Use a compatible
chat model with JSON-object output and a distinct embedding model. Do not paste
credentials or real memory exports into bug reports.

Check [verification](verification.md) before relying on a host integration. Installation
and model selection are explicit operator actions; this implementation does not alter
existing agent profiles or register a background OS daemon.
