import importlib.util
from pathlib import Path
import unittest


class ReplayTests(unittest.TestCase):
    def test_replay_detects_stale_answers_and_private_scope_leaks(self):
        path = Path(__file__).resolve().parents[1] / 'benchmarks' / 'replay.py'
        self.assertTrue(path.exists(), 'The reproducible continuity replay is not implemented')
        spec = importlib.util.spec_from_file_location('replay', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.run()
        self.assertEqual(report['policies']['bigfeels']['forbidden_exposures'], 0)
        self.assertEqual(report['policies']['bigfeels']['expected_covered'], report['expected_total'])
        self.assertGreater(report['policies']['simple_hybrid']['forbidden_exposures'], 0)
        self.assertEqual(report['policies']['no_memory']['expected_covered'], 0)
        self.assertEqual(report['policies']['simple_hybrid']['fixture_embedding_calls'], 6)


if __name__ == '__main__':
    unittest.main()
