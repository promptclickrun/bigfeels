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
from benchmarks.harness.longmemeval_runner import evaluate_longmemeval_subset
from benchmarks.harness.scorer import CaseScore, aggregate_scores


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
