# bigfeels BEAM answer quality — 2026-10-08

This is the first model-answered, model-graded BEAM run for bigfeels, scored the way BEAM's released report scores answers. Unlike the source-recall diagnostics, these numbers measure the same thing as published BEAM results. They are still not a controlled head-to-head, because readers, judges, and context sizes differ across systems.

## Setup

- **Memory:** bigfeels `feat/recall-pass-2` (opening-weighted keyword ranking fused with semantic ranking). Every BEAM message was saved through `remember`, the same raw-save ingestion as `2026-10-07-beam-retrieval.md`, with no extraction. Embeddings came from `nomic-embed-text` via local Ollama.
- **Context:** the native `context` API at its 32,000-byte maximum (median 31,827 bytes, about 8,000 tokens), joined in bigfeels' rank order.
- **Reader:** `gpt-6.1-sol` at medium reasoning, using BEAM's released `answer_generation_for_rag` prompt.
- **Judge:** `gpt-6-luna` at low reasoning. It used BEAM's released `unified_llm_judge_base_prompt` for every rubric item. For event ordering it used BEAM's `llm_equivalence` alignment and normalized Kendall tau.
- **Scoring:** as in BEAM's `report_results.py`, each question's score is its mean rubric grade (`tau_norm` for event ordering). A tier's score is the mean over its questions.
- **Questions:** all 1,800 in the 100K, 500K, and 1M tiers, including abstention and unlabeled questions. The 10M tier was not run.
- **Calls:** all model calls went through subscription CLIs with tools, memories, hooks, and plugins disabled. All 1,800 answers and 1,495 gradings ran through GitHub Copilot CLI. After Copilot hit a GitHub API rate limit, the remaining 305 1M gradings ran through Codex CLI with the same judge model. BEAM revision `b2da22e`.

## Results

Intervals are 95% conversation-bootstrap intervals.

| Tier | Questions | Score [95% interval] |
| --- | ---: | ---: |
| 100K | 400 | 54.8 [51.5, 58.1] |
| 500K | 700 | 51.8 [48.9, 54.7] |
| 1M | 700 | 51.2 [48.9, 53.4] |
| All three | 1,800 | 52.2 |

| Category (all tiers) | Score |
| --- | ---: |
| Preference following | 82.4 |
| Information extraction | 74.0 |
| Knowledge update | 62.6 |
| Instruction following | 61.7 |
| Summarization | 49.3 |
| Multi-session reasoning | 48.3 |
| Temporal reasoning | 42.8 |
| Abstention | 42.5 |
| Contradiction resolution | 30.3 |
| Event ordering (`tau_norm`) | 28.2 |

## Published results, for orientation

These values are from the 2026-10-05 independent study's verified extracts, as mean grades × 100.

| System | 100K | 500K | 1M | Reader / judge |
| --- | ---: | ---: | ---: | --- |
| BEAM paper RAG | 32.3 | 33.0 | 30.7 | Llama-4-Maverick |
| BEAM paper LIGHT | 35.8 | 35.9 | 33.6 | Llama-4-Maverick |
| **bigfeels, this run** | **54.8** | **51.8** | **51.2** | GPT-6.1 Sol / GPT-6 Luna |
| Honcho | 63.0 | 64.9 | 63.1 | Claude Haiku 4.5 / unrecorded |
| Mem0 managed platform | — | — | 64.1 | GPT-5 / GPT-5 |
| Mnemosyne v3.0.0 | 65.2 | — | — | Llama 3.3 70B / DeepSeek V4 Flash |
| Hindsight, historical | 73.4 | 71.1 | 73.9 | Gemini 3.1 Pro / Gemini 2.5 Flash Lite |

Readers, judges, context sizes (Hindsight used about 17,000–27,000 tokens), ingestion pipelines, and agent loops all differ. The table orients; it does not rank. Unlike the published systems, bigfeels here had no extraction or summarization at ingestion, no agent loop, and no query rewriting.

## What the categories point to

These are hypotheses for later work, not tested here.

- **Contradiction resolution (30.3).** bigfeels detects conflicts only between keyed claims. Raw messages carry no keys, so its `disputed` warnings never fired. Extraction with keys would exercise that path.
- **Event ordering (28.2) and temporal reasoning (42.8).** Context was given in relevance order rather than chronological order.
- **Abstention (42.5).** The reader often answered from loosely related context instead of declining.

## Reproduce

Build the stores with `beam_retrieval.py --embedding-model nomic-embed-text --embedding-url http://127.0.0.1:11434/v1`, then run:

```sh
python3 -m benchmarks.harness.beam_answers --beam-dir BEAM/chats --beam-src BEAM \
  --stores /tmp/beam-stores --out /tmp/beam-answers \
  --embedding-model nomic-embed-text --embedding-url http://127.0.0.1:11434/v1 \
  --reader 'copilot -s --available-tools --disable-builtin-mcps --model gpt-6.1-sol --reasoning-effort medium' \
  --judge 'copilot -s --available-tools --disable-builtin-mcps --model gpt-6-luna --reasoning-effort low'
```

Per-question scores are in `2026-10-08-beam-answers.scores.json`.
