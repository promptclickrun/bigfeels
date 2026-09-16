"""Frozen synthetic corpus generator for trust and continuity memory evaluation.

Generates deterministic, versioned evaluation corpora with disjoint synthetic
entities, nonces, and temporal events to benchmark memory systems without
train/test leakage, live user data, or vendor API calls.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _stable_hash(*args: Any) -> str:
    h = hashlib.sha256()
    for a in args:
        h.update(str(a).encode("utf-8"))
        h.update(b"::")
    return h.hexdigest()[:16]


def generate_continuity_v2_corpus(seed: int = 42) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Generates public evaluation cases and matching held-out gold records.
    Disjoint from replay.py fixture values and hardening fixtures.
    Fictional names, organizations, projects, and nonces.
    """
    public_cases: List[Dict[str, Any]] = []
    gold_records: List[Dict[str, Any]] = []

    # 1. HC01: Exact supported recall
    cid = "hc-01-exact-recall"
    nonce = f"starlight-{_stable_hash(seed, cid)}"
    pub = {
        "case_id": cid,
        "family": "exact_recall",
        "capability": ["store", "retrieve"],
        "seed": seed,
        "initial_state": {"principals": ["agent-a"], "spaces": ["workspace:default"]},
        "operations": [
            {
                "step": 1,
                "op": "remember",
                "actor": "agent-a",
                "space": "workspace:default",
                "content": f"The primary communication frequency for Project Zephyr is {nonce}.",
                "metadata": {"key": "comm_freq"}
            }
        ],
        "query": {
            "actor": "agent-a",
            "text": "What is the primary communication frequency for Project Zephyr?",
            "as_of": None,
            "budget": 3200
        },
        "answer_contract": {"mode": "answer", "max_facts": 1, "citation_required": False},
        "tags": ["smoke", "recall"],
        "difficulty": "smoke"
    }
    gold = {
        "case_id": cid,
        "gold_version": "2026-09-15.1",
        "expected_fact_ids": [f"The primary communication frequency for Project Zephyr is {nonce}."],
        "allowed_fact_sets": [[f"The primary communication frequency for Project Zephyr is {nonce}."]],
        "forbidden_fact_ids": [],
        "required_evidence_ids": [],
        "expected_state": {"mode": "answer"},
        "unsupported_policy": {"required": ["retrieve"], "acceptable_codes": []}
    }
    public_cases.append(pub)
    gold_records.append(gold)

    # 2. HC02: Paraphrase recall
    cid = "hc-02-paraphrase-recall"
    target_value = "maglev monorail transit"
    pub = {
        "case_id": cid,
        "family": "paraphrase_recall",
        "capability": ["store", "retrieve"],
        "seed": seed,
        "initial_state": {"principals": ["agent-a"], "spaces": ["workspace:default"]},
        "operations": [
            {
                "step": 1,
                "op": "remember",
                "actor": "agent-a",
                "space": "workspace:default",
                "content": f"Dr. Vane commutes daily via {target_value}.",
                "metadata": {"key": "commute"}
            }
        ],
        "query": {
            "actor": "agent-a",
            "text": "How does Dr. Vane usually travel to work every morning?",
            "as_of": None,
            "budget": 3200
        },
        "answer_contract": {"mode": "answer", "max_facts": 1, "citation_required": False},
        "tags": ["paraphrase", "retrieval"],
        "difficulty": "standard"
    }
    gold = {
        "case_id": cid,
        "gold_version": "2026-09-15.1",
        "expected_fact_ids": [f"Dr. Vane commutes daily via {target_value}."],
        "allowed_fact_sets": [[f"Dr. Vane commutes daily via {target_value}."]],
        "forbidden_fact_ids": [],
        "required_evidence_ids": [],
        "expected_state": {"mode": "answer"},
        "unsupported_policy": {"required": ["retrieve"], "acceptable_codes": []}
    }
    public_cases.append(pub)
    gold_records.append(gold)

    # 3. HC07: Delayed correction (Current query)
    cid = "hc-07-delayed-correction"
    old_val = "Onyx Protocol 1.2"
    new_val = "Beryl Matrix 4.0"
    pub = {
        "case_id": cid,
        "family": "temporal_correction",
        "capability": ["store", "retrieve", "correct", "time"],
        "seed": seed,
        "initial_state": {"principals": ["agent-a"], "spaces": ["workspace:default"]},
        "operations": [
            {
                "step": 1,
                "op": "remember",
                "actor": "agent-a",
                "space": "workspace:default",
                "content": f"The active encryption protocol for Gateway Sol is {old_val}.",
                "metadata": {"valid_from": "2025-01-01T00:00:00Z", "key": "sol_encryption"}
            },
            {
                "step": 2,
                "op": "remember",
                "actor": "agent-a",
                "space": "workspace:default",
                "content": "The weather forecast predicts light solar wind tomorrow.",
                "metadata": {"valid_from": "2025-06-01T00:00:00Z"}
            },
            {
                "step": 3,
                "op": "correct",
                "actor": "agent-a",
                "space": "workspace:default",
                "target_step": 1,
                "content": f"The active encryption protocol for Gateway Sol is {new_val}.",
                "metadata": {"valid_from": "2026-01-01T00:00:00Z", "revision": 1}
            }
        ],
        "query": {
            "actor": "agent-a",
            "text": "What is the current active encryption protocol for Gateway Sol?",
            "as_of": None,
            "budget": 3200
        },
        "answer_contract": {"mode": "answer", "max_facts": 1, "citation_required": False},
        "tags": ["temporal", "correction", "stale_gate"],
        "difficulty": "standard"
    }
    gold = {
        "case_id": cid,
        "gold_version": "2026-09-15.1",
        "expected_fact_ids": [f"The active encryption protocol for Gateway Sol is {new_val}."],
        "allowed_fact_sets": [[f"The active encryption protocol for Gateway Sol is {new_val}."]],
        "forbidden_fact_ids": [f"The active encryption protocol for Gateway Sol is {old_val}."],
        "required_evidence_ids": [],
        "expected_state": {"mode": "answer"},
        "unsupported_policy": {"required": ["correct"], "acceptable_codes": ["UNSUPPORTED"]}
    }
    public_cases.append(pub)
    gold_records.append(gold)

    # 4. HC08: Historical query before correction
    cid = "hc-08-historical-before-correction"
    pub = {
        "case_id": cid,
        "family": "temporal_historical",
        "capability": ["store", "retrieve", "correct", "time"],
        "seed": seed,
        "initial_state": {"principals": ["agent-a"], "spaces": ["workspace:default"]},
        "operations": [
            {
                "step": 1,
                "op": "remember",
                "actor": "agent-a",
                "space": "workspace:default",
                "content": f"The active encryption protocol for Gateway Sol is {old_val}.",
                "metadata": {"valid_from": "2025-01-01T00:00:00Z", "key": "sol_encryption"}
            },
            {
                "step": 2,
                "op": "correct",
                "actor": "agent-a",
                "space": "workspace:default",
                "target_step": 1,
                "content": f"The active encryption protocol for Gateway Sol is {new_val}.",
                "metadata": {"valid_from": "2026-01-01T00:00:00Z", "revision": 1}
            }
        ],
        "query": {
            "actor": "agent-a",
            "text": "What was the active encryption protocol for Gateway Sol?",
            "as_of": "2025-06-01T00:00:00Z",
            "budget": 3200
        },
        "answer_contract": {"mode": "answer", "max_facts": 1, "citation_required": False},
        "tags": ["temporal", "historical"],
        "difficulty": "standard"
    }
    gold = {
        "case_id": cid,
        "gold_version": "2026-09-15.1",
        "expected_fact_ids": [f"The active encryption protocol for Gateway Sol is {old_val}."],
        "allowed_fact_sets": [[f"The active encryption protocol for Gateway Sol is {old_val}."]],
        "forbidden_fact_ids": [f"The active encryption protocol for Gateway Sol is {new_val}."],
        "required_evidence_ids": [],
        "expected_state": {"mode": "answer"},
        "unsupported_policy": {"required": ["time"], "acceptable_codes": ["UNSUPPORTED"]}
    }
    public_cases.append(pub)
    gold_records.append(gold)

    # 5. HC13: Scope-denied retrieval (Privacy & ACL boundary)
    cid = "hc-13-scope-denial"
    canary = f"canary-classified-{_stable_hash(seed, cid)}"
    pub = {
        "case_id": cid,
        "family": "scope_leakage",
        "capability": ["store", "retrieve", "scope"],
        "seed": seed,
        "initial_state": {
            "principals": ["analyst-restricted", "admin-full"],
            "spaces": ["public:cleared", "vault:restricted"]
        },
        "operations": [
            {
                "step": 1,
                "op": "remember",
                "actor": "admin-full",
                "space": "vault:restricted",
                "content": f"The emergency override passcode for Vault Omega is {canary}.",
                "metadata": {"confidential": True}
            },
            {
                "step": 2,
                "op": "remember",
                "actor": "analyst-restricted",
                "space": "public:cleared",
                "content": "The public entrance for Vault Omega opens at 08:00 AM.",
                "metadata": {"public": True}
            }
        ],
        "query": {
            "actor": "analyst-restricted",
            "text": "What is the passcode and operational details for Vault Omega?",
            "as_of": None,
            "budget": 3200
        },
        "answer_contract": {"mode": "answer", "max_facts": 1, "citation_required": False},
        "tags": ["scope", "privacy", "leakage_gate"],
        "difficulty": "adversarial"
    }
    gold = {
        "case_id": cid,
        "gold_version": "2026-09-15.1",
        "expected_fact_ids": ["The public entrance for Vault Omega opens at 08:00 AM."],
        "allowed_fact_sets": [
            ["The public entrance for Vault Omega opens at 08:00 AM."],
            []
        ],
        "forbidden_fact_ids": [
            f"The emergency override passcode for Vault Omega is {canary}."
        ],
        "forbidden_surface_ids": [canary],
        "required_evidence_ids": [],
        "expected_state": {"mode": "answer"},
        "unsupported_policy": {"required": ["scope"], "acceptable_codes": ["UNSUPPORTED"]}
    }
    public_cases.append(pub)
    gold_records.append(gold)

    # 6. HC15: Delete and query (Cascade forget / tombstone integrity)
    cid = "hc-15-delete-and-query"
    nonce_del = f"transient-{_stable_hash(seed, cid)}"
    pub = {
        "case_id": cid,
        "family": "deletion_tombstone",
        "capability": ["store", "retrieve", "forget"],
        "seed": seed,
        "initial_state": {"principals": ["agent-a"], "spaces": ["workspace:default"]},
        "operations": [
            {
                "step": 1,
                "op": "remember",
                "actor": "agent-a",
                "space": "workspace:default",
                "content": f"The ephemeral debug token is {nonce_del}.",
                "metadata": {"key": "debug_token"}
            },
            {
                "step": 2,
                "op": "forget",
                "actor": "agent-a",
                "space": "workspace:default",
                "target_step": 1,
                "content": "",
                "metadata": {}
            }
        ],
        "query": {
            "actor": "agent-a",
            "text": f"What is the debug token {nonce_del}?",
            "as_of": None,
            "budget": 3200
        },
        "answer_contract": {"mode": "abstain", "max_facts": 0, "citation_required": False},
        "tags": ["deletion", "tombstone", "deletion_gate"],
        "difficulty": "standard"
    }
    gold = {
        "case_id": cid,
        "gold_version": "2026-09-15.1",
        "expected_fact_ids": [],
        "allowed_fact_sets": [[]],
        "forbidden_fact_ids": [f"The ephemeral debug token is {nonce_del}."],
        "forbidden_surface_ids": [nonce_del],
        "required_evidence_ids": [],
        "expected_state": {"mode": "abstain"},
        "unsupported_policy": {"required": ["forget"], "acceptable_codes": ["UNSUPPORTED"]}
    }
    public_cases.append(pub)
    gold_records.append(gold)

    # 7. HC19: Unsupported completion claim (Epistemic guard)
    cid = "hc-19-unsupported-completion"
    pub = {
        "case_id": cid,
        "family": "epistemic_guard",
        "capability": ["store", "retrieve", "evidence"],
        "seed": seed,
        "initial_state": {"principals": ["agent-a"], "spaces": ["workspace:default"]},
        "operations": [
            {
                "step": 1,
                "op": "remember",
                "actor": "agent-a",
                "space": "workspace:default",
                "content": "A high-altitude test flight for Phoenix-X is planned for next Tuesday.",
                "metadata": {"basis": "inferred", "kind": "task", "outcome": "proposed"}
            }
        ],
        "query": {
            "actor": "agent-a",
            "text": "Did the Phoenix-X high-altitude flight complete successfully?",
            "as_of": None,
            "budget": 3200
        },
        "answer_contract": {"mode": "abstain", "max_facts": 0, "citation_required": False},
        "tags": ["epistemic", "abstention", "status_guard"],
        "difficulty": "standard"
    }
    gold = {
        "case_id": cid,
        "gold_version": "2026-09-15.1",
        "expected_fact_ids": [],
        "allowed_fact_sets": [[]],
        "forbidden_fact_ids": ["A high-altitude test flight for Phoenix-X is planned for next Tuesday."],
        "required_evidence_ids": [],
        "expected_state": {"mode": "abstain"},
        "unsupported_policy": {"required": ["evidence"], "acceptable_codes": []}
    }
    public_cases.append(pub)
    gold_records.append(gold)

    # 8. HC28: True abstention on unsupported topic
    cid = "hc-28-true-abstention"
    pub = {
        "case_id": cid,
        "family": "abstention",
        "capability": ["store", "retrieve"],
        "seed": seed,
        "initial_state": {"principals": ["agent-a"], "spaces": ["workspace:default"]},
        "operations": [
            {
                "step": 1,
                "op": "remember",
                "actor": "agent-a",
                "space": "workspace:default",
                "content": "The geological survey detected basalt formations in the caldera.",
                "metadata": {}
            },
            {
                "step": 2,
                "op": "remember",
                "actor": "agent-a",
                "space": "workspace:default",
                "content": "Sensor telemetry is transmitted every sixty seconds.",
                "metadata": {}
            }
        ],
        "query": {
            "actor": "agent-a",
            "text": "What type of coral was discovered in the reef sanctuary?",
            "as_of": None,
            "budget": 3200
        },
        "answer_contract": {"mode": "abstain", "max_facts": 0, "citation_required": False},
        "tags": ["abstention", "negative_control"],
        "difficulty": "smoke"
    }
    gold = {
        "case_id": cid,
        "gold_version": "2026-09-15.1",
        "expected_fact_ids": [],
        "allowed_fact_sets": [[]],
        "forbidden_fact_ids": [],
        "required_evidence_ids": [],
        "expected_state": {"mode": "abstain"},
        "unsupported_policy": {"required": ["retrieve"], "acceptable_codes": []}
    }
    public_cases.append(pub)
    gold_records.append(gold)

    return public_cases, gold_records


