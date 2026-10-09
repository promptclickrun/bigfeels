# bigfeels BEAM agent search without embeddings — 2026-10-09

The best BEAM run so far had the reader search bigfeels over MCP, with keyword ranking fused with local `nomic-embed-text` embeddings (`2026-10-08-beam-answers-agent.md`). This run repeats it on the 100K tier with no embedding provider, so every search is SQLite FTS5 keyword ranking only. It scored the same.

## Setup

- **Code:** `main` at `2f78a34`, including the current tool descriptions that steer agents to `memory_context`.
- **Memory:** the same raw-message stores as the earlier runs. Every BEAM message was saved through `remember`, with no model at save time. The stores still hold vectors, but no query was embedded.
- **Reader:** `gpt-6.1-sol` (medium) through Copilot CLI, limited to `memory_context` and `memory_search`.
- **Judge:** `gpt-6-luna` (low) through Codex CLI, with BEAM's released rubric and event-ordering scoring.
- **Questions:** all 400 in 100K.

## Results

Differences are paired on the same questions, with 95% conversation-bootstrap intervals.

| Run (100K, agent search) | Score | Keyword only vs. this run |
| --- | ---: | ---: |
| **Keyword only (this run)** | **59.5 [56.1, 63.0]** | — |
| Keyword + embeddings, current tool descriptions (raw variant in `2026-10-08-beam-extraction.md`, PR #24) | 59.7 | −0.2 [−2.7, +2.3] |
| Keyword + embeddings, earlier tool descriptions (`2026-10-08-beam-answers-agent.md`) | 60.6 | −1.0 [−3.3, +1.2] |

Both differences are within run-to-run variation, which is about ±1 point. The reader averaged 3.1 searches, read a median of 34 KB, and cost a mean of 4.31 AIU per answer (median 3.25).

| Category | Keyword + embeddings (59.7) | Keyword only |
| --- | ---: | ---: |
| Abstention | 52.5 | 50.0 |
| Contradiction resolution | 37.2 | 36.6 |
| Event ordering (`tau_norm`) | 34.7 | 35.6 |
| Information extraction | 90.5 | 88.5 |
| Instruction following | 70.0 | 63.1 |
| Knowledge update | 67.5 | 72.5 |
| Multi-session reasoning | 62.6 | 59.1 |
| Preference following | 87.5 | 86.9 |
| Summarization | 48.1 | 49.8 |
| Temporal reasoning | 46.2 | 53.1 |

## What this means

When an agent writes its own queries and searches several times, embeddings added no measurable answer quality on BEAM 100K. The source-recall diagnostic still favors embeddings for a single fixed query (49.4% against 43.0% at 3,200 bytes), so they matter more where one search must find everything, such as automatic per-turn recall. The 500K and 1M tiers were not run.

Reproduce with `beam_answers.py --agent` as in `2026-10-08-beam-answers-agent.md`, leaving out `--embedding-model`. Per-question scores, searches, bytes read, and cost are in `2026-10-09-beam-keyword-only.scores.json`.
