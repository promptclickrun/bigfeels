# bigfeels evaluation summary — 2026-09-15

Candidate: uncommitted integrated working tree based on `f51179661fec624f3f40fa4221a982df5d70f324`.

## LongMemEval_S development slice

Dataset SHA-256: `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`. The first 100 of 500 questions were used as a declared development slice. The metric is a gold-session retrieval proxy, not answer accuracy or turn-level precision. Context was limited to 3,200 conservative UTF-8 bytes.

| System | Gold-session hits | Proxy recall | Mean retrieval latency |
| --- | ---: | ---: | ---: |
| bigfeels 0.1.0 | 91 / 100 | 0.91 | 22.17 ms |
| simple lexical | 68 / 100 | 0.68 | 13.81 ms |
| curated notes | 12 / 100 | 0.12 | 0.01 ms |
| no memory | 0 / 100 | 0.00 | 0.00 ms |

bigfeels exceeded the simple lexical baseline by 23 percentage points on this development proxy. This is not an answer-quality result.

## LongMemEval_S held-out retrieval slice

The remaining 400 questions, offsets 100 through 499, were frozen before execution and run in four fixed 100-question shards. Input turns were normalized into identical 12,000-character units for every system. Both systems completed all 400 cases with zero ingestion failures.

| System | Gold-session hits | Proxy recall | Weighted mean retrieval latency |
| --- | ---: | ---: | ---: |
| bigfeels 0.1.0 | 351 / 400 | 0.8775 | 30.00 ms |
| simple lexical | 249 / 400 | 0.6225 | 14.14 ms |

bigfeels exceeded the simple lexical baseline by 25.5 percentage points on this held-out proxy. It traded speed for recall and does not establish answer correctness.

## Continuity_V2 trust/capability smoke

On eight frozen synthetic cases, bigfeels executed 8/8 with recall 1.00, precision 1.00, no scorer-detected stale/leakage exposure, and the harness maximum score. The installed Mnemosyne 3.15.1 adapter executed 4/8 and marked four unsupported, scoring 0.783. Different capability coverage means this is not a common-task performance margin.

## Mnemosyne scale boundary

The adapter used installed `mnemosyne-memory 3.15.1`, its public `BeamMemory.remember_batch`, explicit imported trust, disabled LLM/entity extraction, and an isolated `/tmp` database. One LongMemEval_S question completed with a hit. A five-question run exceeded 420 seconds; a ten-question run was stopped without a result; an earlier per-row twenty-question run exited before producing JSON. These are operational throughput observations. No accuracy score was assigned to incomplete runs.

## Claim boundary

The evidence supports saying bigfeels outperformed simple lexical retrieval on the frozen 400-question holdout proxy, outperformed curated notes on the normalized 100-question development proxy, and covered more of this repository's trust/capability smoke than the installed Mnemosyne adapter. It does not support a claim of general answer-quality or general retrieval superiority over Mnemosyne.
