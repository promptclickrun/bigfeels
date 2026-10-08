"""Regression coverage for the host-neutral benchmark harness."""
import unittest
from unittest.mock import patch

from benchmarks.harness.base_adapter import (
    BaseMemoryAdapter,
    CapabilityProfile,
    NormalizedRecord,
    OpResult,
    RetrievalResult,
    StorageStats,
)
from benchmarks.harness.beam_answers import judge_score, kendall_tau_b
from benchmarks.harness.longmemeval_runner import evaluate_longmemeval_subset
from benchmarks.harness.runner import generate_markdown_report, run_evaluation
from benchmarks.harness.scorer import CaseScore, aggregate_scores, score_case


class RecordingAdapter(BaseMemoryAdapter):
    name = "recording"
    version = "test"
    observed = []

    def capabilities(self):
        return CapabilityProfile()

    def create_isolated_run(self, run_id, seed, spaces, principals):
        type(self).observed = []
        return object()

    def apply_op(self, handle, operation):
        type(self).observed.append(operation["content"])
        return OpResult(status="ok")

    def retrieve(self, handle, actor, query, as_of=None, budget=3200):
        return RetrievalResult(
            status="ok",
            records=[NormalizedRecord("one", self.observed[0])],
        )

    def restart(self, handle):
        return handle

    def get_storage_stats(self, handle):
        return StorageStats()

    def teardown(self, handle):
        return None


class GreedyAdapter(RecordingAdapter):
    """Ignores the requested budget, like an adapter with its own accounting."""
    budgets = []

    def retrieve(self, handle, actor, query, as_of=None, budget=3200):
        type(self).budgets.append(budget)
        return RetrievalResult(
            status="ok",
            records=[NormalizedRecord(str(i), "alpha beta " * 40) for i in range(3)],
        )


class FailingIngestionAdapter(RecordingAdapter):
    def apply_op(self, handle, operation):
        return OpResult(status="error", safe_diagnostics={"kind": "fixture_failure"})


