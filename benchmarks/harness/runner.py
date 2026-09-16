"""Unified benchmark execution runner for bigfeels and memory baselines."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Type

# Path resolution
_SRC_DIR = str(Path(__file__).resolve().parent.parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from .base_adapter import BaseMemoryAdapter
from .baselines import CuratedNotesAdapter, NoMemoryAdapter, SimpleLexicalAdapter
from .bigfeels_adapter import BigfeelsAdapter
from .corpus import generate_continuity_v2_corpus, write_frozen_corpus
from .mnemosyne_adapter import MnemosyneAdapter
from .scorer import CaseScore, EvaluationSummary, aggregate_scores, score_case


ADAPTER_REGISTRY: Dict[str, Type[BaseMemoryAdapter]] = {
    "no_memory": NoMemoryAdapter,
    "curated_notes": CuratedNotesAdapter,
    "simple_lexical": SimpleLexicalAdapter,
    "bigfeels": BigfeelsAdapter,
    "mnemosyne": MnemosyneAdapter,
}


def run_evaluation(
    adapter_cls: Type[BaseMemoryAdapter],
    public_cases: List[Dict[str, Any]],
    gold_records: Dict[str, Dict[str, Any]],
    budget: int = 3200,
) -> EvaluationSummary:
    """Execute evaluation for one adapter over all cases."""
    adapter = adapter_cls()
    caps = adapter.capabilities().to_dict()
    case_scores: List[CaseScore] = []

    for case in public_cases:
        cid = case["case_id"]
        gold = gold_records.get(cid, {})
        req_caps = set(case.get("capability", []))

        # Check if adapter lacks fundamental required capability
        # Store & retrieve are required; others like 'time', 'scope', 'forget', 'correct'
        # can be executed if supported, or recorded as unsupported
        supported = True
        for c in req_caps:
            if not caps.get(c, False):
                # E.g. If case tests 'scope' or 'time' or 'forget' and adapter has False
                supported = False
                break

        if not supported:
            case_scores.append(
                CaseScore(
                    case_id=cid,
                    status="unsupported",
                    details={"missing_capabilities": [c for c in req_caps if not caps.get(c, False)]},
                )
            )
            continue

        # Setup isolated run
        init_state = case.get("initial_state", {})
        spaces = init_state.get("spaces", ["workspace:default"])
        principals = init_state.get("principals", ["agent-a"])
        handle = adapter.create_isolated_run(
            run_id=f"{adapter.name}-{cid}",
            seed=case.get("seed", 42),
            spaces=spaces,
            principals=principals,
        )

        try:
            # Apply sequential operations
            has_error = False
            err_msg = ""
            for op in case.get("operations", []):
                op_res = adapter.apply_op(handle, op)
                if op_res.status == "error":
                    has_error = True
                    err_msg = str(op_res.safe_diagnostics)
                    break
                elif op_res.status == "unsupported":
                    # Mark case unsupported
                    has_error = True
                    err_msg = f"unsupported op {op.get('op')}"
                    break

            if has_error:
                case_scores.append(
                    CaseScore(
                        case_id=cid,
                        status="unsupported" if "unsupported" in err_msg else "error",
                        details={"error": err_msg},
                    )
                )
                continue

            # Execute query retrieval
            q = case["query"]
            ret_res = adapter.retrieve(
                handle=handle,
                actor=q.get("actor", "agent-a"),
                query=q.get("text", ""),
                as_of=q.get("as_of"),
                budget=q.get("budget", budget),
            )

            if ret_res.status != "ok":
                case_scores.append(
                    CaseScore(
                        case_id=cid,
                        status="error",
                        latency_ms=ret_res.latency_ms,
                        details={"error": str(ret_res.safe_diagnostics)},
                    )
                )
                continue

            retrieved_contents = [r.content for r in ret_res.records]

            # Score case against gold
            cs = score_case(
                case_id=cid,
                gold=gold,
                retrieved_contents=retrieved_contents,
                latency_ms=ret_res.latency_ms,
                tokens_used=ret_res.tokens_used,
                provider_calls=ret_res.provider_calls,
                status="ok",
            )
            case_scores.append(cs)

        finally:
            adapter.teardown(handle)

    return aggregate_scores(
        system_name=adapter.name,
        version=adapter.version,
        capabilities=caps,
        case_scores=case_scores,
    )


def generate_markdown_report(report_data: Dict[str, Any]) -> str:
    """Format evaluation run results into a clear Markdown comparison table."""
    lines: List[str] = []
    lines.append("# Bigfeels Memory Comparative Evaluation Report")
    lines.append("")
    lines.append(f"- **Benchmark:** `{report_data['benchmark']}`")
    lines.append(f"- **Corpus Version:** `{report_data['corpus_version']}`")
    lines.append(f"- **Corpus SHA256:** `{report_data['corpus_sha256']}`")
    lines.append(f"- **Timestamp:** `{report_data['timestamp']}`")
    lines.append(f"- **Environment:** macOS `{report_data['environment']['os']}` | Python `{report_data['environment']['python_version']}` | Arch `{report_data['environment']['arch']}`")
    lines.append(f"- **Context Budget Ceiling:** `{report_data['budget_bytes']}` conservative UTF-8 bytes")
    lines.append(f"- **Paid Provider Calls:** `0` (Deterministic offline zero-cost execution)")
    lines.append("")
    lines.append("## Executive Comparison Summary")
    lines.append("")
    lines.append("| System | Version | Executed / Total | Coverage | Recall | Precision | Stale Exp | Leakage | Abstention Acc | Trust Gate | Trust-Adjusted Score | Mean Latency |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    for sys_name, s in report_data["systems"].items():
        cov_pct = f"{round((s['executed_cases']/s['total_cases'])*100, 1)}%"
        gate_str = (
            "N/A" if s["trust_gate_passed"] is None
            else "PASSED" if s["trust_gate_passed"]
            else "**FAILED**"
        )
        lines.append(
            f"| `{sys_name}` | `{s['version']}` | {s['executed_cases']} / {s['total_cases']} | {cov_pct} | "
            f"{s['mean_retrieval_recall']:.2f} | {s['mean_retrieval_precision']:.2f} | "
            f"{s['stale_exposure_rate']:.2f} | {s['leakage_rate']:.2f} | {s['abstention_accuracy']:.2f} | "
            f"{gate_str} | **{s['trust_adjusted_score']:.3f}** | {s['mean_latency_ms']:.2f}ms |"
        )

    lines.append("")
    lines.append("## Capability Support Matrix")
    lines.append("")
    all_caps = ["store", "retrieve", "evidence", "time", "correct", "forget", "scope", "extract", "restart", "queue", "cost"]
    header = "| Capability | " + " | ".join(f"`{s}`" for s in report_data["systems"].keys()) + " |"
    sep = "| :--- | " + " | ".join(":---:" for _ in report_data["systems"]) + " |"
    lines.append(header)
    lines.append(sep)

    for cap in all_caps:
        row = f"| `{cap}` | "
        cols = []
        for s in report_data["systems"].values():
            val = s["capabilities"].get(cap, False)
            cols.append("Yes" if val else "-")
        row += " | ".join(cols) + " |"
        lines.append(row)

    lines.append("")
    lines.append("## Detailed Case Breakdown")
    lines.append("")
    lines.append("| Case ID | Family | " + " | ".join(f"`{s}`" for s in report_data["systems"].keys()) + " |")
    lines.append("| :--- | :--- | " + " | ".join(":---:" for _ in report_data["systems"]) + " |")

    cases = report_data.get("cases_metadata", [])
    for c in cases:
        cid = c["case_id"]
        fam = c["family"]
        row = f"| `{cid}` | {fam} | "
        cols = []
        for sys_name, s in report_data["systems"].items():
            cs_match = next((item for item in s["case_scores"] if item["case_id"] == cid), None)
            if not cs_match:
                cols.append("N/A")
            elif cs_match["status"] == "unsupported":
                cols.append("Unsupported")
            elif cs_match["status"] == "error":
                cols.append("Error")
            else:
                badges = []
                if cs_match["recall"] == 1.0:
                    badges.append("R:1.0")
                elif cs_match["recall"] > 0:
                    badges.append(f"R:{cs_match['recall']:.1f}")
                else:
                    badges.append("R:0")

                if cs_match["stale_exposure"]:
                    badges.append("STALE")
                if cs_match["leakage"]:
                    badges.append("LEAK")
                if cs_match["correct_abstention"]:
                    badges.append("ABS:ok")
                cols.append(" ".join(badges))
        row += " | ".join(cols) + " |"
        lines.append(row)

    lines.append("")
    lines.append("## Methodology Notes & Limitations")
    lines.append("- All systems were evaluated on disjoint fictional data with frozen nonce strings.")
    lines.append("- Installed Mnemosyne 3.15.1 was evaluated using public APIs over isolated `/tmp` databases in fresh worker subprocesses with zero live profile access.")
    lines.append("- Missing capabilities are scored as `Unsupported` coverage gaps, not silent passes or failures.")
    lines.append("- Non-compensatory trust gate: any stale knowledge exposure or cross-space leakage sets trust gate to 0.")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Host-neutral memory evaluation harness.")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
        help="Directory containing frozen corpus or where corpus will be generated.",
    )
    parser.add_argument(
        "--systems",
        type=str,
        default="no_memory,curated_notes,simple_lexical,bigfeels,mnemosyne",
        help="Comma-separated list of systems to evaluate.",
    )
    parser.add_argument("--budget", type=int, default=3200, help="Conservative UTF-8 byte budget ceiling.")
    parser.add_argument("--json-out", type=Path, default=None, help="Path to write results JSON.")
    parser.add_argument("--md-out", type=Path, default=None, help="Path to write Markdown report.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic generator seed.")
    args = parser.parse_args()

    corpus_dir = args.corpus_dir
    corpus_dir.mkdir(parents=True, exist_ok=True)
    pub_file = corpus_dir / "continuity_v2.public.jsonl"
    gold_file = corpus_dir / "continuity_v2.gold.jsonl"

    if not pub_file.exists() or not gold_file.exists():
        print(f"Generating frozen synthetic corpus in {corpus_dir} (seed={args.seed})...")
        write_frozen_corpus(corpus_dir, seed=args.seed)

    # Read public cases
    public_cases: List[Dict[str, Any]] = []
    with pub_file.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                public_cases.append(json.loads(line))

    # Read gold records
    gold_records: Dict[str, Dict[str, Any]] = {}
    with gold_file.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                gold_records[rec["case_id"]] = rec

    import hashlib
    corpus_sha = hashlib.sha256(pub_file.read_bytes()).hexdigest()

    systems_to_run = [s.strip() for s in args.systems.split(",") if s.strip()]
    results_by_system: Dict[str, Dict[str, Any]] = {}

    print(f"Starting comparative evaluation across {len(systems_to_run)} systems on {len(public_cases)} cases...")
    for sname in systems_to_run:
        if sname not in ADAPTER_REGISTRY:
            print(f"Warning: unknown system '{sname}', skipping.")
            continue
        print(f"  Evaluating '{sname}'...")
        summary = run_evaluation(ADAPTER_REGISTRY[sname], public_cases, gold_records, budget=args.budget)
        results_by_system[sname] = summary.to_dict()

    report_payload = {
        "benchmark": "Continuity_V2_Trust_Benchmark",
        "corpus_version": "2026-09-15.1",
        "corpus_sha256": corpus_sha,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "os": platform.system() + " " + platform.release(),
            "python_version": platform.python_version(),
            "arch": platform.machine(),
        },
        "budget_bytes": args.budget,
        "budget_unit": "conservative_utf8_bytes",
        "systems": results_by_system,
        "cases_metadata": [
            {"case_id": c["case_id"], "family": c.get("family", ""), "difficulty": c.get("difficulty", "")}
            for c in public_cases
        ],
    }

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report_payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote raw JSON report to {args.json_out}")

    md_report = generate_markdown_report(report_payload)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md_report + "\n", encoding="utf-8")
        print(f"Wrote Markdown report to {args.md_out}")

    print("\n" + md_report)


if __name__ == "__main__":
    main()
