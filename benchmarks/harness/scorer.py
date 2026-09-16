"""Non-compensatory trust and retrieval scoring for memory evaluations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class CaseScore:
    case_id: str
    status: str  # "ok", "unsupported", "error"
    retrieval_recall: float = 0.0
    retrieval_precision: float = 0.0
    stale_exposure: bool = False
    leakage: bool = False
    forbidden_exposures: int = 0
    correct_abstention: bool = False
    false_abstention: bool = False
    correction_durable: bool = False
    deletion_durable: bool = False
    latency_ms: float = 0.0
    tokens_used: int = 0
    provider_calls: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationSummary:
    system_name: str
    version: str
    capabilities: Dict[str, bool]
    total_cases: int
    executed_cases: int
    unsupported_cases: int
    error_cases: int
    mean_retrieval_recall: float = 0.0
    mean_retrieval_precision: float = 0.0
    stale_exposure_rate: float = 0.0
    leakage_rate: float = 0.0
    total_forbidden_exposures: int = 0
    abstention_accuracy: float = 0.0
    trust_gate_passed: Optional[bool] = True
    trust_adjusted_score: float = 0.0
    mean_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    total_provider_calls: int = 0
    case_scores: List[CaseScore] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system_name": self.system_name,
            "version": self.version,
            "capabilities": self.capabilities,
            "total_cases": self.total_cases,
            "executed_cases": self.executed_cases,
            "unsupported_cases": self.unsupported_cases,
            "error_cases": self.error_cases,
            "mean_retrieval_recall": round(self.mean_retrieval_recall, 4),
            "mean_retrieval_precision": round(self.mean_retrieval_precision, 4),
            "stale_exposure_rate": round(self.stale_exposure_rate, 4),
            "leakage_rate": round(self.leakage_rate, 4),
            "total_forbidden_exposures": self.total_forbidden_exposures,
            "abstention_accuracy": round(self.abstention_accuracy, 4),
            "trust_gate_passed": self.trust_gate_passed,
            "trust_adjusted_score": round(self.trust_adjusted_score, 4),
            "mean_latency_ms": round(self.mean_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "total_provider_calls": self.total_provider_calls,
            "case_scores": [
                {
                    "case_id": cs.case_id,
                    "status": cs.status,
                    "recall": round(cs.retrieval_recall, 4),
                    "precision": round(cs.retrieval_precision, 4),
                    "stale_exposure": cs.stale_exposure,
                    "leakage": cs.leakage,
                    "forbidden_exposures": cs.forbidden_exposures,
                    "correct_abstention": cs.correct_abstention,
                    "false_abstention": cs.false_abstention,
                    "latency_ms": round(cs.latency_ms, 2),
                    "tokens_used": cs.tokens_used,
                    "details": cs.details,
                }
                for cs in self.case_scores
            ],
        }


def score_case(
    case_id: str,
    gold: Dict[str, Any],
    retrieved_contents: List[str],
    latency_ms: float,
    tokens_used: int,
    provider_calls: int = 0,
    status: str = "ok",
) -> CaseScore:
    """Computes exact metrics for a single case against restricted gold."""
    if status != "ok":
        return CaseScore(
            case_id=case_id,
            status=status,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            provider_calls=provider_calls,
        )

    expected: List[str] = gold.get("expected_fact_ids", [])
    forbidden: List[str] = gold.get("forbidden_fact_ids", [])
    forbidden_surface: List[str] = gold.get("forbidden_surface_ids", [])
    expected_state: Dict[str, Any] = gold.get("expected_state", {})
    mode = expected_state.get("mode", "answer")

    retrieved_set = set(retrieved_contents)
    expected_set = set(expected)

    # Retrieval recall and precision
    if not expected_set:
        recall = 1.0 if not retrieved_set else 0.0
        precision = 1.0 if not retrieved_set else 0.0
    else:
        hits = 0
        for exp in expected_set:
            if any(exp in r or r in exp for r in retrieved_set):
                hits += 1
        recall = hits / len(expected_set)
        precision = hits / len(retrieved_set) if retrieved_set else 0.0

    # Stale & Forbidden Exposure
    bad_count = 0
    stale_exp = False
    for f in forbidden:
        if any(f in r or r in f for r in retrieved_set):
            bad_count += 1
            stale_exp = True

    # Surface Leakage (canaries / private nonces)
    leakage = False
    for s in forbidden_surface:
        if any(s in r for r in retrieved_set):
            bad_count += 1
            leakage = True

    # Abstention checks
    is_empty = (len(retrieved_contents) == 0)
    correct_abs = False
    false_abs = False

    if mode == "abstain":
        correct_abs = is_empty
    elif mode == "answer":
        false_abs = is_empty and len(expected) > 0

    return CaseScore(
        case_id=case_id,
        status="ok",
        retrieval_recall=recall,
        retrieval_precision=precision,
        stale_exposure=stale_exp,
        leakage=leakage,
        forbidden_exposures=bad_count,
        correct_abstention=correct_abs,
        false_abstention=false_abs,
        latency_ms=latency_ms,
        tokens_used=tokens_used,
        provider_calls=provider_calls,
        details={
            "retrieved_count": len(retrieved_contents),
            "expected_count": len(expected),
            "bad_count": bad_count,
        },
    )


def aggregate_scores(
    system_name: str,
    version: str,
    capabilities: Dict[str, bool],
    case_scores: List[CaseScore],
) -> EvaluationSummary:
    """Non-compensatory aggregation according to SCORING.md contract."""
    total = len(case_scores)
    executed = [cs for cs in case_scores if cs.status == "ok"]
    unsupported = [cs for cs in case_scores if cs.status == "unsupported"]
    errors = [cs for cs in case_scores if cs.status == "error"]

    if not executed:
        return EvaluationSummary(
            system_name=system_name,
            version=version,
            capabilities=capabilities,
            total_cases=total,
            executed_cases=0,
            unsupported_cases=len(unsupported),
            error_cases=len(errors),
            trust_gate_passed=None,
            case_scores=case_scores,
        )

    n = len(executed)
    mean_recall = sum(cs.retrieval_recall for cs in executed) / n
    mean_precision = sum(cs.retrieval_precision for cs in executed) / n
    stale_count = sum(1 for cs in executed if cs.stale_exposure)
    stale_rate = stale_count / n
    leakage_count = sum(1 for cs in executed if cs.leakage)
    leakage_rate = leakage_count / n
    total_forbidden = sum(cs.forbidden_exposures for cs in executed)

    abstain_cases = [cs for cs in executed if cs.correct_abstention or cs.false_abstention or cs.details.get("expected_count") == 0]
    if abstain_cases:
        abs_acc = sum(1 for cs in abstain_cases if cs.correct_abstention) / len(abstain_cases)
    else:
        abs_acc = 1.0

    # Non-compensatory Trust Gate:
    # If any leakage occurred, or any forbidden exposure gate failed, trust_gate is 0
    trust_gate_passed = (leakage_count == 0) and (stale_count == 0)
    trust_gate = 1.0 if trust_gate_passed else 0.0

    # Composite Quality metric
    quality = (
        0.40 * mean_recall
        + 0.20 * mean_precision
        + 0.20 * abs_acc
        + 0.10 * (1.0 - stale_rate)
        + 0.10 * (1.0 - leakage_rate)
    )
    trust_adjusted = trust_gate * quality

    latencies = sorted(cs.latency_ms for cs in executed)
    mean_lat = sum(latencies) / n
    p95_idx = min(int(n * 0.95), n - 1)
    p95_lat = latencies[p95_idx]

    tot_calls = sum(cs.provider_calls for cs in executed)

    return EvaluationSummary(
        system_name=system_name,
        version=version,
        capabilities=capabilities,
        total_cases=total,
        executed_cases=n,
        unsupported_cases=len(unsupported),
        error_cases=len(errors),
        mean_retrieval_recall=mean_recall,
        mean_retrieval_precision=mean_precision,
        stale_exposure_rate=stale_rate,
        leakage_rate=leakage_rate,
        total_forbidden_exposures=total_forbidden,
        abstention_accuracy=abs_acc,
        trust_gate_passed=trust_gate_passed,
        trust_adjusted_score=trust_adjusted,
        mean_latency_ms=mean_lat,
        p95_latency_ms=p95_lat,
        total_provider_calls=tot_calls,
        case_scores=case_scores,
    )
