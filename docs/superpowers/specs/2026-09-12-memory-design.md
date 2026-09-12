# bigfeels design

Implement the approved portable, evidence-based agent memory design. Open-source
Apache-2.0, on-device service and SQLite storage, optional hosted extraction and
embedding providers, automatic governed capture. Shared owner memory plus
explicitly linked project spaces. Evidence, revisable knowledge, and bounded
task context are separate layers. Personal/project/episodic/procedural knowledge
retains source, time, status, uncertainty, and outcome distinctions.

Required: authenticated scoped API, MCP stdio tools, native Hermes/OpenClaw
adapters, asynchronous durable processing, correction/conflict/revision rules,
forgetting with replay tombstones, retention, hybrid retrieval, evidence inspection,
CLI setup/pairing/diagnostics/export/restore, local inspection UI, reproducible
continuity baselines, compatibility evidence. No network publication, host
configuration edits, live private conversation capture, or daemon installation.
No multi-tenant hosting, sync, or autonomous executable skill modifications.

Default storage is under the OS user data directory. Clients use bearer tokens
whose space permissions are established locally. API cannot self-grant scopes.
Capture policies act before persistence/model submission. No model configured
means queued extraction with honest status, plus functional explicit saves and
keyword retrieval. Memory data never becomes authorization.
