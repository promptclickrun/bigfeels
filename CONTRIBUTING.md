# Contributing

Use Python 3.11+, SQLite with FTS5, and Node 22+ for the OpenClaw adapter tests.
Create an isolated checkout or branch. Add a behavioral regression first, observe
it fail, implement the smallest change, then rerun the relevant suite.

Keep core runtime dependencies optional. New host adapters must implement the
documented service contract through their host's actual public extension API,
with pinned source evidence and an honest live-runtime compatibility status.

Tests must use synthetic data and temporary databases. Do not call paid models,
read personal conversations, or write host profiles in the default suite. Cover
source identity, credentials, session boundaries, failure recovery, and deletion
for every new ingestion path. Do not log credentials or source content.

All submitted contributions are under Apache-2.0. No telemetry is sent by the
service. Report sensitive issues privately to the repository owner's contact
channel once the project is published; do not include private data in public issues.