class BenchmarkHarnessTests(unittest.TestCase):
    def test_longmemeval_preserves_dataset_session_ids(self):
        fixture = [{
            "question_id": "q-session-id",
            "question": "Which session contains the answer?",
            "answer": "not copied into the turn",
            "question_date": None,
            "answer_session_ids": ["actual-session-42"],
            "haystack_session_ids": ["actual-session-42"],
            "haystack_sessions": [[{"role": "user", "content": "Needle text."}]],
        }]
        with patch.dict(
            "benchmarks.harness.longmemeval_runner.ADAPTER_MAP",
            {"recording": RecordingAdapter},
            clear=True,
        ):
            result = evaluate_longmemeval_subset(fixture, ["recording"], limit=1)
        self.assertEqual(result["recording"]["session_recall_at_k"], 1.0)
        self.assertTrue(RecordingAdapter.observed[0].startswith("[actual-session-42]"))

        skipped = dict(fixture[0], question_id="skipped", haystack_session_ids=["skipped-session"])
        with patch.dict(
            "benchmarks.harness.longmemeval_runner.ADAPTER_MAP",
            {"recording": RecordingAdapter},
            clear=True,
        ):
            evaluate_longmemeval_subset([skipped, *fixture], ["recording"], limit=1, offset=1)
        self.assertTrue(RecordingAdapter.observed[0].startswith("[actual-session-42]"))

    def test_longmemeval_accepts_numeric_gold_answers(self):
        fixture = [{
            "question_id": "q-numeric-answer",
            "question": "How many?",
            "answer": 7,
            "question_date": None,
            "answer_session_ids": ["number-session"],
            "haystack_session_ids": ["number-session"],
            "haystack_sessions": [[{"role": "user", "content": "The answer is 7."}]],
        }]
        with patch.dict(
            "benchmarks.harness.longmemeval_runner.ADAPTER_MAP",
            {"recording": RecordingAdapter},
            clear=True,
        ):
            result = evaluate_longmemeval_subset(fixture, ["recording"], limit=1)
        self.assertEqual(result["recording"]["session_recall_at_k"], 1.0)

    def test_longmemeval_reports_ingestion_failure_instead_of_scoring_empty_recall(self):
        fixture = [{
            "question_id": "q-ingestion-failure",
            "question": "What failed?",
            "answer": "ingestion",
            "answer_session_ids": ["failed-session"],
            "haystack_session_ids": ["failed-session"],
            "haystack_sessions": [[{"role": "user", "content": "Ingestion failed."}]],
        }]
        with patch.dict(
            "benchmarks.harness.longmemeval_runner.ADAPTER_MAP",
            {"failing": FailingIngestionAdapter},
            clear=True,
        ):
            result = evaluate_longmemeval_subset(fixture, ["failing"], limit=1)
        self.assertEqual(result["failing"]["ingestion_failures"], 1)
        self.assertEqual(result["failing"]["instances"][0]["status"], "invalid_ingestion")

    def test_longmemeval_chunks_oversized_turns_for_every_adapter(self):
        fixture = [{
            "question_id": "q-long-turn",
            "question": "Where is the long turn?",
            "answer": "needle",
            "answer_session_ids": ["long-session"],
            "haystack_session_ids": ["long-session"],
            "haystack_sessions": [[{"role": "user", "content": "x" * 25_000}]],
        }]
        with patch.dict(
            "benchmarks.harness.longmemeval_runner.ADAPTER_MAP",
            {"recording": RecordingAdapter},
            clear=True,
        ):
            evaluate_longmemeval_subset(fixture, ["recording"], limit=1)
        self.assertEqual(len(RecordingAdapter.observed), 3)
        self.assertTrue(all(len(content) <= 12_100 for content in RecordingAdapter.observed))

    def test_beam_scoring_helpers_match_the_released_evaluator(self):
        # scipy.stats.kendalltau(variant="b") values, including a joint tie.
        self.assertEqual(kendall_tau_b([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertAlmostEqual(kendall_tau_b([1, 2, 3, 5, 5], [1, 3, 2, 5, 5]), 7 / 9)
        self.assertEqual(judge_score('```json\n{"score": 0.5, "reason": "a } brace"}\n```'), 0.5)
        self.assertEqual(judge_score('Result -> "score": 1.0'), 1.0)

    def test_scorer_requires_complete_facts_and_keeps_precision_bounded(self):
        gold = {"expected_fact_ids": ["alpha beta", "alpha gamma"]}
        fragment = score_case("fragment", gold, ["alpha"], 0.0, 0)
        self.assertEqual((fragment.retrieval_recall, fragment.retrieval_precision), (0.0, 0.0))
        partial = score_case("partial", gold, ["note: alpha beta.", "unrelated"], 0.0, 0)
        self.assertEqual((partial.retrieval_recall, partial.retrieval_precision), (0.5, 0.5))
        both = score_case("both", gold, ["alpha beta; alpha gamma"], 0.0, 0)
        self.assertEqual((both.retrieval_recall, both.retrieval_precision), (1.0, 1.0))

    def test_declared_budget_is_a_ceiling_for_every_case(self):
        case = {"case_id": "c1", "capability": [], "operations": [],
                "query": {"text": "alpha", "budget": 3200}}
        gold = {"c1": {"expected_fact_ids": ["alpha beta"]}}
        GreedyAdapter.budgets = []
        tiny = run_evaluation(GreedyAdapter, [case], gold, budget=1)
        self.assertEqual(GreedyAdapter.budgets, [1])
        self.assertEqual(tiny.case_scores[0].tokens_used, 0)
        self.assertEqual(tiny.case_scores[0].details["retrieved_count"], 0)
        # Each record costs 440 content bytes plus 64; two fit in 1100.
        roomy = run_evaluation(GreedyAdapter, [case], gold, budget=1100)
        self.assertEqual(roomy.case_scores[0].tokens_used, 1008)
        self.assertEqual(roomy.case_scores[0].details["trimmed_for_budget"], 1)

    def test_report_describes_the_actual_run(self):
        case = {"case_id": "c1", "capability": [], "operations": [], "query": {"text": "alpha"}}
        summary = run_evaluation(GreedyAdapter, [case], {}, budget=1).to_dict()
        report = generate_markdown_report({
            "benchmark": "b", "corpus_version": "v", "corpus_sha256": "s", "timestamp": "t",
            "environment": {"os": "Linux 6.1", "python_version": "3.12", "arch": "x86_64"},
            "budget_bytes": 1, "systems": {"recording": summary}, "cases_metadata": [],
        })
        self.assertIn("`Linux 6.1`", report)
        self.assertNotIn("macOS", report)
        self.assertNotIn("Mnemosyne", report)

    def test_trust_gate_is_not_applicable_when_no_case_executes(self):
        summary = aggregate_scores(
            "unsupported",
            "1",
            {},
            [CaseScore(case_id="one", status="unsupported")],
        )
        self.assertIsNone(summary.trust_gate_passed)


if __name__ == "__main__":
    unittest.main()
