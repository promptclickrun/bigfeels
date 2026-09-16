# Verification

The integrated 0.1.0 candidate was verified locally on 2026-09-15 without activating a live memory provider or using private conversations.

## Reproduce the relevant checks

```sh
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests -v
node --test adapters/openclaw/test.mjs adapters/openclaw/tools.test.mjs tests/*.test.mjs
PYTHONPATH=src python3 benchmarks/replay.py --output benchmarks/results.json
python3 -m build
```

The root `tests/*.test.mjs` selection is intentional. It includes scheduler, delayed retry, and lifecycle-cancellation regressions that the old default command omitted.

For an interface smoke without automatic capture:

1. run CLI remember/search/inspect/correct;
2. run forget-preview and review the complete closure;
3. execute forget with its plan token and confirm inspect fails;
4. initialize MCP, list tools, and repeat the explicit lifecycle;
5. inspect process/status output without treating queued work as learned memory;
6. start the HTTP service, use the browser workspace, and confirm deletion always previews before token-confirmed execution.

## Integrated results

- **102 Python tests passed** with `ResourceWarning` promoted to errors.
- **21 Node tests passed** through `npm run test`.
- Core regressions passed for compact recall, repeated evidence, compatible claims, caller-attested outcomes, abstention, delayed corrections, invalid extraction, deletion closure/stale and expired plans, and additive migration.
- A malformed nonempty SQLite database was rejected without changing its bytes; an exact pre-diagnostics v1 schema migrated additively.
- OpenClaw drained more than eight jobs, resumed delayed retries, and stopped new capture writes after both session and gateway termination.
- CLI, direct-local MCP, HTTP-backed MCP, HTTP, browser UI, Hermes tools, and OpenClaw tools use the same preview/token deletion contract. The UI's first click was verified to preview collateral content and submit the exact returned token.
- `python -m build` produced `bigfeels_mem-0.1.0.tar.gz` and `bigfeels_mem-0.1.0-py3-none-any.whl` with setuptools 84. Generated evaluation reports were excluded from the source distribution, and the wheel contained runtime files only.
- A fresh temporary virtual environment installed the wheel with `--no-deps`; the installed CLI completed remember/search/correct/preview/forget, and installed MCP completed save/recall with all 11 tools present.

## Comparative evaluation

The frozen eight-case Continuity_V2 smoke ran bigfeels, installed `mnemosyne-memory 3.15.1`, no memory, curated notes, and simple lexical retrieval in isolated temporary stores. bigfeels executed 8/8 cases with the harness maximum score. Mnemosyne executed 4/8 because the adapter correctly reported unsupported semantics instead of emulating them.

On the normalized LongMemEval_S first-100 development slice, bigfeels produced 91/100 gold-session proxy hits versus 68/100 for simple lexical and 12/100 for curated notes. On the frozen remaining 400 questions, bigfeels produced 351/400 hits (0.8775) versus 249/400 (0.6225) for simple lexical, a 25.5-point advantage with zero ingestion failures. bigfeels averaged 30.00 ms retrieval versus 14.14 ms for the simpler baseline.

The Mnemosyne adapter used its documented `BeamMemory.remember_batch` path with imported trust, disabled LLM/entity extraction, and an isolated `/tmp` database. One LongMemEval_S question completed with a hit. Five and ten-question runs did not complete within the execution windows, so no incomplete Mnemosyne accuracy score was assigned. Raw bounded results and claim limits are in `benchmarks/results/2026-09-15-evaluation.md`.

## Limits

Fixture extraction and embeddings do not establish live model quality, entitlement, cost, or provider retention. The LongMemEval metric is gold-session retrieval, not answer accuracy or turn-level precision. The comparative evidence supports superiority over the simple lexical baseline on the named held-out proxy, not general answer-quality or general retrieval superiority over Mnemosyne. Source compatibility does not establish live Hermes/OpenClaw activation, and configured CI does not prove hosted matrix execution. No package publication, live profile activation, gateway restart, or private conversation capture occurred.
