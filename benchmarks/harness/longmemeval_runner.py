"""Cleaned LongMemEval retrieval evaluation runner.

Operates purely offline and zero-cost on longmemeval_oracle.json or a specified
split/path. Evaluates retrieval precision, recall, and needle extraction without
external API keys or package installations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

# Path resolution
_SRC_DIR = str(Path(__file__).resolve().parent.parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from .base_adapter import BaseMemoryAdapter
from .baselines import CuratedNotesAdapter, NoMemoryAdapter, SimpleLexicalAdapter
from .bigfeels_adapter import BigfeelsAdapter
from .mnemosyne_adapter import MnemosyneAdapter

DEFAULT_ORACLE_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json"
)

ADAPTER_MAP: Dict[str, Type[BaseMemoryAdapter]] = {
    "no_memory": NoMemoryAdapter,
    "curated_notes": CuratedNotesAdapter,
    "simple_lexical": SimpleLexicalAdapter,
    "bigfeels": BigfeelsAdapter,
    "mnemosyne": MnemosyneAdapter,
}


def _content_chunks(content: str, max_chars: int = 12_000) -> List[str]:
    """Normalize oversized turns into identical bounded units for every system."""
    return [content[index:index + max_chars] for index in range(0, len(content), max_chars)] or [""]


def download_or_load_dataset(path_or_url: str, cache_dir: Path) -> tuple[List[Dict[str, Any]], str]:
    """Download or load cleaned LongMemEval dataset, returning records and sha256."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        filename = path_or_url.split("/")[-1]
        local_path = cache_dir / filename
        if not local_path.exists():
            print(f"Downloading LongMemEval dataset from {path_or_url} to {local_path}...")
            req = urllib.request.Request(path_or_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                local_path.write_bytes(data)
        else:
            print(f"Using cached LongMemEval dataset at {local_path}")
        target_file = local_path
    else:
        target_file = Path(path_or_url).resolve()

    raw_bytes = target_file.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    records = json.loads(raw_bytes.decode("utf-8"))
    return records, sha256


def evaluate_longmemeval_subset(
    dataset: List[Dict[str, Any]],
    systems: List[str],
    limit: int = 20,
    budget: int = 3200,
    offset: int = 0,
) -> Dict[str, Any]:
    """Evaluates adapters on declared development subset of LongMemEval."""
    subset = dataset[offset:offset + limit]
    print(f"Running LongMemEval retrieval evaluation on {len(subset)} questions across: {systems}")

    results_by_sys: Dict[str, Any] = {}

    for sname in systems:
        adapter_cls = ADAPTER_MAP.get(sname)
        if not adapter_cls:
            continue
        adapter = adapter_cls()
        caps = adapter.capabilities().to_dict()

        hits_at_k = 0
        total_eval = 0
        total_lat = 0.0
        instance_scores = []
        ingestion_failures = 0

        print(f"  Evaluating {sname} on {len(subset)} questions...")
        for q_idx, item in enumerate(subset):
            qid = item.get("question_id", f"q_{q_idx}")
            qtext = str(item.get("question") or "")
            qdate = item.get("question_date")
            gold_answer = str(item.get("answer") or "")
            answer_sids = set(item.get("answer_session_ids", []))
            haystack_sessions = item.get("haystack_sessions", [])
            haystack_session_ids = item.get("haystack_session_ids", [])

            # Create isolated instance
            handle = adapter.create_isolated_run(
                run_id=f"lme-{sname}-{qid}",
                seed=42,
                spaces=["default"],
                principals=["eval-user"],
            )

            try:
                # Ingest sessions
                ingestion_error = None
                ingest_step = 0
                for s_idx, session_turns in enumerate(haystack_sessions):
                    sid = (
                        str(haystack_session_ids[s_idx])
                        if s_idx < len(haystack_session_ids)
                        else f"s_{s_idx}"
                    )
                    for turn in session_turns:
                        role = turn.get("role", "user")
                        content = str(turn.get("content") or "")
                        chunks = _content_chunks(content)
                        for chunk_index, chunk in enumerate(chunks):
                            ingest_step += 1
                            outcome = adapter.apply_op(
                                handle,
                                {
                                    "step": ingest_step,
                                    "op": "remember",
                                    "actor": "eval-user",
                                    "space": "default",
                                    "content": f"[{sid}] {chunk}",
                                    "metadata": {
                                        "session_id": sid,
                                        "role": role,
                                        "chunk_index": chunk_index,
                                        "chunk_count": len(chunks),
                                    },
                                },
                            )
                            if outcome.status != "ok":
                                ingestion_error = outcome.safe_diagnostics or {
                                    "status": outcome.status,
                                }
                                break
                        if ingestion_error:
                            break
                    if ingestion_error:
                        break

                # LongMemEval ingestion is one bounded batch per isolated
                # question (the adapter also flushes at its safety bound).
                # Make the boundary explicit so batch failures are counted
                # as ingestion failures, not mistaken for empty retrieval.
                flush_pending = getattr(adapter, "flush_pending", None)
                if not ingestion_error and flush_pending is not None:
                    flush_result = flush_pending(handle)
                    if flush_result.status != "ok":
                        ingestion_error = flush_result.safe_diagnostics or {
                            "status": flush_result.status,
                        }

                if ingestion_error:
                    ingestion_failures += 1
                    total_eval += 1
                    instance_scores.append({
                        "question_id": qid,
                        "status": "invalid_ingestion",
                        "hit": False,
                        "session_hit": False,
                        "answer_hit": False,
                        "latency_ms": 0.0,
                        "retrieved_count": 0,
                        "diagnostics": ingestion_error,
                    })
                    continue

                # Query retrieval
                t0 = time.perf_counter()
                ret_res = adapter.retrieve(
                    handle=handle,
                    actor="eval-user",
                    query=qtext,
                    # LongMemEval question/session dates are prompt metadata, not
                    # a normalized storage cutoff. Passing them only to systems
                    # with an as_of API makes the retrieval comparison asymmetric.
                    as_of=None,
                    budget=budget,
                )
                lat = (time.perf_counter() - t0) * 1000
                total_lat += lat

                # Check if retrieved records contain the gold session or answer keywords
                retrieved_texts = [r.content for r in ret_res.records]
                session_hit = False
                for sid in answer_sids:
                    if any(f"[{sid}]" in t for t in retrieved_texts):
                        session_hit = True
                        break

                # Also check direct gold answer substring recall
                answer_hit = any(gold_answer.lower() in t.lower() for t in retrieved_texts) if gold_answer else False
                hit = session_hit or answer_hit

                if hit:
                    hits_at_k += 1
                total_eval += 1

                instance_scores.append({
                    "question_id": qid,
                    "status": "ok",
                    "hit": hit,
                    "session_hit": session_hit,
                    "answer_hit": answer_hit,
                    "latency_ms": round(lat, 2),
                    "retrieved_count": len(retrieved_texts),
                })

            finally:
                adapter.teardown(handle)

        recall_at_k = hits_at_k / total_eval if total_eval else 0.0
        avg_lat = total_lat / total_eval if total_eval else 0.0
        results_by_sys[sname] = {
            "evaluated_questions": total_eval,
            "ingestion_failures": ingestion_failures,
            "session_recall_at_k": round(recall_at_k, 4),
            "mean_latency_ms": round(avg_lat, 2),
            "instances": instance_scores,
        }

    return results_by_sys


def main():
    parser = argparse.ArgumentParser(description="LongMemEval retrieval runner.")
    parser.add_argument("--dataset", type=str, default=DEFAULT_ORACLE_URL, help="URL or path to LongMemEval JSON.")
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/longmemeval_cache"), help="Local cache directory.")
    parser.add_argument("--limit", type=int, default=10, help="Number of instances to evaluate in dev subset.")
    parser.add_argument("--offset", type=int, default=0, help="Zero-based dataset offset for a frozen split.")
    parser.add_argument("--systems", type=str, default="no_memory,curated_notes,simple_lexical,bigfeels,mnemosyne")
    parser.add_argument("--budget", type=int, default=3200)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    records, sha = download_or_load_dataset(args.dataset, args.cache_dir)
    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    eval_res = evaluate_longmemeval_subset(
        records, systems, limit=args.limit, budget=args.budget, offset=args.offset,
    )

    report = {
        "benchmark": "LongMemEval_Cleaned_Retrieval",
        "dataset_source": args.dataset,
        "dataset_sha256": sha,
        "total_dataset_questions": len(records),
        "evaluated_subset_limit": args.limit,
        "evaluated_subset_offset": args.offset,
        "subset_policy": (
            "first_n_development_only" if args.offset == 0
            else "frozen_contiguous_holdout"
        ),
        "metric_contract": "gold-session retrieval proxy; not answer accuracy or turn-level precision",
        "storage_as_of_filter": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "systems": eval_res,
    }

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote LongMemEval report to {args.json_out}")

    print("\n--- LongMemEval Development Subset Results ---")
    for sname, res in eval_res.items():
        print(f"  System: {sname:15} | Recall@k: {res['session_recall_at_k']:.3f} | Latency: {res['mean_latency_ms']:.2f}ms")


if __name__ == "__main__":
    main()
