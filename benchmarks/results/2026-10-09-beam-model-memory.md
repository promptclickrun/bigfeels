# bigfeels BEAM with model-written memory — 2026-10-09

This run tested bigfeels' new default: a model writes the memory from captured conversation. Results are on the 100K development tier, with agent search over MCP. On BEAM, model-written memory alone scored lower than recall over raw messages. Adding it beside raw messages did not raise the score either.

## Setup

- **Capture.** Each of the 5,094 user and assistant messages in 100K was observed as evidence, dated by its BEAM time anchor. This matches the new Hermes and OpenClaw default of capturing user and assistant turns.
- **Extraction.** bigfeels' batched extraction ran under the `model` memory policy, with up to 8 messages per call, the hosts' limit. The extractor was `gpt-6-luna` (low reasoning) through Copilot CLI. It used about 640 calls and 15 minutes at 8 concurrent calls. For each batch, the extractor saw the current values of keys named in the batch plus the 60 most recent keys.
- **Output.** 4,410 memories: 803 episode summaries and 841 inferred claims. 413 claims were keyed; 43 of them were disputed and 28 were closed by a stated update. A trial on one conversation set the prompt. It cut disputed claims from 49 of 67 to 4 of 218, and raw-sentence fallbacks from 18% to 6%.
- **Variants.**
  - **Model memory only:** a fresh store, which is what a host integration recalls by default.
  - **Raw + model memory:** the raw-message stores from earlier runs with the same extracted rows added.

  New memories were embedded with local `nomic-embed-text`.
- **Answering and grading.** Unchanged from `2026-10-08-beam-answers-agent.md`. The reader was `gpt-6.1-sol` (medium) with `memory_context` and `memory_search` over MCP. The judge was `gpt-6-luna` (low) with BEAM's released rubric and event-ordering scoring.

## Results

Differences are paired on the same 400 questions, with 95% conversation-bootstrap intervals.

| Variant | Score | vs raw messages (59.7) | Mean cost (AIU) | Searches |
| --- | ---: | ---: | ---: | ---: |
| Model memory only | 54.5 [51.9, 57.3] | −5.2 [−7.8, −2.5] | 4.06 | 2.4 |
| Raw + model memory | 57.9 [54.9, 60.8] | −1.8 [−4.1, +0.5] | 3.94 | 2.6 |

The raw-message baseline is the raw variant of `2026-10-08-beam-extraction.md`, which used the same tool descriptions. Against the earlier raw agent run (60.6), the differences are −6.1 and −2.6.

| Category | Raw | Model only | Raw + model |
| --- | ---: | ---: | ---: |
| Abstention | 52.5 | 57.5 | 50.0 |
| Contradiction resolution | 37.2 | 33.1 | 33.1 |
| Event ordering (`tau_norm`) | 34.7 | 33.0 | 33.9 |
| Information extraction | 90.5 | 71.6 | 87.3 |
| Instruction following | 70.0 | 56.2 | 65.6 |
| Knowledge update | 67.5 | 57.5 | 67.5 |
| Multi-session reasoning | 62.6 | 56.6 | 60.6 |
| Preference following | 87.5 | 83.1 | 81.9 |
| Summarization | 48.1 | 47.2 | 47.5 |
| Temporal reasoning | 46.2 | 48.8 | 51.9 |

## Why model memory alone scored lower

Most lost answers had the needed fact in memory. The reader still answered wrongly for three reasons:

- **An older value won.** For example, both "April 20" and "a new April 25 deadline" were stored on the same day without a shared key, so no update closed the older one.
- **A standing instruction was stored but not retrieved.** "The user wants code snippets syntax-highlighted" did not rank for a question about implementing login.
- **A change was stored as tentative.** "Considering moving the call to April 22" was stored where the conversation later settled on that time.

Detail questions about the assistant's own recommendations lost the most, because summaries drop steps that raw messages keep. The reader also searched less often (2.4 searches, against 3.8 in the raw agent run), apparently treating compact facts as complete.

## What this means

On BEAM, with this reader, recall over raw messages remains the strongest single source. Model-written memory adds structure: summaries, labelled inferences, updates, and conflict flags. It did not add accuracy at either the model-only or the combined setting. The 500K and 1M tiers were not run, because the variant did not beat the baseline on the development tier.

Reproduce with `benchmarks/harness/beam_extract.py --combine RAW OUT`, then `beam_answers.py --agent` on each store directory. Per-question scores, searches, bytes read, and cost for both variants are in `2026-10-09-beam-model-memory.scores.json`.
