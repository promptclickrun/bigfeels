# Review hardening and comparative evaluation

## Authorized scope and baseline

The team is implementing the recommendations from the review of `f51179661fec624f3f40fa4221a982df5d70f324`, then running comparative benchmarks against the actual Mnemosyne implementation, no memory, curated notes, and a simple retrieval baseline. Keep the existing SQLite/native-host architecture. This does not authorize publishing, switching a live memory provider, importing personal conversations, editing host runtimes/profiles, or creating paid infrastructure.

The default agent owns integration, all regression tests and execution, final review, UI verification, benchmark design/execution, and documentation. The user explicitly assigned both Doofy and Forge as implementation collaborators for this task. Coordinate through Team Code Alpha. No hidden replacement coding agents.

## Exclusive writable paths

All worktrees are siblings under the workspace's `repos` directory.

- **Default / integration:** `bigfeels-mem`, branch `feat/initial-release`. Owns `tests/**`, every `*.test.*` and `test.mjs`, `benchmarks/**`, `docs/**`, root documentation, `src/bigfeels_mem/static/**`, `src/bigfeels_mem/mcp.py`, `cli.py`, `server.py`, packaging/CI and final integration. Does not write collaborator-owned production files until handoff.
- **Doofy / core:** `bigfeels-mem-doofy`, branch `fix/review-core`. Owns `src/bigfeels_mem/store.py`, `schema.py`, `retrieval.py`, `providers.py`, and any new core-only helper modules explicitly named in the handoff. No adapter, UI, test, benchmark, or documentation writes.
- **Forge / adapters and scheduling:** `bigfeels-mem-forge`, branch `fix/review-adapters`. Owns `src/bigfeels_mem/local.py`, `rpc.py`, production files under `adapters/hermes/` and `adapters/openclaw/`, plus the root `openclaw.plugin.json` if needed for registered tools. Excludes all test files, README files, core storage/retrieval/provider files, and other root files.

Collaborators may inspect all source and suggest tests. Default owns test authoring/execution and acceptance. Return changed paths, design decisions, dependencies, and caveats in this chat. Leave a completed worktree and identify HEAD; do not merge, publish, install into a host, or alter Git identity. No commits are required for handoff because the existing author identity needs release-time reconciliation. Default can integrate exact reviewed file changes.

## Acceptance ledger

- [ ] R1: Ordinary paragraph-length evidence and repeatedly supported facts remain usable at the native default recall budget. Context uses a compact projection and bounded evidence references; inspect/export retain full lineage. Oversized relevant records have explicit safe handling and diagnostic visibility.
- [ ] R2: Equivalent claims in differently worded source messages do not manufacture contradictions. Separate claim comparison from source text while preserving negation, conditions, subject scope, and evidence. Uncertain synthesis remains candidate knowledge.
- [ ] R3: Explicit tools cannot present arbitrary caller assertions as independently verified outcomes merely because a tool evidence ID exists. Prefer a clearly caller-attested label or rejection over an invented semantic verifier. Handle legacy stored labels honestly and preserve existing data.
- [ ] R4: Native default retrieval handles natural questions, common-word noise, the reviewed bicycle/cycling example, unrelated-query abstention, and deterministic budgets. Avoid corpus-specific benchmark tuning. Optional semantic features must remain optional and their limitations explicit.
- [ ] R5: Pending/retried extraction cannot resurrect a corrected claim. Other useful claims from the same source remain extractable. Preserve temporal correction history across restart and export/restore.
- [ ] R6: Deletion previews show all affected memories and evidence counts before confirmation. Guard against a changed deletion plan. Shared-evidence closure remains privacy-safe and cannot silently broaden after confirmation.
- [ ] R7: OpenClaw continues draining bounded batches while the integration is active, retries delayed work without requiring another user turn, and cancels safely at lifecycle end. Keep foreground work bounded. Expose backlog age and pending/failed state.
- [ ] R8: Invalid candidate output is distinguishable from a valid empty extraction. Preserve partial successes, record safe accepted/rejected counts and reasons, and use bounded retry or inspectable failure rather than silently marking invalid-only learning done.
- [ ] R9: Schema changes preserve existing preview databases and v1 exports. No reset or destructive migration. Update every affected adapter/tool/UI/CLI contract.
- [ ] R10: Run focused regressions and complete affected suites against the integrated candidate; inspect deletion UI behavior, stale plans, and first-tap confirmation. Exercise real local storage and native helper processes with fixture model responses.
- [ ] B1: Freeze a separate held-out evaluation corpus before tuning the candidate. Include long messages, paraphrases, repeated evidence, delayed corrections, scope denial, deletion, multi-fact recall, distractors, and abstention.
- [ ] B2: Execute the actual installed or verified Mnemosyne package against isolated fresh data, preserving its provenance/version and supported public API. Do not substitute a toy baseline bearing its name or use the live memory tools/store.
- [ ] B3: Run bigfeels, Mnemosyne, no memory, curated notes, and a simple retrieval baseline with equal inputs and context budgets. Separate automatic-capture and explicit-memory tracks where capabilities differ.
- [ ] B4: Where model-backed answering/extraction is exercised, use the same supported model route and record real usage, latency, failures, and limits. Default regression tests remain offline. Do not fabricate model responses or silently enable a paid fallback.
- [ ] B5: Publish a local reproducible results report with raw machine-readable results, uncertainty, provenance, untested boundaries, and an honest replacement recommendation. Public promotion remains outside this task.

## Shared interface decisions

Keep `LocalClient.process_pending(limit)` returning the integer number processed, and `Store.process_one(...)` returning its existing boolean. Rich job diagnostics belong in status rather than changing these return types.

Retain `status.queue` as state-to-count mapping. Add `status.processing` with `oldest_pending_at` (ISO string or null), `next_retry_at` (Unix seconds or null), and safe rejection/failure counters. No source text or provider error bodies in diagnostics. Forge can use pending counts and retry timing to schedule the next bounded batch.

Add `forget_preview` accepting `{id}`. Return `{id, plan_token, memories: [...], counts: {memories, evidence}}`, scoped to the caller. Preview records include at least ID and content so a human can judge collateral deletion. `forget` accepts `{id, plan_token?}`; validate any supplied plan token atomically against the current closure. Missing confirmation for collateral memories beyond the selected target must not delete them. Direct single-memory forgetting may remain compatible. UI always previews and submits the plan token. Native tools and MCP expose the preview operation and describe the confirmation requirement. Tokens bind identities/revisions and dependency membership, not a stale count alone.

If a shared interface must change, propose the exact shape in this chat before editing another owner's assumptions. Keep changes within the existing architecture; do not build a new service or an autonomous framework.

## Verification status

Baseline review: 79 Python tests passed, three optional host-checkout tests skipped, and 17 Node tests passed. Default's isolated probes reproduced R1, R2, R3, R4 and invalid-only R8; Doofy reproduced R5/R6; Forge reproduced R7/R8.

Default has now added `tests/test_review_hardening.py` and executed it on the unchanged production baseline: eight regressions were discovered, with seven behavioral failures and the expected missing `forget_preview` operation error. `tests/openclaw_scheduler.test.mjs` also failed both intended cases: four events remained after the eight-job batch, and two retryable events stayed pending without another turn. These are the observed pre-fix gates. The installed comparison package was discovered through metadata as `mnemosyne-memory` 3.15.1; its live data has not been opened.
