# Bigfeels Evaluation Harness

Host-neutral comparative evaluation harness for `bigfeels-mem` and alternative memory architectures (`no_memory`, `curated_notes`, `simple_lexical`, and installed `mnemosyne`).

All benchmarks run zero-cost and use isolated ephemeral `/tmp` databases. The Mnemosyne lane never opens the live profile database.

## Mnemosyne ingestion semantics

The benchmark worker uses the installed Mnemosyne 3.15.1 public `BeamMemory.remember_batch` API with `veracity="imported"`, `force_veracity=True`, `trust_tier="IMPORTED"`, and both extraction modes disabled. The adapter buffers remembers in input order, preserves each operation's step ID, and flushes at a bounded 512-item batch or before every retrieve, correct, forget, restart, stats, and teardown boundary. LongMemEval explicitly flushes once after each question's haystack ingestion, so batch errors are reported as ingestion failures rather than hidden as retrieval misses.

The worker reports command, error type, batch size, and EOF/protocol diagnostics as structured JSON. It uses the same session ID across restart so correction and retrieval tests continue to address the same isolated records.

## Directory structure

- `harness/base_adapter.py`: abstract adapter contract and normalized result models.
- `harness/corpus.py`: deterministic synthetic corpus generator and frozen checksums.
- `harness/baselines.py`: no-memory, curated-notes, and lexical baselines.
- `harness/bigfeels_adapter.py`: direct adapter over isolated SQLite paths.
- `harness/mnemosyne_worker.py`: isolated subprocess for installed Mnemosyne BeamMemory.
- `harness/mnemosyne_adapter.py`: buffered host adapter and flush barriers.
- `harness/scorer.py`: trust and retrieval scoring.
- `harness/runner.py`: synthetic comparative benchmark.
- `harness/longmemeval_runner.py`: cached/local LongMemEval retrieval runner.

## Reproducible commands

Synthetic comparative benchmark:

```bash
python3 -m benchmarks.harness.runner \
  --corpus-dir benchmarks/harness/data \
  --json-out benchmarks/harness/results.json \
  --md-out benchmarks/harness/report.md \
  --budget 3200
```

Cached LongMemEval Mnemosyne-only probes:

```bash
python3 -m benchmarks.harness.longmemeval_runner \
  --dataset /tmp/longmemeval_cache/longmemeval_s_cleaned.json \
  --systems mnemosyne --limit 5 --budget 3200 \
  --json-out /tmp/longmemeval_mnemo_5.json

python3 -m benchmarks.harness.longmemeval_runner \
  --dataset /tmp/longmemeval_cache/longmemeval_s_cleaned.json \
  --systems mnemosyne --limit 10 --budget 3200 \
  --json-out /tmp/longmemeval_mnemo_10.json
```

Capability gaps are recorded as unsupported, not silent passes. The synthetic trust gates reject stale exposure and cross-space leakage. LongMemEval's reported metric is a gold-session retrieval proxy, not answer accuracy or turn-level precision.