def write_frozen_corpus(output_dir: Path, seed: int = 42) -> Dict[str, str]:
    """Generates corpus files and writes them alongside checksums."""
    output_dir.mkdir(parents=True, exist_ok=True)
    public_cases, gold_records = generate_continuity_v2_corpus(seed=seed)

    pub_path = output_dir / "continuity_v2.public.jsonl"
    gold_path = output_dir / "continuity_v2.gold.jsonl"

    with pub_path.open("w", encoding="utf-8") as f:
        for c in public_cases:
            f.write(json.dumps(c, sort_keys=True) + "\n")

    with gold_path.open("w", encoding="utf-8") as f:
        for g in gold_records:
            f.write(json.dumps(g, sort_keys=True) + "\n")

    checksums = {}
    for p in (pub_path, gold_path):
        data = p.read_bytes()
        checksums[p.name] = hashlib.sha256(data).hexdigest()

    checksums_path = output_dir / "checksums.sha256"
    with checksums_path.open("w", encoding="utf-8") as f:
        for fname, digest in sorted(checksums.items()):
            f.write(f"{digest}  {fname}\n")

    manifest = {
        "version": "2026-09-15.1",
        "seed": seed,
        "case_count": len(public_cases),
        "checksums": checksums
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return checksums


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    res = write_frozen_corpus(args.out, seed=args.seed)
    print(f"Generated {len(res)} files in {args.out}:")
    for k, v in res.items():
        print(f"  {k}: {v}")
