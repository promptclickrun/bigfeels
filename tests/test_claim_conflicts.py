"""Safety regressions for same-key facts; no model or live store required."""
import tempfile
import unittest
from bigfeels_mem.local import LocalClient


class ClaimConflictTests(unittest.TestCase):
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
