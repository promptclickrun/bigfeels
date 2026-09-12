# Continuity replay

Run `PYTHONPATH=src python3 benchmarks/replay.py --output benchmarks/results.json`.
The report compares retrieval policies on the same synthetic evidence and queries.
It uses no paid model calls, private conversations, or stochastic judge.

Policies: no memory; curated Markdown (all owner notes, budget limited); simple
lexical+vector retrieval without temporal revision handling; bigfeels. The tiny
fixture embedding is explicitly deterministic and hand-authored. It tests index
wiring, not semantic model quality. All policies use identical context budgets.

Metrics are exact expected fact coverage, stale/forbidden fact exposure, supported
citation rate, abstention, bytes returned, latency, and provider request counts.
These are retrieval regressions, NOT an answer-quality benchmark or LongMemEval
score. No performance superiority claim follows from this small designed fixture.
Before a stable release, add a held-out real-workload corpus and run the same
answering model and token budget for every baseline, with human-adjudicated
outcomes and costs from actual provider usage.
