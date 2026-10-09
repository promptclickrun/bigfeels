# bigfeels BEAM with model-extracted facts — 2026-10-08

Every system that publishes a higher BEAM score than bigfeels runs a model over conversations at ingestion. This run tested whether bigfeels' own extraction pipeline, applied to BEAM's user messages, closes that gap. Results are on the 100K development tier, using agent search over MCP. It did not close the gap.

## Setup

- **Extraction.** Each of the 2,548 user messages in 100K was observed as evidence, dated by its BEAM time anchor. bigfeels' batched extraction (`Store.process_batch`, 12 messages per call) sent them to `gpt-6-luna` through Copilot CLI. That took 221 calls and about 3 minutes, against 2,548 calls one message at a time.
- **Trust rules.** Unchanged. A claim becomes active memory only when it is user-stated and its quote is found in its own message. If the quote leaves out part of its sentence, the whole source sentence is stored, so no qualifier or negation is lost.
- **Extraction output.** About 900 active facts, most of them keyed, with 79 marked disputed by keyed-conflict detection. Raw message memories stayed in place, and new facts were embedded with local `nomic-embed-text`.
- **Real dates.** Separately, raw message memories were re-dated from their placeholder sequence times to BEAM's time anchors, the date a real deployment would record.
- **Answering and grading.** Reader `gpt-6.1-sol` with agent search over MCP; judge `gpt-6-luna`, using BEAM's released rubric prompt and event-ordering scoring. All four variants ran on the same 400 questions.

## Results

Each difference is paired against raw-only, with a 95% conversation-bootstrap interval.

| Variant | Score | vs. raw only |
| --- | ---: | ---: |
| Raw messages | 59.7 | — |
| Raw + real dates | 58.7 | −1.0 [−3.1, +1.0] |
| Raw + extracted facts | 58.0 | −1.7 [−3.8, +0.5] |
| Facts + real dates | 59.7 | +0.0 [−1.9, +1.9] |

No variant is distinguishable from raw messages alone. Earlier raw-only agent runs on the same questions scored 60.6 and 59.3, so run-to-run variation is about ±1 point.

| Category | Raw | Raw + dates | Facts | Facts + dates |
| --- | ---: | ---: | ---: | ---: |
| Abstention | 52.5 | 43.8 | 50.0 | 60.0 |
| Contradiction resolution | 37.2 | 34.4 | 33.4 | 32.8 |
| Event ordering (`tau_norm`) | 34.7 | 37.2 | 34.1 | 36.5 |
| Information extraction | 90.5 | 89.1 | 87.4 | 87.2 |
| Instruction following | 70.0 | 76.9 | 72.5 | 71.9 |
| Knowledge update | 67.5 | 67.5 | 62.5 | 62.5 |
| Multi-session reasoning | 62.6 | 63.2 | 61.6 | 63.6 |
| Preference following | 87.5 | 84.4 | 84.4 | 86.2 |
| Summarization | 48.1 | 46.8 | 49.0 | 47.6 |
| Temporal reasoning | 46.2 | 43.8 | 45.0 | 48.8 |

## Why extraction did not help here

Under bigfeels' trust rules, extraction can only add grounded restatements of what the user said. BEAM users write long run-on sentences, and the extractor usually quoted only part of one. So most "facts" became copies of whole source sentences that the raw messages already contained. Claims inferred from assistant replies, and anything the model concluded rather than quoted, stay as review candidates, which recall never returns.

The higher-scoring systems store model-written facts and summaries directly. That is the main remaining design difference, and changing it is a trust-model decision rather than a retrieval fix.

The batched extraction path still pays for itself whenever extraction is configured: it is about 12 times fewer model calls for the same checked output.

Reproduce with `benchmarks/harness/beam_extract.py` on a copy of the stores, then `beam_answers.py --agent`.
