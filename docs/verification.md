# Local verification — 2026-09-12

## Native setup revision

Final local checks: **82 Python tests passed**, with ResourceWarnings treated
as errors, and **17 Node tests passed**. A standalone copy passed 79 Python
tests and skipped the three checks that require the reference Hermes checkout.

Default setup now runs inside the native host, with its existing model access.
No browser, pairing token, independent HTTP process, or independent model
configuration is required. The optional HTTP interface remains separately tested.

Verified locally:

- Native Python storage and MCP initialize/save/recall without credentials or a
  server; local status, diagnostics, export, and physical index cleanup.
- Space limits apply to local reads/writes and to evidence submitted for model
  processing. Failed model responses leave durable pending work.
- Hermes root repository discovery/loading, bundled core imports without pip,
  native lifecycle identities, echo lineage, and calls to its auxiliary API.
- OpenClaw real Python child save/recall and host-completion fixtures, with no
  credentials passed into the helper environment or protocol.
- Hermes and OpenClaw share the same local store in both directions, with zero
  network credentials created.
- A fresh virtual environment installed the rebuilt wheel and ran local MCP
  save/recall, status, doctor, and export with no token or service.

Host completions in the final suite are fixtures. These results establish use
of official host APIs, not live subscription entitlement, actual provider
response quality, or production gateway behavior. Native keyword retrieval does
not require an embedding endpoint. Setup instructions use the repository URL
supplied by the user; no repository or package was published during this work.

Test-isolation incident: an early native Hermes test opened the default OS
bigfeels directory and could start its worker before isolation was corrected.
The directory was left untouched because its prior existence was not established.
One run failed before reaching the host model API; a later uninstrumented run
does not establish whether a model/network call was attempted. Final tests use
temporary paths, stubbed model calls, and worker shutdown inside fixture lifetime.

## Initial HTTP preview

This preview was developed and tested on macOS with Python 3.14.6 and Node
22.23.0. CI is configured for Python 3.11/3.14 on Ubuntu/macOS; those remote
matrix jobs have not been run here.

Initial local suite: **65 Python tests passed** with ResourceWarnings promoted to
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
reference checkout is absent; its two real MemoryManager tests and native
provider-discovery check are then skipped.
See [compatibility](compatibility.md) for the exact upstream commits and surfaces.

## Evaluation limits

The six-case [synthetic replay](../benchmarks/results.json) covers current and
historical facts, semantic recall, scope denial, unsupported completion, and
abstention. bigfeels retrieved all three expected items with zero forbidden
exposures and all three expected abstentions. These hand-authored fixtures and
fixture embeddings do not establish general answer quality or superiority over
another product. This replay uses fixture responses and makes no model calls.

A disposable 1,000-record local corpus took 1.548 seconds to populate. Ten
keyword retrievals measured 21.457–22.941 ms (mean 21.83 ms) on this machine.
These are a small local observation, not an SLA or a vector-scale benchmark.

No live Hermes profile or OpenClaw Gateway was modified or used to capture
private conversations. Live host activation, long-running delivery, actual hosted-model
quality/cost, OS daemon installation, remote hosting, and multi-device operation
remain unverified. The package is a local preview, not a published registry release.
