# Mnemosyne Import Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship a safe local importer and a reproducible default-profile cutover/rollback procedure for issue #3.

**Architecture:** Translate a read-only v1.3 export into native evidence, memories, supports, and relations. Apply the complete validated plan atomically to an empty store, or verify an identical prior import. Use existing private JSON publication for the receipt.

**Tech Stack:** Python standard library, SQLite, unittest, native Hermes plugin loader.

**Spec:** `docs/superpowers/specs/2026-10-05-mnemosyne-import.md`

## Global Constraints

- Python 3.11+, no mandatory third-party runtime dependencies.
- Synthetic data only in tests; no models or host-profile mutation.
- Preserve source bytes; reject unsupported or lossy input.
- Live rollout remains dependent on access to the active default profile.

## Review Focus

- Duplicate/colliding source IDs: namespace by table and reject duplicates within a table.
- Naive or ambiguous timestamps: explicit timezone or a fail-closed diagnostic.
- Dangling/cross-scope supersession: reject before destination creation.
- Interrupted receipt publication: verified replay must regenerate it without duplication.
- Corrected/deleted imported records: repeat import must never overwrite or resurrect them.

### Task 1: Import plan and transactional application

**Files:** create `src/bigfeels_mem/mnemosyne.py`, `tests/test_mnemosyne_import.py`; modify `src/bigfeels_mem/cli.py`.

**Interfaces:** `plan_import(source, *, source_id, space, naive_timezone=None) -> ImportPlan`; `apply_import(store, plan) -> dict`. CLI: `import-mnemosyne INPUT --source-id ID --space SPACE --receipt PATH [--naive-timezone ZONE] [--dry-run]`.

- [x] Write CLI behavioral tests for fidelity, scopes, history, excluded sections, repeat import, receipt recovery, changed/deleted targets, malformed source, duplicate IDs, timezones, and rollback.
- [x] Run `python -m unittest discover -s tests -p test_mnemosyne_import.py`; confirm the missing command fails the expected success assertions.
- [x] Implement strict parsing, deterministic mapping, atomic application, exact verification, and receipt publication according to the spec.
- [x] Run focused tests and the full Python suite; expect no failures.
- [x] Commit importer, CLI, and tests.

### Task 2: Supported installation and rollback evidence

**Files:** create `docs/mnemosyne-migration.md`; modify `adapters/hermes/README.md`, `INSTALL.md`.

**Interfaces:** Existing root `plugin.yaml` and `__init__.py`; no Hermes core modification.

- [x] Verify the root plugin through the pinned upstream Hermes loader and document the actual boundary.
- [x] Document the read-only export input, receipt/scopes, source-timezone requirements, default-profile-only installation and verification, backups, and rollback.
- [x] Run full Python and Node suites, replay, package build, and a fresh-wheel import smoke.
- [x] Obtain independent code review; fix correctness findings with behavioral regressions.
- [ ] Publish a PR and merge the exact verified source tree. Keep #3 open if live data/profile access is unavailable.
