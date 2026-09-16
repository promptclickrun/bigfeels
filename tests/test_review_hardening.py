"""Review regressions: real storage, synthetic evidence, no host/network access."""
import tempfile
import unittest
from pathlib import Path

from bigfeels_mem.store import Store, Principal, MemoryError


PARAGRAPH = (
    'I prefer Python. For the reporting service, please keep the command line simple. '
    'The first release should read local files and produce a report that I can inspect. '
    'Do not add a database server or cloud account merely for this small utility. '
    'When an input row is invalid, keep the original file untouched and show the row number. '
    'The program needs to be understandable to a teammate who did not write it. '
    'A few clear functions and useful tests are more valuable here than a large framework.'
)


class Extractor:
    def __init__(self, claim='I prefer Python.', key='preferred-language', **fields):
        self.claim, self.key, self.fields = claim, key, fields

    def extract(self, evidence):
        return [dict(content=self.claim, quote=self.claim, basis='direct',
                     kind='preference', key=self.key, **self.fields)]


class ReviewHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'memory.sqlite')
        self.principal = Principal('review-regression', ('owner',))

    def call(self, operation, **payload):
        return self.store.dispatch(self.principal, operation, payload)

    def observe(self, content, event_id='1', **extra):
        payload = dict(space='owner', source='synthetic-review',
                       source_event_id=event_id, session_id='review',
                       speaker='user', content=content)
        payload.update(extra)
        return self.call('observe', **payload)

    def save(self, content, **extra):
        return self.call('remember', space='owner', content=content, **extra)

    def test_default_recall_returns_grounded_claim_from_ordinary_paragraph(self):
        event = self.observe(PARAGRAPH)
        self.assertTrue(self.store.process_one(Extractor()))
        result = self.call('context', query='Python')
        self.assertTrue(result['memories'], 'Matching ordinary evidence vanished at the default budget')
        self.assertIn('Python', result['memories'][0]['content'])
        self.assertLessEqual(result['tokens'], result['trace']['budget'])
        self.assertEqual(self.call('inspect', id=event['id'])['content'], PARAGRAPH)

    def test_repeated_corroboration_does_not_make_an_active_fact_unretrievable(self):
        for index in range(20):
            self.observe('I prefer Python.', str(index))
            self.assertTrue(self.store.process_one(Extractor()))
        result = self.call('context', query='Python')
        self.assertEqual(len(result['memories']), 1)
        detail = self.call('inspect', id=result['memories'][0]['id'])
        self.assertEqual(len(detail['evidence_ids']), 20, 'Full lineage must survive context compaction')
        self.assertEqual(detail['status'], 'active')
        self.assertLessEqual(result['tokens'], result['trace']['budget'])

    def test_same_extracted_claim_does_not_conflict_with_its_added_context(self):
        for index, content in enumerate(['I prefer Python.', 'For backend scripts, I prefer Python.']):
            self.observe(content, str(index))
            self.assertTrue(self.store.process_one(Extractor()))
        result = self.call('context', query='Python', budget=3200)
        self.assertTrue(result['memories'])
        self.assertNotIn('disputed', {m['status'] for m in result['memories']})
        source_ids = {eid for m in result['memories']
                      for eid in self.call('inspect', id=m['id'])['evidence_ids']}
        self.assertEqual(len(source_ids), 2)

    def test_tool_speaker_alone_cannot_certify_a_contradicted_success_claim(self):
        evidence = self.observe('Deployment failed: authentication denied.', speaker='tool')
        try:
            memory = self.save('The deployment succeeded.', basis='observed',
                               outcome='verified', evidence_ids=[evidence['id']])
        except MemoryError as error:
            self.assertEqual(error.status, 400)
        else:
            self.assertNotEqual(memory['outcome'], 'verified',
                                'Caller attestation must not be labeled independently verified')

    def test_natural_unrelated_question_does_not_match_only_a_common_word(self):
        self.save('I travel by bicycle.')
        self.assertTrue(self.call('context', query='bicycle')['memories'])
        self.assertEqual(self.call('context', query='What do I want for dinner?')['memories'], [])

    def test_delayed_extraction_cannot_resurrect_a_superseded_claim(self):
        evidence = self.observe('We use PostgreSQL.', occurred_at='2025-01-01T00:00:00Z')
        old = self.save('We use PostgreSQL.', key='database',
                        evidence_ids=[evidence['id']], valid_from='2025-01-01T00:00:00Z')
        new = self.call('correct', id=old['id'], revision=old['revision'],
                        content='We use SQLite.', valid_from='2026-01-01T00:00:00Z')
        self.assertTrue(self.store.process_one(Extractor('We use PostgreSQL.', 'database')))
        result = self.call('context', query='database PostgreSQL SQLite', budget=3200)
        self.assertEqual([m['id'] for m in result['memories']], [new['id']])
        self.assertEqual(result['memories'][0]['status'], 'active')

    def test_invalid_only_extraction_is_not_successful_empty_learning(self):
        self.observe('I prefer Python.')
        class InvalidExtractor:
            def extract(self, evidence):
                return [{'content':'I prefer Python.', 'quote':'I prefer Python.',
                         'kind':'preferences', 'basis':'direct'}]
        self.store.process_one(InvalidExtractor())
        status = self.call('status')
        self.assertEqual(status['memories'], 0)
        self.assertEqual(status['queue'].get('done', 0), 0)
        self.assertTrue(status['queue'].get('pending', 0) or status['queue'].get('failed', 0))

    def test_deletion_preview_covers_collateral_and_rejects_stale_confirmation(self):
        a = self.observe('My hometown is Cedarville. I prefer Python.', 'a')
        b = self.observe('I prefer Python. I drink tea.', 'b')
        town = self.save('My hometown is Cedarville.', evidence_ids=[a['id']])
        language = self.save('I prefer Python.', evidence_ids=[a['id'], b['id']])
        tea = self.save('I drink tea.', evidence_ids=[b['id']])
        piano = self.save('I play piano.')
        plan = self.call('forget_preview', id=town['id'])
        self.assertEqual({m['id'] for m in plan['memories']},
                         {town['id'], language['id'], tea['id']})
        self.assertEqual(plan['counts'], {'memories':3, 'evidence':2})
        with self.assertRaises(MemoryError):
            self.call('forget', id=town['id'])
        extra = self.save('Another sourced note.', evidence_ids=[b['id']])
        with self.assertRaises(MemoryError) as stale:
            self.call('forget', id=town['id'], plan_token=plan['plan_token'])
        self.assertEqual(stale.exception.status, 409)
        for memory in [town, language, tea, piano, extra]:
            self.assertEqual(self.call('inspect', id=memory['id'])['id'], memory['id'])
        fresh = self.call('forget_preview', id=town['id'])
        deleted = self.call('forget', id=town['id'], plan_token=fresh['plan_token'])
        self.assertEqual(deleted['memories'], len(fresh['memories']))
        self.assertEqual(self.call('inspect', id=piano['id'])['content'], 'I play piano.')
