"""Evidence TTL is enforced without depending on a running extractor."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bigfeels_mem.store import Store, Principal


class EvidenceRetentionTests(unittest.TestCase):
    def test_capture_without_extraction_consent_never_enters_worker_queue(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / 'memory.sqlite')
            p = Principal('test', ('owner',))
            payload = {'space':'owner','source':'test','source_event_id':'one','session_id':'one',
                'speaker':'user','content':'Retain only, do not submit','queue_extraction':False}
            saved = store.dispatch(p, 'observe', payload)
            self.assertEqual(saved['status'], 'stored')
            # Replaying with a broader setting cannot silently upgrade consent.
            self.assertEqual(store.dispatch(p, 'observe', {**payload, 'queue_extraction':True})['status'], 'duplicate')
            class Extractor:
                def extract(self, evidence):
                    raise AssertionError('Unapproved model submission')
            self.assertFalse(store.process_one(Extractor()))
            self.assertEqual(store.dispatch(p, 'export', {})['jobs'], [])

    def test_expired_source_is_hidden_from_inspect_export_and_recall(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / 'memory.sqlite')
            p = Principal('test', ('owner',))
            e = store.dispatch(p, 'observe', {'space':'owner','source':'test','source_event_id':'one','session_id':'one','speaker':'user','content':'Approved distilled fact','expires_at':'2026-02-01T00:00:00Z'})
            m = store.dispatch(p, 'remember', {'space':'owner','content':'Approved distilled fact','evidence_ids':[e['id']]})
            self.assertIsNone(store.dispatch(p, 'inspect', {'id':e['id']})['content'])
            self.assertFalse(store.dispatch(p, 'inspect', {'id':m['id']})['source_available'])
            self.assertIsNone(store.dispatch(p, 'export', {})['evidence'][0]['content'])
            self.assertFalse(store.dispatch(p, 'search', {'query':'distilled'})['memories'][0]['source_available'])

    def test_expiry_during_extraction_cannot_commit_new_knowledge(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / 'memory.sqlite')
            p = Principal('test', ('owner',))
            with patch('bigfeels_mem.store.now', return_value='2026-01-01T00:00:00.000000Z'):
                store.dispatch(p, 'observe', {'space':'owner','source':'test','source_event_id':'one','session_id':'one','speaker':'user','content':'Transient source','expires_at':'2026-01-02T00:00:00Z'})
            clock = patch('bigfeels_mem.store.now', return_value='2026-01-01T00:00:00.000000Z')
            now = clock.start()
            self.addCleanup(clock.stop)
            class Extractor:
                def extract(self, evidence):
                    now.return_value='2026-01-03T00:00:00.000000Z'
                    return [{'content':'Transient source','quote':'Transient source','basis':'direct'}]
            self.assertFalse(store.process_one(Extractor()))
            self.assertEqual(store.dispatch(p, 'export', {})['memories'], [])


if __name__ == '__main__':
    unittest.main()
