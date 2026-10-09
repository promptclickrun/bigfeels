# bigfeels BEAM keyword + semantic retrieval — 2026-10-07

Candidate: branch `feat/recall-pass-2` (keyword and semantic rankings fused by reciprocal rank). Baseline: the same commit without an embedding model, which is the keyword-only ranking from `2026-10-07-beam-retrieval.md`. BEAM data, method, metric, and question set are unchanged from that report: 355 / 629 / 615 source-labeled questions at 100K / 500K / 1M.

Embeddings came from `nomic-embed-text` served locally by Ollama through bigfeels' OpenAI-compatible provider. No memory content left the machine, and no paid model was called. As before, this measures source retrieval, not BEAM answer quality, so it cannot be compared with published answer scores.

The 100K tier was the development set. All fusion choices were made there, and 500K and 1M were used only to accept or reject them.

## Results

Annotated-source recall. Each difference is hybrid minus keyword-only, with a 95% paired conversation-bootstrap interval in percentage points.

| Budget | Tier | Keyword only | Keyword + semantic | Difference [95% interval] |
| ---: | --- | ---: | ---: | ---: |
| 3,200 B | 100K | 42.97% | 49.43% | +6.46 [+3.77, +9.25] |
| 3,200 B | 500K | 41.24% | 45.98% | +4.74 [+2.13, +7.15] |
| 3,200 B | 1M | 30.60% | 36.63% | +6.03 [+4.02, +8.15] |
| 12,800 B | 100K | 47.31% | 56.09% | +8.78 [+5.77, +11.99] |
| 12,800 B | 500K | 46.58% | 51.59% | +5.01 [+2.33, +7.54] |
| 12,800 B | 1M | 32.95% | 40.57% | +7.62 [+5.54, +9.89] |
| 32,000 B | 100K | 57.89% | 63.22% | +5.34 [+2.34, +8.10] |
| 32,000 B | 500K | 53.27% | 57.85% | +4.58 [+1.76, +7.30] |
| 32,000 B | 1M | 38.23% | 46.72% | +8.49 [+6.55, +10.74] |

32,000 bytes is the API maximum, roughly 8,000 tokens. That is close to the ~6,700 retrieved tokens reported for Mem0's BEAM run, and well below Hindsight's 17,000–27,000.

Instruction-following and preference-following questions gained 18–21 points at every budget. Those questions share few words with the message that set the instruction or preference. Information extraction moved −1.3 to +0.9 points.

Median query latency was 77–105 ms with 8 concurrent workers, including the local query-embedding call. Indexing all 112,498 memories took about 75 minutes on this machine.

## External check: LongMemEval_S development slice

The first 100 LongMemEval_S questions were run through `Store` with and without the same local embeddings, at 3,200 bytes.

| | Gold session retrieved | Gold answer text retrieved | Either |
| --- | ---: | ---: | ---: |
| Keyword only | 91 | 61 | 92 |
| Keyword + semantic | 97 | 67 | 99 |

## Rejected during development

- **The existing fixed threshold.** Adding similarity only above 0.65 reached 45.5% at 3,200 B on 100K, against 48.9% for fusion. For this model a typical memory scores about 0.52 and source messages about 0.70.
- **A relative (z-score) floor for semantic candidates.** It did not separate BEAM's unanswerable questions from answerable ones: their top z-scores averaged 3.9 and 3.3. Deciding that nothing is relevant is left to the reader.
- **nomic task prefixes** (`search_query:` / `search_document:`). They lowered fused recall at 3,200 B on 100K from 48.9% to 46.8%.
- **Tie-aware fusion and pseudo-relevance feedback.** Both landed within ±2 points, with no consistent direction.

## Reproduce

```sh
ollama pull nomic-embed-text
python3 -m benchmarks.harness.beam_retrieval --beam-dir /path/to/BEAM/chats \
  --stores /tmp/beam-stores --budgets 3200 12800 32000 \
  --embedding-model nomic-embed-text --embedding-url http://127.0.0.1:11434/v1 \
  --json-out /tmp/beam-hybrid.json
```

Run the same command without `--embedding-model` to get the keyword-only baseline. Then pass `--compare` to print paired differences.
