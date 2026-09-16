"""Safety regressions for same-key facts; no model or live store required."""
import tempfile
import random
import unittest
from unittest.mock import patch
from bigfeels_mem.retrieval import claims_compatible, conflicting_claim_ids
from bigfeels_mem.local import LocalClient


class ClaimConflictTests(unittest.TestCase):
    def test_interval_sweep_matches_pairwise_reference(self):
        rng = random.Random(410)
        for _ in range(100):
            records = []
            for index in range(40):
                start = rng.randrange(1, 20)
                end = rng.randrange(start + 1, 25)
                records.append({'id':str(index), 'content':rng.choice(['A', 'a.', 'B', 'C']),
                    'valid_from':f'2026-01-{start:02}T00:00:00Z',
                    'valid_until':None if rng.random() < .2 else f'2026-01-{end:02}T00:00:00Z'})
            targets = rng.sample(records, rng.randrange(len(records)))
            expected = {a['id'] for a in records for b in targets
                if (a['valid_until'] is None or b['valid_from'] < a['valid_until'])
                and (b['valid_until'] is None or a['valid_from'] < b['valid_until'])
                and not claims_compatible(a['content'], b['content'])}
            self.assertEqual(conflicting_claim_ids(records, targets), expected)

    def test_corrected_slot_cannot_be_recreated_by_reworded_old_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            client = LocalClient(td, auto_process=False)
            try:
                evidence = client.call('observe', {'space':'owner', 'source':'test', 'source_event_id':'one',
                    'session_id':'one', 'speaker':'user', 'content':'For backend scripts, we use PostgreSQL.',
                    'occurred_at':'2025-01-01T00:00:00Z'})
                old = client.call('remember', {'space':'owner', 'key':'database', 'content':'We use PostgreSQL.',
                    'evidence_ids':[evidence['id']], 'valid_from':'2025-01-01T00:00:00Z'})
                current = client.call('correct', {'id':old['id'], 'revision':old['revision'],
                    'content':'We use SQLite.', 'valid_from':'2026-01-01T00:00:00Z'})
                class Extractor:
                    def extract(self, evidence):
                        return [{'content':'We use PostgreSQL.', 'quote':'we use PostgreSQL.', 'key':'database', 'basis':'direct'},
                                {'content':'Backend scripts exist.', 'key':'other-slot', 'basis':'inferred'}]
                self.assertTrue(client.store.process_one(Extractor()))
                result = client.call('context', {'query':'database', 'budget':16000})
                self.assertEqual([m['id'] for m in result['memories']], [current['id']])
                self.assertFalse(result['warnings'])
                self.assertEqual(client.call('status', {})['memories'], 3)
            finally:
                client.close()

    def test_legacy_projection_does_not_compare_every_pair(self):
        with tempfile.TemporaryDirectory() as td:
            client = LocalClient(td, auto_process=False)
            try:
                with client.store.connection(True) as connection:
                    connection.executemany('INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        [(f'mem_{i}', 'owner', f'Atlas budget {i} USD', 'fact', 'direct', 'unspecified',
                          'atlas', 'active', 1, '2025-01-01T00:00:00.000000Z', '2025-01-01T00:00:00.000000Z', None)
                         for i in range(80)])
                with patch('bigfeels_mem.store.claims_compatible', wraps=claims_compatible) as compare:
                    unrelated = client.call('context', {'query':'unrelated zebras', 'budget':1})
                    self.assertEqual(compare.call_count, 0)
                    self.assertFalse(unrelated['warnings'])
                    result = client.call('context', {'query':'Atlas', 'budget':1})
                    self.assertTrue(result['warnings'])
                    self.assertEqual(result['trace']['matched'], 80)
                    self.assertLessEqual(compare.call_count, 240)
            finally:
                client.close()

    def test_new_conflict_relations_are_sparse(self):
        with tempfile.TemporaryDirectory() as td:
            client = LocalClient(td, auto_process=False)
            try:
                for i in range(12):
                    client.call('remember', {'space':'owner', 'key':'atlas', 'content':f'Atlas budget {i} USD'})
                with client.store.connection() as connection:
                    self.assertLessEqual(connection.execute('SELECT COUNT(*) FROM relations').fetchone()[0], 12)
                result = client.call('context', {'query':'Atlas', 'budget':16000})
                self.assertEqual(len(result['memories']), 12)
                self.assertEqual({m['status'] for m in result['memories']}, {'disputed'})
            finally:
                client.close()

    def test_changed_values_are_disputed_and_warn_on_recall(self):
        pairs = [
            ('Approved budget for project Atlas is 10000 USD', 'Approved budget for project Atlas is 20000 USD'),
            ('Approved budget for project Atlas is 1.5 USD', 'Approved budget for project Atlas is 5.1 USD'),
            ('Approved budget for project Atlas is -100 USD', 'Approved budget for project Atlas is 100 USD'),
            ('Deadline for project Atlas is 2026-10-01', 'Deadline for project Atlas is 2026-10-02'),
            ('Customer Atlas account owner is Alice', 'Customer Atlas account owner is Bob'),
            ('Alice reports to Bob for project Atlas', 'Bob reports to Alice for project Atlas'),
            ('Project Atlas is approved', 'Project Atlas is not approved'),
            ('Approved budget for project Atlas is 10000 USD', 'Approved budget for project Atlas is 10000 EUR'),
        ]
        for first, second in pairs:
            with self.subTest(first=first, second=second), tempfile.TemporaryDirectory() as td:
                with_client = LocalClient(td, auto_process=False)
                try:
                    a = with_client.call('remember', {'space':'owner', 'key':'atlas', 'content':first})
                    b = with_client.call('remember', {'space':'owner', 'key':'atlas', 'content':second})
                    for mid in (a['id'], b['id']):
                        self.assertEqual(with_client.call('inspect', {'id':mid})['status'], 'disputed')
                    recalled = with_client.call('search', {'query':'Atlas', 'budget':16000})
                    self.assertTrue(recalled.get('warnings'))
                finally:
                    with_client.close()

    def test_legacy_active_claims_warn_even_when_context_budget_omits_them(self):
        with tempfile.TemporaryDirectory() as td:
            client = LocalClient(td, auto_process=False)
            try:
                for amount in (10000, 20000):
                    client.call('remember', {'space':'owner', 'key':'atlas', 'content':f'Atlas budget is {amount} USD'})
                # Legacy storage state from the previously permissive heuristic.
                with client.store.connection(True) as connection:
                    connection.execute("UPDATE memories SET status='active'")
                    connection.execute("DELETE FROM relations WHERE kind='contradicts'")
                result = client.call('context', {'query':'Atlas', 'budget':1})
                self.assertEqual(result['memories'], [])
                self.assertTrue(result['warnings'])
                result = client.call('search', {'query':'Atlas', 'budget':16000})
                self.assertEqual([m['status'] for m in result['memories']], ['disputed', 'disputed'])
                self.assertFalse(client.call('search', {'query':'unrelated zebras'})['warnings'])
                with client.store.connection() as connection:
                    self.assertEqual({row[0] for row in connection.execute('SELECT status FROM memories')}, {'active'})
            finally:
                client.close()

    def test_cosmetic_agreement_and_cross_scope_records_do_not_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            client = LocalClient(td, spaces=['customer:a', 'customer:b'], auto_process=False)
            try:
                a = client.call('remember', {'space':'customer:a', 'key':'atlas', 'content':'Atlas budget is 100 USD.'})
                repeat = client.call('remember', {'space':'customer:a', 'key':'atlas', 'content':'ATLAS budget is 100 USD'})
                self.assertEqual(a['id'], repeat['id'])
                b = client.call('remember', {'space':'customer:b', 'key':'atlas', 'content':'Atlas budget is 200 USD'})
                result = client.call('search', {'query':'Atlas', 'budget':16000})
                self.assertFalse(result['warnings'])
                self.assertEqual({m['id'] for m in result['memories']}, {a['id'], b['id']})
            finally:
                client.close()

    def test_same_claim_and_nonoverlapping_validity_do_not_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            client = LocalClient(td, auto_process=False)
            try:
                first = client.call('remember', {'space':'owner', 'key':'atlas', 'content':'Atlas budget is 100 USD', 'valid_from':'2026-01-01T00:00:00Z', 'valid_until':'2026-02-01T00:00:00Z'})
                second = client.call('remember', {'space':'owner', 'key':'atlas', 'content':'Atlas budget is 200 USD', 'valid_from':'2026-02-01T00:00:00Z'})
                self.assertEqual(client.call('inspect', {'id':first['id']})['status'], 'active')
                self.assertEqual(second['status'], 'active')
            finally:
                client.close()


if __name__ == '__main__':
    unittest.main()
