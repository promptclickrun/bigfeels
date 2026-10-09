# bigfeels BEAM answer quality with agent search — 2026-10-08

In earlier runs, the answering model received one fixed block of retrieved memory. Here it searches bigfeels itself through the MCP server, the way an agent uses bigfeels in practice, and chooses its own queries and budgets. This run scored higher on every tier, and on average it cost less per answer than the fixed 96,000-byte context.

## Setup

- **Memory:** the same stores as the earlier runs: raw-message saves, opening-weighted keywords fused with local `nomic-embed-text` embeddings.
- **Reader:** `gpt-6.1-sol` (medium) through Copilot CLI. For each conversation it got bigfeels' MCP server, limited to `memory_context` and `memory_search` with no other tools.
- **Instructions:** these mirror BEAM's released `answer_generation_for_rag` rules ("answer only from memory, do not use internal knowledge, be direct and concise"). They add only that the model should search before answering, search several times for questions spanning several topics or events, and that 16,000 bytes per search is usually enough. See `AGENT_PROMPT` in `benchmarks/harness/beam_answers.py`.
- **Judge:** `gpt-6-luna` (low) through Codex CLI, with BEAM's released rubric prompt and event-ordering scoring. On earlier answers, Codex grading ran 0.6 points stricter than Copilot grading.
- **Questions:** all 1,800 in the 100K, 500K, and 1M tiers. 100K was used to choose this approach; 500K and 1M are held out.
- **Cost:** Copilot's per-session usage figure, in AI units (nano-AIU / 10^9). It covers the reader's input, output, and caching.

The bigfeels code was unchanged during the run, with one exception. A tool-description edit was live in the working tree for 36 seconds while held-out answers were being generated. Up to about 30 of the 1,400 held-out answers may have seen one extra sentence in the tool description, and that sentence repeated the instructions above.

## Results

Each difference is paired on the same questions against the fixed-context runs (`2026-10-08-beam-answers.md` and `-96k.md`). Intervals are 95% conversation-bootstrap intervals.

| Tier | Agent search | vs. fixed 96,000 B | vs. fixed 32,000 B |
| --- | ---: | ---: | ---: |
| 100K | 60.6 [57.0, 64.0] | +3.7 [+0.4, +7.2] | +5.8 [+2.6, +8.9] |
| 500K | 55.8 [52.3, 59.2] | +2.1 [−0.3, +4.8] | +4.0 [+1.6, +6.3] |
| 1M | 55.5 [53.2, 58.0] | +2.3 [+0.5, +4.1] | +4.3 [+2.2, +6.5] |
| All 1,800 | 56.7 [54.9, 58.6] | +2.5 [+1.1, +4.0] | +4.5 [+3.1, +6.0] |

| Reader cost per answer | Mean | Median |
| --- | ---: | ---: |
| Fixed 32,000 B (40-question sample) | 4.85 | 4.80 |
| Fixed 96,000 B (40-question sample) | 8.09 | 8.03 |
| Agent search (all 1,800) | 5.11 | 3.32 |

The agent averaged 3.8 searches per question. It used about 1.5 for single-fact questions and 6 to 8 for summarization, event ordering, and multi-session questions. Its median question read 34 KB of memory.

| Category | Fixed 32,000 B | Fixed 96,000 B | Agent |
| --- | ---: | ---: | ---: |
| Preference following | 82.4 | 86.3 | 85.7 |
| Information extraction | 74.0 | 80.5 | 82.4 |
| Instruction following | 61.7 | 67.1 | 72.1 |
| Knowledge update | 62.6 | 64.7 | 64.9 |
| Summarization | 49.3 | 54.5 | 55.8 |
| Multi-session reasoning | 48.3 | 50.3 | 53.5 |
| Temporal reasoning | 42.8 | 42.4 | 44.4 |
| Abstention | 42.5 | 38.9 | 40.8 |
| Event ordering (`tau_norm`) | 28.2 | 30.5 | 34.9 |
| Contradiction resolution | 30.3 | 26.8 | 32.9 |

## Changes that followed

- **Shipped: tool descriptions that tell agents how to search.** The descriptions now say to search again with different queries for questions spanning several topics, sessions, or events, and that 8,000–16,000 bytes per call suits most questions. `memory_search` is described as the provenance-inspection tool. In this run, agents called `memory_search` for 96% of searches and so paid for full provenance records. With the new descriptions on 100K, they used `memory_context` for 93% of searches and cost 7% less (mean 4.11 against 4.41). Accuracy moved −1.3 [−3.5, +1.2], which is within run-to-run variation.
- **Not shipped: memory-use guidance in the context header.** The guidance said to prefer later updates, flag contradictions, and say a detail is not recorded. Given a fixed 96,000-byte context on 100K, it scored −0.5 [−2.9, +2.1]. Abstention and knowledge update rose 7.5 points each, while instruction following, summarization, and temporal reasoning fell.

## Published results, for orientation

| System | 100K | 500K | 1M | Notes |
| --- | ---: | ---: | ---: | --- |
| BEAM paper RAG / LIGHT | 32.3 / 35.8 | 33.0 / 35.9 | 30.7 / 33.6 | Llama-4-Maverick reader |
| **bigfeels, agent search** | **60.6** | **55.8** | **55.5** | GPT-6.1 Sol / GPT-6 Luna, raw-message saves |
| Honcho | 63.0 | 64.9 | 63.1 | Agent with tools; LLM ingestion |
| Mem0 managed platform | — | — | 64.1 | GPT-5 / GPT-5 |
| Mnemosyne v3.0.0 | 65.2 | — | — | Llama 3.3 70B / DeepSeek V4 Flash |
| Hindsight, historical | 73.4 | 71.1 | 73.9 | Gemini 3.1 Pro / Gemini 2.5 Flash Lite |

Readers, judges, context sizes, and ingestion differ, so the table orients rather than ranks. The published systems extract or structure memories with a model at ingestion. bigfeels stored raw messages here.

## Reproduce

Build the stores with `beam_retrieval.py --embedding-model nomic-embed-text --embedding-url http://127.0.0.1:11434/v1`, then run:

```sh
python3 -m benchmarks.harness.beam_answers --beam-dir BEAM/chats --beam-src BEAM \
  --stores /tmp/beam-stores --out /tmp/beam-agent --agent \
  --embedding-model nomic-embed-text --embedding-url http://127.0.0.1:11434/v1 \
  --reader 'copilot --output-format json --disable-builtin-mcps --model gpt-6.1-sol --reasoning-effort medium --additional-mcp-config @{mcp_config} --allow-tool bigfeels --available-tools bigfeels-memory_context bigfeels-memory_search' \
  --judge 'JUDGE COMMAND'
```

Per-question scores, search counts, bytes read, and cost are in `2026-10-08-beam-answers-agent.scores.json`.
