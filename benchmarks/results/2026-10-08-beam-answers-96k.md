# bigfeels BEAM answer quality at 96,000 bytes — 2026-10-08

This is a rerun of `2026-10-08-beam-answers.md` after a second optimization pass. Two things changed: the context budget (96,000 bytes instead of 32,000) and the grading client.

## What changed, and what was tested but not shipped

Shipped:

- **Higher budget ceiling.** Context and search budgets may now go up to 256,000 bytes, against the old 32,000. Defaults and the adapters' automatic-recall limits are unchanged.
- **Lighter search.** Search no longer loads every eligible memory's full text, only byte lengths plus the text of keyed claims.
- **Runner fixes.** The answer runner stops whole timed-out command groups and gained a `--context-order` option.

Tested and not shipped, because none helped:

- **Diversity-aware packing (MMR, λ 0.7 and 0.85).** Source recall at 32,000 B on 100K moved +0.4 and −0.1 points.
- **Oldest-first context.** Graded A/B on 100K through Codex, with both orders on the same 400 questions: +0.7 points [−1.1, +2.7].
- **Pseudo-relevance feedback and nomic task prefixes.** Covered in `2026-10-07-beam-hybrid.md`.

The remaining lever was context size. Source recall at 100K / 500K / 1M rises with the budget:

| Budget | 100K | 500K | 1M |
| --- | ---: | ---: | ---: |
| 32,000 B | 63.5% | 57.7% | 46.5% |
| 96,000 B | 73.1% | 67.4% | 55.7% |
| 128,000 B | 77.7% | 69.6% | 59.5% |

96,000 bytes is about 24,000 tokens, inside the 17,700–27,300-token mean contexts recorded for Hindsight's published run. The median question here received 95,837 bytes in 38 memories.

## Setup

Everything else matches the first run:

- **Memory:** raw-message saves and local `nomic-embed-text` embeddings.
- **Reader:** `gpt-6.1-sol` (medium) through Copilot CLI, using BEAM's released answer prompt.
- **Judge:** `gpt-6-luna` (low), using BEAM's released judge prompt and event-ordering scoring.
- **Questions:** all 1,800 in the 100K, 500K, and 1M tiers.

This time every grade went through Codex CLI, which avoids the GitHub API rate limit Copilot hit last time. To check that the client doesn't bias scores, 200 answers from the first run were re-graded through Codex. They scored 53.3 against 54.0 under Copilot, a difference of −0.6 [−2.7, +1.4], with identical scores on 154 of 200. If anything, grading through Codex is slightly stricter.

## Results

Intervals are 95% conversation-bootstrap intervals. Each difference is paired on the same questions against the first run.

| Tier | First run (32,000 B) | This run (96,000 B) | Difference |
| --- | ---: | ---: | ---: |
| 100K | 54.8 | 56.8 [53.6, 60.3] | +2.0 [−0.0, +4.3] |
| 500K | 51.8 | 53.6 [50.2, 57.1] | +1.9 [−0.1, +4.0] |
| 1M | 51.2 | 53.3 [50.8, 55.7] | +2.1 [+0.5, +3.7] |
| All 1,800 | 52.2 | 54.2 [52.4, 56.0] | +2.0 [+0.9, +3.1] |

| Category | First run | This run |
| --- | ---: | ---: |
| Preference following | 82.4 | 86.3 |
| Information extraction | 74.0 | 80.5 |
| Instruction following | 61.7 | 67.1 |
| Knowledge update | 62.6 | 64.7 |
| Summarization | 49.3 | 54.5 |
| Multi-session reasoning | 48.3 | 50.3 |
| Temporal reasoning | 42.8 | 42.4 |
| Abstention | 42.5 | 38.9 |
| Event ordering (`tau_norm`) | 28.2 | 30.5 |
| Contradiction resolution | 30.3 | 26.8 |

More context helped where evidence had been missing. It slightly hurt abstention and contradiction handling, because there was more loosely related text to answer from.

## Where the remaining gap is

In the first run, questions whose labeled sources were all retrieved still averaged only 61.8. By category:

| Category, all sources retrieved | Score |
| --- | ---: |
| Contradiction resolution | 31 |
| Temporal reasoning | 46 |
| Knowledge update | 64 |
| Information extraction | 85 |

For contradictions, the reader had both conflicting statements yet answered with one. BEAM's released prompt asks for a direct, concise answer. Closing that gap is mostly about how an integration asks its model to use memory, and about extraction that gives bigfeels keyed claims to flag. It is not about finding more text.

Per-question scores are in `2026-10-08-beam-answers-96k.scores.json`.
