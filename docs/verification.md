# Local verification — 2026-09-12

This preview was developed and tested on macOS with Python 3.14.6 and Node
22.23.0. CI is configured for Python 3.11/3.14 on Ubuntu/macOS; those remote
matrix jobs have not been run here.

Final local suite: **65 Python tests passed** with ResourceWarnings promoted to
errors, and **11 Node tests passed**. Both queued Hermes callback regressions ran
against the reference upstream MemoryManager, including expanded skill messages.

The wheel and source distribution build successfully. A fresh virtual environment
installed the wheel without runtime dependencies and passed CLI init/pair/doctor,
HTTP UI/save/recall, MCP initialize/list/recall, export, and token-revocation smoke
checks. The source distribution includes both native adapters; the Python wheel
contains the service, CLI, MCP interface, and UI assets.

## Coverage

- Core regressions cover scoped authorization, idempotent capture, provenance,
  durable retries and leases, conflicting and repeated facts, historical recall,
  correction revisions, expiry, negation preservation, unsupported success
  claims, deletion during in-flight model work, index purging, and export/restore.
- Provider tests use local HTTP fixtures for structured extraction and embeddings,
  malformed responses, timeouts, redaction, and explicit remote-provider consent.
- Interface tests exercise real authenticated HTTP, browser origin protections,
  CLI persistence and token revocation, MCP stdio tools, and UI request races.
- Adapter tests exercise native registration, scoped tools, source roles, stable
  run identities, concurrent recall isolation, memory-tool echo exclusion, and
  safe timeout behavior. A real upstream Hermes MemoryManager regression queues
  two completed-turn callbacks before draining them to check capture identity.
- A cross-agent test runs the Python Hermes adapter and Node OpenClaw adapter
  against the real local service. A project decision travels between hosts, a
  correction changes the current answer, an unlinked credential cannot retrieve
  it, and forgetting removes the source lineage.

Headless Chrome browser QA used synthetic data at desktop and mobile sizes.
It exercised connection, browsing, literal rendering of malicious markup,
inspection, correction, forgetting, empty search, disconnect, layout overflow,
and console errors. Delayed-response regressions cover disconnect and searches.

## Reproduce

```sh
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -v
node --test adapters/openclaw/test.mjs adapters/openclaw/tools.test.mjs
PYTHONPATH=src python3 benchmarks/replay.py --output benchmarks/results.json
python3 -m build
```

The standalone suite substitutes a test-only Hermes import surface when the
reference checkout is absent; its two real MemoryManager tests are then skipped.
See [compatibility](compatibility.md) for the exact upstream commits and surfaces.

## Evaluation limits

The six-case [synthetic replay](../benchmarks/results.json) covers current and
historical facts, semantic recall, scope denial, unsupported completion, and
abstention. bigfeels retrieved all three expected items with zero forbidden
exposures and all three expected abstentions. These hand-authored fixtures and
fixture embeddings do not establish general answer quality or superiority over
another product. No paid model calls were made.

A disposable 1,000-record local corpus took 1.548 seconds to populate. Ten
keyword retrievals measured 21.457–22.941 ms (mean 21.83 ms) on this machine.
These are a small local observation, not an SLA or a vector-scale benchmark.

No live Hermes profile or OpenClaw Gateway was modified or used to capture
private conversations. Host discovery, long-running delivery, actual hosted-model
quality/cost, OS daemon installation, remote hosting, and multi-device operation
remain unverified. The package is a local preview, not a published registry release.
