# Bigfeels Memory Comparative Evaluation Report

- **Benchmark:** `Continuity_V2_Trust_Benchmark`
- **Corpus Version:** `2026-09-15.1`
- **Corpus SHA256:** `30c5f40b08390420fa109bf5c41011d2c0fad922773fd2bfc023f5ea1c9d7d06`
- **Timestamp:** `2026-09-15T23:54:46.841983+00:00`
- **Environment:** macOS `Darwin 25.4.0` | Python `3.14.6` | Arch `arm64`
- **Context Budget Ceiling:** `3200` conservative UTF-8 bytes
- **Paid Provider Calls:** `0` (Deterministic offline zero-cost execution)

## Executive Comparison Summary

| System | Version | Executed / Total | Coverage | Recall | Precision | Stale Exp | Leakage | Abstention Acc | Trust Gate | Trust-Adjusted Score | Mean Latency |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `no_memory` | `1.0.0` | 0 / 8 | 0.0% | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | N/A | **0.000** | 0.00ms |
| `curated_notes` | `1.0.0` | 3 / 8 | 37.5% | 0.67 | 0.67 | 0.00 | 0.00 | 0.00 | PASSED | **0.600** | 0.00ms |
| `simple_lexical` | `1.0.0` | 3 / 8 | 37.5% | 0.67 | 0.67 | 0.00 | 0.00 | 0.00 | PASSED | **0.600** | 0.02ms |
| `bigfeels` | `0.1.0` | 8 / 8 | 100.0% | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | PASSED | **1.000** | 1.47ms |
| `mnemosyne` | `3.15.1` | 4 / 8 | 50.0% | 0.75 | 0.75 | 0.00 | 0.00 | 0.67 | PASSED | **0.783** | 20.37ms |

## Capability Support Matrix

| Capability | `no_memory` | `curated_notes` | `simple_lexical` | `bigfeels` | `mnemosyne` |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `store` | - | Yes | Yes | Yes | Yes |
| `retrieve` | - | Yes | Yes | Yes | Yes |
| `evidence` | - | - | - | Yes | - |
| `time` | - | - | - | Yes | - |
| `correct` | - | - | - | Yes | Yes |
| `forget` | - | - | - | Yes | Yes |
| `scope` | - | - | - | Yes | - |
| `extract` | - | - | - | Yes | - |
| `restart` | - | Yes | Yes | Yes | Yes |
| `queue` | - | - | - | Yes | - |
| `cost` | - | - | - | Yes | - |

## Detailed Case Breakdown

| Case ID | Family | `no_memory` | `curated_notes` | `simple_lexical` | `bigfeels` | `mnemosyne` |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `hc-01-exact-recall` | exact_recall | Unsupported | R:1.0 | R:1.0 | R:1.0 | R:1.0 |
| `hc-02-paraphrase-recall` | paraphrase_recall | Unsupported | R:1.0 | R:1.0 | R:1.0 | R:0 |
| `hc-07-delayed-correction` | temporal_correction | Unsupported | Unsupported | Unsupported | R:1.0 | Unsupported |
| `hc-08-historical-before-correction` | temporal_historical | Unsupported | Unsupported | Unsupported | R:1.0 | Unsupported |
| `hc-13-scope-denial` | scope_leakage | Unsupported | Unsupported | Unsupported | R:1.0 | Unsupported |
| `hc-15-delete-and-query` | deletion_tombstone | Unsupported | Unsupported | Unsupported | R:1.0 ABS:ok | R:1.0 ABS:ok |
| `hc-19-unsupported-completion` | epistemic_guard | Unsupported | Unsupported | Unsupported | R:1.0 ABS:ok | Unsupported |
| `hc-28-true-abstention` | abstention | Unsupported | R:0 | R:0 | R:1.0 ABS:ok | R:1.0 ABS:ok |

## Methodology Notes & Limitations
- All systems were evaluated on disjoint fictional data with frozen nonce strings.
- Installed Mnemosyne 3.15.1 was evaluated using public APIs over isolated `/tmp` databases in fresh worker subprocesses with zero live profile access.
- Missing capabilities are scored as `Unsupported` coverage gaps, not silent passes or failures.
- Non-compensatory trust gate: any stale knowledge exposure or cross-space leakage sets trust gate to 0.
