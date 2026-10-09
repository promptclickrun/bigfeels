# bigfeels BEAM source-retrieval diagnostic — 2026-10-07

Baseline: `main` at `0e403155d5bb35f96176570023703f2507574226`. Candidate: `9dde89f` (opening-weighted ranking on the persistent recall index). BEAM data: `mohammadtavakoli78/BEAM` at `b2da22eac88bb0874c64665f13457eb99835774a`, the 100K, 500K, and 1M tiers. The truncated chat is used when present, as in the released runner.

## Method

`benchmarks/harness/beam_retrieval.py` saves every user and assistant message through the native `remember` API into one store per conversation, with a short provenance header and 12,000-character chunks. It then asks each probing question through the native `context` API. Only the question text reaches retrieval. The metric is annotated-source recall: the share of a question's labeled source messages that appear in the returned context. Abstention questions, questions without source labels, and questions citing duplicated source IDs are excluded, which leaves 355 / 629 / 615 questions. This matches the independent 2026-10-05 study's denominators.

This measures evidence retrieval in the lexical-only, raw-message configuration. It is not a BEAM answer score and cannot be compared with published vendor results.

The 100K tier was the development set for design choices. The 500K and 1M tiers were held out and used only to accept or reject each choice. Intervals are 95% paired conversation-bootstrap intervals for the difference in percentage points.

## Results

| Budget | Tier | Recall before | Recall after | Difference [95% interval] | Query p50 before → after | Query p95 before → after |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 3,200 B | 100K | 40.63% | 42.97% | +2.34 [−0.08, +4.92] | 41.8 → 5.5 ms | 52.4 → 8.2 ms |
| 3,200 B | 500K | 38.23% | 41.24% | +3.01 [+1.21, +4.88] | 167.2 → 18.7 ms | 258.5 → 32.1 ms |
| 3,200 B | 1M | 27.01% | 30.60% | +3.59 [+2.34, +4.92] | 323.4 → 35.1 ms | 443.4 → 61.4 ms |
| 12,800 B | 100K | 47.01% | 47.31% | +0.30 [−2.16, +2.27] | 41.9 → 5.7 ms | 52.1 → 9.2 ms |
| 12,800 B | 500K | 40.06% | 46.58% | +6.52 [+4.75, +8.36] | 166.1 → 18.8 ms | 262.9 → 34.1 ms |
| 12,800 B | 1M | 27.66% | 32.95% | +5.29 [+3.37, +7.28] | 324.0 → 34.9 ms | 450.6 → 63.8 ms |

Recall rose in every question category at both budgets. Event ordering and summarization stay below 9%: they cite many messages spread across a conversation, which a small lexical budget cannot cover. Latency is from 8 worker processes on an Apple-silicon Mac and is descriptive only.

The independent study measured 41.79% / 38.78% / 26.86% for the baseline at 3,200 bytes. This harness measures 40.63% / 38.23% / 27.01% with a different provenance header.

## External check: LongMemEval_S

The same candidate ran the existing LongMemEval_S retrieval proxy (dataset SHA-256 `d6f21ea9…c3a442`, 3,200 bytes).

| | Dev hits (first 100) | Held-out hits (400) | Gold-session retrieved | Gold answer text retrieved | Mean latency |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 91 | 351 | 436 | 174 | 37.4 ms |
| Candidate | 92 | 348 | 436 | 167 | 5.7 ms |

Gold-session retrieval is unchanged. Gold answer text appeared in 13 more and 20 fewer cases. That difference is not significant on 500 questions, but it is a small loss on this proxy.

## Rejected during development

- Ranking full memories by coverage or BM25 without weighting openings lowered BEAM recall (100K: 27.5% and 35.0%) and LongMemEval (419 hits).
- Rare-word (IDF) weighting did not help consistently.
- Breaking ties toward smaller memories raised 100K recall by 3.3 points but lowered 1M by 1.0 point at 3,200 bytes. It was not adopted.
- Returning query-focused excerpts of oversized memories lowered recall by displacing whole smaller memories, and adding them only in leftover budget changed nothing.

## Reproduce

```sh
git clone --filter=blob:none --sparse https://github.com/mohammadtavakoli78/BEAM && cd BEAM
git checkout b2da22eac88bb0874c64665f13457eb99835774a
git sparse-checkout set --no-cone '/chats/100K/' '/chats/500K/' '/chats/1M/'
cd /path/to/bigfeels
python3 -m benchmarks.harness.beam_retrieval --beam-dir /path/to/BEAM/chats \
  --stores /tmp/beam-stores --json-out /tmp/beam-candidate.json
```

Run the baseline commit into a separate `--stores` directory, then pass `--compare baseline.json` to print paired differences.
