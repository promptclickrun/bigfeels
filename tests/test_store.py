import concurrent.futures
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from bigfeels_mem.schema import SCHEMA
    from bigfeels_mem.store import DELETE_PLAN_TTL_SECONDS, Store, MemoryError
except ImportError:
    Store = None


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Store, 'The durable memory store is not implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'memory.sqlite'
        self.store = Store(self.path)
        self.store.create_space('project:alpha')
        self.owner = self.store.authenticate(self.store.pair('hermes', ['owner', 'project:alpha']))
        self.other = self.store.authenticate(self.store.pair('openclaw', ['owner']))

    def call(self, op, **data):
        return self.store.dispatch(self.owner, op, data)

    def remember(self, content='Use SQLite for the memory service', **kw):
        return self.call('remember', space='owner', content=content, **kw)

    def test_nonempty_unknown_database_is_rejected_without_mutation(self):
        malformed = Path(self.tmp.name) / 'malformed.sqlite'
        with closing(sqlite3.connect(malformed)) as connection:
            connection.execute('CREATE TABLE jobs (evidence_id TEXT PRIMARY KEY)')
            connection.commit()
        before = malformed.read_bytes()
        with self.assertRaises(MemoryError) as error:
            Store(malformed)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(malformed.read_bytes(), before)

    def test_existing_v1_database_receives_additive_job_diagnostics(self):
        legacy = Path(self.tmp.name) / 'legacy-v1.sqlite'
        old_schema = SCHEMA.replace(
            ",\n accepted_count INTEGER NOT NULL DEFAULT 0,\n"
            " rejected_count INTEGER NOT NULL DEFAULT 0,\n"
            " rejection_reason TEXT)",
            ")",
        )
        self.assertNotEqual(old_schema, SCHEMA)
        with closing(sqlite3.connect(legacy)) as connection:
            connection.executescript(old_schema)
            connection.commit()
        Store(legacy)
        with closing(sqlite3.connect(legacy)) as connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}
        self.assertTrue({'accepted_count', 'rejected_count', 'rejection_reason'} <= columns)

    def test_existing_v1_database_rebuilds_recall_index_once(self):
        # The original v1 layout indexed whole memories without stemming.
        legacy_index = (
            'CREATE VIRTUAL TABLE memory_fts USING fts5(id UNINDEXED, content, key);\n'
            'CREATE TRIGGER memory_insert AFTER INSERT ON memories BEGIN\n'
            ' INSERT INTO memory_fts(id,content,key) VALUES(new.id,new.content,new.key);\nEND;\n'
            'CREATE TRIGGER memory_delete AFTER DELETE ON memories BEGIN\n'
            ' DELETE FROM memory_fts WHERE id=old.id;\nEND;\n'
            'CREATE TRIGGER memory_update AFTER UPDATE OF content,key ON memories BEGIN\n'
            ' DELETE FROM memory_fts WHERE id=old.id;\n'
            ' INSERT INTO memory_fts(id,content,key) VALUES(new.id,new.content,new.key);\nEND;\n')
        old_schema = SCHEMA[:SCHEMA.index('DROP TRIGGER')] + legacy_index
        legacy = Path(self.tmp.name) / 'legacy-index.sqlite'
        filler = ' '.join(f'filler{index}' for index in range(80))
        with closing(sqlite3.connect(legacy)) as connection:
            connection.executescript(old_schema)
            connection.execute(
                "INSERT INTO memories VALUES ('mem_late','owner',?,'fact','direct','unspecified',"
                "NULL,'active',1,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z',NULL)",
                (filler + ' Telescopes are my hobby.',))
            connection.commit()
        store = Store(legacy)
        owner = store.authenticate(store.pair('reader', ['owner']))
        found = store.dispatch(owner, 'context', {'query': 'telescope', 'budget': 32000})
        self.assertEqual([m['id'] for m in found['memories']], ['mem_late'])
        with closing(sqlite3.connect(legacy)) as connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(memory_fts)')}
            layout = connection.execute("SELECT value FROM metadata WHERE key='recall_index'").fetchone()
        self.assertIn('opening', columns)
        self.assertIsNotNone(layout)

    def test_deletion_preview_token_expires_without_deleting(self):
        memory = self.remember('Short-lived deletion confirmation.')
        with patch('bigfeels_mem.store.time.time', return_value=1_000):
            preview = self.call('forget_preview', id=memory['id'])
        with patch(
            'bigfeels_mem.store.time.time',
            return_value=1_000 + DELETE_PLAN_TTL_SECONDS + 1,
        ):
            with self.assertRaises(MemoryError) as expired:
                self.call('forget', id=memory['id'], plan_token=preview['plan_token'])
        self.assertEqual(expired.exception.status, 409)
        self.assertEqual(self.call('inspect', id=memory['id'])['content'], memory['content'])

    def observe(self, **kw):
        d = dict(space='owner', source='hermes', source_event_id='turn-1',
                 session_id='one', speaker='user', content='I prefer Python.', captured=True)
        d.update(kw)
        return self.call('observe', **d)

    def test_durable_capture_replay_and_conflicting_payload(self):
        event = self.observe()
        self.assertEqual(event['id'], self.observe()['id'])
        with self.assertRaises(MemoryError) as cm:
            self.observe(content='Different payload')
        self.assertEqual(cm.exception.status, 409)
        restarted = Store(self.path)
        self.assertEqual(restarted.dispatch(self.owner, 'inspect', {'id': event['id']})['content'], 'I prefer Python.')

    def test_scope_is_checked_before_search_and_inspection(self):
        m = self.call('remember', space='project:alpha', content='Secret codename penguin')
        self.assertEqual(self.store.dispatch(self.other, 'search', {'query': 'penguin'})['memories'], [])
        for op, data in [('inspect', {'id': m['id']}), ('search', {'query': 'penguin', 'spaces': ['project:alpha']})]:
            with self.assertRaises(MemoryError) as cm:
                self.store.dispatch(self.other, op, data)
            self.assertEqual(cm.exception.status, 403)

    def test_correction_is_atomic_revision_checked_and_temporal(self):
        old = self.remember('We use PostgreSQL', key='database', valid_from='2025-01-01T00:00:00Z')
        new = self.call('correct', id=old['id'], revision=1, content='We use SQLite', valid_from='2026-01-01T00:00:00Z')
        self.assertEqual(new['content'], 'We use SQLite')
        now = self.call('context', query='database SQLite PostgreSQL')['memories']
        self.assertEqual([m['id'] for m in now], [new['id']])
        then = self.call('context', query='PostgreSQL', as_of='2025-06-01T00:00:00Z')['memories']
        self.assertEqual([m['id'] for m in then], [old['id']])
        with self.assertRaises(MemoryError) as cm:
            self.call('correct', id=old['id'], revision=1, content='Use Redis')
        self.assertEqual(cm.exception.status, 409)

    def test_conflict_is_visible_without_arbitrary_winner(self):
        a = self.remember('Use PostgreSQL', key='database')
        b = self.remember('Use SQLite', key='database')
        found = self.call('search', query='Use')['memories']
        self.assertEqual({m['id'] for m in found}, {a['id'], b['id']})
        self.assertEqual({m['status'] for m in found}, {'disputed'})

    def test_echo_preserves_lineage_and_does_not_create_evidence(self):
        e = self.observe()
        echoed = self.observe(source='openclaw', source_event_id='copy', origin_ids=[e['id']])
        self.assertEqual(echoed['status'], 'echo')
        self.assertEqual(self.call('status')['evidence'], 1)

    def test_forgetting_cascades_and_blocks_source_replay_after_restart(self):
        e = self.observe()
        m = self.remember('I prefer Python.', evidence_ids=[e['id']])
        self.call('forget', id=m['id'])
        self.assertEqual(self.call('search', query='Python')['memories'], [])
        self.store = Store(self.path)
        self.assertEqual(self.observe()['status'], 'deleted')
        exported = self.call('export')
        self.assertNotIn('I prefer Python', str(exported))
        self.assertTrue(exported['tombstones'])

    def test_capture_off_and_redaction_precede_storage(self):
        self.assertEqual(self.observe(captured=False)['status'], 'skipped')
        self.assertEqual(self.call('status')['evidence'], 0)
        e = self.observe(content='Authorization: Bearer secret-token-123\napi_key=supersecret123')
        content = self.call('inspect', id=e['id'])['content']
        self.assertNotIn('secret-token-123', content)
        self.assertNotIn('supersecret123', content)

    def test_unrelated_queries_abstain_and_budget_is_bounded(self):
        self.remember()
        self.assertEqual(self.call('context', query='holiday beaches')['memories'], [])
        self.assertEqual(self.call('context', query='SQLite', budget=1)['memories'], [])
        result = self.call('context', query='SQLite', budget=1600)
        self.assertLessEqual(result['tokens'], 1600)
        self.assertTrue(result['memories'][0]['evidence_ids'])
        self.assertTrue(result['memories'][0]['source_available'])

    def test_late_keywords_and_keys_remain_retrievable(self):
        filler = ' '.join(f'filler{index}' for index in range(80))
        early = self.remember('Astronomy is the chosen hobby. ' + filler)
        late = self.remember(filler + ' Astronomy is the chosen hobby.')
        keyed = self.remember(filler + ' ' + filler, key='hobby-telescope')
        found = self.call('context', query='astronomy', budget=32000)['memories']
        self.assertEqual([m['id'] for m in found], [early['id'], late['id']])
        found = self.call('context', query='telescope', budget=32000)['memories']
        self.assertEqual([m['id'] for m in found], [keyed['id']])

    def test_proposal_and_inference_cannot_claim_verified_outcome(self):
        m = self.remember('I will deploy tomorrow', kind='task', basis='inferred', outcome='proposed')
        self.assertEqual(m['status'], 'candidate')
        self.assertEqual(m['outcome'], 'proposed')
        self.assertEqual(self.call('context', query='deploy')['memories'], [])

    def test_durable_worker_promotes_only_grounded_statements(self):
        self.store.memory_policy = 'grounded'
        self.observe()
        class Extractor:
            def extract(self, e):
                return [dict(content='I prefer Python.', quote='I prefer Python.', basis='direct', kind='preference'),
                        dict(content='The deployment succeeded', quote='made up', basis='observed', outcome='verified')]
        self.assertTrue(self.store.process_one(Extractor()))
        result = self.call('context', query='Python')['memories']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['basis'], 'direct')
        self.assertEqual(self.call('context', query='deployment')['memories'], [])
        self.assertFalse(self.store.process_one(Extractor()))

    def test_batch_extraction_checks_each_item_against_its_own_evidence(self):
        self.store.memory_policy = 'grounded'
        texts = ['I prefer Python.', 'I live in Lisbon.', 'I drink green tea.']
        for index, text in enumerate(texts):
            self.observe(source_event_id=f'batch-{index}', content=text)
        self.call('observe', space='project:alpha', source='native-test', source_event_id='other-space',
                  session_id='s1', speaker='user', content='I prefer Rust.', captured=True)
        calls = []
        class Extractor:
            def extract(self, e):
                raise AssertionError('batches must use extract_batch')
            def extract_batch(self, items):
                calls.append([e['content'] for e in items])
                # The Lisbon quote is attributed to the wrong item, so it is ungrounded there.
                return [[dict(content='I prefer Python.', quote='I prefer Python.', basis='direct')],
                        [dict(content='I prefer Python.', quote='I prefer Python.', basis='direct')],
                        [dict(content='I drink green tea.', quote='I drink green tea.', basis='direct')]]
        self.assertEqual(self.store.process_batch(Extractor(), limit=8), 3)
        self.assertEqual(calls, [texts])
        self.assertEqual(len(self.call('context', query='Python', spaces=['owner'])['memories']), 1)
        self.assertEqual(len(self.call('context', query='green tea', spaces=['owner'])['memories']), 1)
        self.assertEqual(self.call('context', query='Rust')['memories'], [])
        class Broken:
            def extract_batch(self, items):
                raise RuntimeError('offline')
            extract = extract_batch
        self.assertEqual(self.store.process_batch(Broken(), limit=8), 0)
        with self.store.connection() as c:
            pending = c.execute("SELECT state,error FROM jobs j JOIN evidence e ON e.id=j.evidence_id "
                                "WHERE e.space='project:alpha'").fetchone()
        self.assertEqual((pending['state'], pending['error']), ('pending', 'provider_error'))

    def test_failed_provider_keeps_job_and_content_out_of_error(self):
        self.observe()
        class Broken:
            def extract(self, e):
                raise RuntimeError('private-content secret')
        self.assertFalse(self.store.process_one(Broken()))
        status = self.call('status')
        self.assertEqual(status['queue']['pending'], 1)
        self.assertNotIn('private-content', str(status))

    def test_expired_evidence_is_unavailable_but_knowledge_is_traceable(self):
        e = self.observe(expires_at='2000-01-01T00:00:00Z')
        m = self.remember('Python preference', evidence_ids=[e['id']])
        self.store.maintenance()
        detail = self.call('inspect', id=m['id'])
        self.assertFalse(detail['source_available'])
        self.assertIsNone(detail['evidence'][0]['content'])

    def test_export_restore_preserves_corrections_and_tombstones_without_tokens(self):
        self.remember()
        bundle = self.call('export')
        self.assertNotIn('credentials', bundle)
        restored = Store(Path(self.tmp.name) / 'restored.sqlite')
        restored.restore(bundle)
        p = restored.authenticate(restored.pair('reader', ['owner']))
        self.assertEqual(len(restored.dispatch(p, 'search', {'query': 'SQLite'})['memories']), 1)

    def test_concurrent_duplicate_capture_creates_one_job(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(lambda _: self.observe()['id'], range(12)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.call('status')['queue']['pending'], 1)

    def test_cannot_attach_evidence_from_another_space(self):
        e = self.observe(space='project:alpha')
        with self.assertRaises(MemoryError):
            self.remember(evidence_ids=[e['id']])

    def test_inflight_extraction_cannot_resurrect_forgotten_evidence(self):
        e = self.observe()
        store, p = self.store, self.owner
        class Extractor:
            def extract(self, evidence):
                store.dispatch(p, 'forget', {'id': e['id']})
                return [dict(content='I prefer Python.', quote='I prefer Python.', basis='direct')]
        self.assertFalse(self.store.process_one(Extractor()))
        self.assertEqual(self.call('status')['memories'], 0)

    def test_inflight_embedding_cannot_resurrect_forgotten_memory(self):
        m = self.remember()
        store, p = self.store, self.owner
        class Embedder:
            model = 'fixture-v1'
            def embed(self, texts):
                store.dispatch(p, 'forget', {'id': m['id']})
                return [[1, 0] for _ in texts]
        self.assertTrue(hasattr(self.store, 'process_embeddings'), 'Background embedding is not implemented')
        self.store.process_embeddings(Embedder())
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM vectors').fetchone()[0], 0)

    def test_semantic_index_enables_recall_and_outage_falls_back(self):
        self.remember('I travel by bicycle')
        class Embedder:
            model = 'fixture-v1'
            def embed(self, texts):
                return [[1, 0] for _ in texts]
        self.assertTrue(hasattr(self.store, 'process_embeddings'), 'Background embedding is not implemented')
        self.store.process_embeddings(Embedder())
        self.store.embedder = Embedder()
        result = self.call('context', query='cycling', budget=1600)
        self.assertEqual(result['memories'][0]['reason'], ['semantic'])
        class Broken:
            model = 'fixture-v1'
            def embed(self, texts):
                raise RuntimeError('offline')
        self.store.embedder = Broken()
        result = self.call('context', query='bicycle', budget=1600)
        self.assertEqual(len(result['memories']), 1)
        self.assertEqual(result['trace']['embedding_status'], 'unavailable')

    def test_semantic_neighbors_fuse_with_keywords_without_a_fixed_threshold(self):
        both = self.remember('Quarterly revenue report for finance')
        semantic_only = self.remember('Sales grew nine percent last quarter')
        keyword_only = self.remember('Finance team lunch is on Friday')
        unrelated = self.remember('The cat sleeps on the sofa')
        topics = {'revenue': [1.0, 0.0], 'sales': [0.6, 0.8], 'cat': [0.0, 0.0]}
        class Embedder:
            model = 'fixture-v2'
            def embed(self, texts):
                # Modest similarity (0.6) sits below the old 0.65 cutoff.
                return [next((v for word, v in topics.items() if word in t.lower()), [0.0, 1.0])
                        if t != 'finance results' else [1.0, 0.0] for t in texts]
        self.store.rebuild_embeddings(Embedder())
        self.store.embedder = Embedder()
        found = self.call('context', query='finance results', budget=16000)['memories']
        ids = [m['id'] for m in found]
        self.assertEqual(ids[0], both['id'])
        self.assertIn(semantic_only['id'], ids)
        self.assertIn(keyword_only['id'], ids)
        self.assertNotIn(unrelated['id'], ids)

    def test_semantic_ranking_sees_vectors_rewritten_by_another_process(self):
        alpha, beta = self.remember('Alpha note'), self.remember('Beta note')
        def embedder(target):
            class Embedder:
                model = 'fixture-v3'
                def embed(self, texts):
                    return [[1.0, 0.0] if t in ('query words', target) else [0.0, 1.0] for t in texts]
            return Embedder()
        self.store.rebuild_embeddings(embedder('Alpha note'))
        self.store.embedder = embedder('Alpha note')
        top = lambda: [m['id'] for m in self.call('context', query='query words', budget=16000)['memories']]
        self.assertEqual(top(), [alpha['id']])
        Store(self.path).rebuild_embeddings(embedder('Beta note'))
        self.assertEqual(top(), [beta['id']])

    def test_restored_bundle_cannot_mix_private_evidence_into_owner_memory(self):
        e = self.observe(space='project:alpha')
        m = self.remember()
        bundle = self.call('export')
        bundle['supports'].append(dict(memory_id=m['id'], evidence_id=e['id']))
        restored = Store(Path(self.tmp.name) / 'invalid.sqlite')
        with self.assertRaises(MemoryError):
            restored.restore(bundle)

    def test_lexical_ranking_does_not_depend_on_private_corpus(self):
        self.remember('SQLite memory')
        before = self.store.dispatch(self.other, 'search', {'query': 'SQLite'})
        self.call('remember', space='project:alpha', content='SQLite '*100)
        after = self.store.dispatch(self.other, 'search', {'query': 'SQLite'})
        self.assertEqual([m['id'] for m in before['memories']], [m['id'] for m in after['memories']])

    def test_extractor_cannot_remove_negation_or_label_failed_tool_verified(self):
        self.observe(content='I do not prefer PostgreSQL.')
        self.observe(source_event_id='tool-1', speaker='tool', content='Deployment failed: authentication denied.')
        class Extractor:
            def extract(self, e):
                if e['speaker'] == 'user':
                    return [dict(content='Prefer PostgreSQL', quote='prefer PostgreSQL.', basis='direct')]
                return [dict(content='Deployment succeeded', quote=e['content'], basis='observed', outcome='verified')]
        self.store.process_one(Extractor())
        self.store.process_one(Extractor())
        records = self.call('search', query='', budget=8000)['memories']
        self.assertIn('I do not prefer PostgreSQL.', [m['content'] for m in records])
        self.assertNotIn('verified', [m['outcome'] for m in records])

    def test_model_policy_keeps_model_wording_and_labels_inferences(self):
        self.observe(content='We met on Tuesday and, after a long debate about costs, I decided to move the API to Go.')
        self.observe(source_event_id='turn-2', speaker='assistant', content='Go should cut your hosting bill.')
        class Extractor:
            def extract(self, e):
                if e['speaker'] == 'user':
                    return [dict(content='The user moved the API to Go.', quote='I decided to move the API to Go',
                                 basis='direct', kind='decision')]
                return [dict(content='Moving to Go is expected to lower hosting costs.',
                             quote='Go should cut your hosting bill.', basis='direct')]
        self.assertEqual(self.store.process_batch(Extractor()), 2)
        found = {m['content']: m for m in self.call('context', query='Go', budget=8000)['memories']}
        self.assertEqual(found['The user moved the API to Go.']['basis'], 'direct')
        inferred = found['Moving to Go is expected to lower hosting costs.']
        self.assertEqual((inferred['basis'], inferred['status']), ('inferred', 'active'))

    def test_stated_update_replaces_the_older_value_without_widening_forget(self):
        self.observe(content='My deadline is March 1.', occurred_at='2026-01-01T00:00:00Z')
        self.observe(source_event_id='turn-2', content='I moved my deadline to March 15.',
                     occurred_at='2026-02-01T00:00:00Z')
        class Extractor:
            def extract(self, e):
                if 'moved' in e['content']:
                    return [dict(content='The deadline is March 15.', quote='moved my deadline to March 15',
                                 basis='direct', key='project-deadline', update=True)]
                return [dict(content='The deadline is March 1.', quote='My deadline is March 1.',
                             basis='direct', key='project-deadline')]
        self.assertTrue(self.store.process_one(Extractor()))
        self.assertTrue(self.store.process_one(Extractor()))
        [current] = self.call('context', query='deadline')['memories']
        self.assertEqual((current['content'], current['status']), ('The deadline is March 15.', 'active'))
        [earlier] = self.call('context', query='deadline', as_of='2026-01-15T00:00:00Z')['memories']
        self.assertEqual((earlier['status'], earlier['valid_until']), ('superseded', '2026-02-01T00:00:00.000000Z'))
        self.assertIn({'source_id': current['id'], 'target_id': earlier['id'], 'kind': 'replaces'},
                      self.call('inspect', id=current['id'])['relations'])
        preview = self.call('forget_preview', id=current['id'])
        self.assertEqual([m['id'] for m in preview['memories']], [current['id']])

    def test_batch_entries_cite_several_items_and_see_known_keys(self):
        self.remember('The deadline is March 1.', key='project-deadline')
        for index, text in enumerate(['Planning call started.', 'We chose Go.', 'Call ended.']):
            self.observe(source_event_id=f'call-{index}', content=text)
        seen = []
        class Extractor:
            def extract_batch(self, items):
                seen.append(items[0]['known_keys'])
                return [[dict(content='In a planning call the user chose Go.', kind='episode', items=[0, 1, 2, 9])],
                        [], []]
        self.assertEqual(self.store.process_batch(Extractor()), 3)
        self.assertEqual(seen, [['project-deadline']])
        [episode] = self.call('context', query='planning call')['memories']
        self.assertEqual((episode['kind'], episode['basis'], episode['evidence_count']), ('episode', 'inferred', 3))

    def test_same_fact_can_recur_after_expiring(self):
        self.remember('I use SQLite', valid_from='2025-01-01T00:00:00Z', valid_until='2025-02-01T00:00:00Z')
        current = self.remember('I use SQLite', valid_from='2026-01-01T00:00:00Z')
        self.assertEqual([m['id'] for m in self.call('context', query='SQLite')['memories']], [current['id']])

    def test_export_restore_preserves_pending_job_with_partial_explicit_memory(self):
        e = self.observe(content='I prefer Python. I use SQLite.')
        self.remember('I prefer Python', evidence_ids=[e['id']])
        restored = Store(Path(self.tmp.name) / 'restore-pending.sqlite')
        restored.restore(self.call('export'))
        p = restored.authenticate(restored.pair('reader', ['owner']))
        self.assertEqual(restored.dispatch(p, 'status', {})['queue']['pending'], 1)

    def test_candidates_are_inspectable_but_not_automatic_context(self):
        m = self.remember('Maybe prefers Python', basis='inferred')
        browse = self.call('search', query='', include_inactive=True, budget=1600)
        self.assertEqual([x['id'] for x in browse['memories']], [m['id']])
        self.assertEqual(self.call('context', query='Python', include_inactive=True)['memories'], [])

    def test_duplicate_explicit_save_does_not_leave_deleted_content(self):
        first = self.remember('Rare forgotten secret about orchards')
        second = self.remember('Rare forgotten secret about orchards')
        self.assertEqual(first['id'], second['id'])
        self.call('forget', id=first['id'])
        self.assertNotIn('Rare forgotten secret', str(self.call('export')))

    def test_repeats_merge_with_memories_written_without_claim_digests(self):
        first = self.remember('Use SQLite for the memory service.')
        restored = Store(Path(self.tmp.name) / 'restored.sqlite')
        restored.restore(self.call('export'))
        reader = restored.authenticate(restored.pair('reader', ['owner']))
        repeat = restored.dispatch(reader, 'remember', {'space': 'owner', 'content': 'use sqlite for the  memory service'})
        self.assertEqual(repeat['id'], first['id'])
        # Writers that predate the digest index (older versions, raw SQL)
        # are backfilled the next time the store opens.
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('DELETE FROM claims')
            connection.commit()
        reopened = Store(self.path)
        repeat = reopened.dispatch(self.owner, 'remember', {'space': 'owner', 'content': 'USE SQLITE FOR THE MEMORY SERVICE!'})
        self.assertEqual(repeat['id'], first['id'])

    def test_purge_removes_deleted_terms_from_database_pages(self):
        marker = 'zqxneverretainthiswordlpt'
        m = self.remember(marker)
        self.call('forget', id=m['id'])
        self.store.maintenance()
        self.assertFalse(marker.encode() in self.path.read_bytes(), 'Deleted terms remain in database pages')

    def test_redaction_handles_json_quoted_values_and_basic_auth(self):
        e = self.observe(content='{"api_key": "plain-private-token", "password": "correct horse battery staple"}\npassword="a quoted secret"\nAuthorization: Basic bXk6cGFzcw==')
        content = self.call('inspect', id=e['id'])['content']
        for secret in ('plain-private-token', 'correct horse', 'a quoted secret', 'bXk6cGFzcw=='):
            self.assertNotIn(secret, content)

    def test_bad_candidate_does_not_discard_good_extraction(self):
        self.observe()
        class Extractor:
            def extract(self, e):
                return [dict(content=None, basis='inferred'),
                        dict(content='Python', quote='I prefer Python.', basis='direct')]
        self.assertTrue(self.store.process_one(Extractor()))
        self.assertEqual(len(self.call('context', query='Python')['memories']), 1)

    def test_repeated_grounded_fact_is_not_a_conflict_and_all_sources_are_forgotten(self):
        self.observe(occurred_at='2026-01-01T00:00:00Z')
        self.observe(source_event_id='turn-2', occurred_at='2026-01-02T00:00:00Z')
        class Extractor:
            def extract(self, e):
                return [dict(content=e['content'], quote=e['content'], basis='direct', key='preferred-language')]
        self.store.process_one(Extractor())
        self.store.process_one(Extractor())
        found = self.call('context', query='Python', budget=1600)['memories']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['status'], 'active')
        self.assertEqual(len(found[0]['evidence_ids']), 2)
        self.call('forget', id=found[0]['id'])
        self.assertEqual(self.call('export')['evidence'], [])

    def test_correction_does_not_extend_previously_expired_fact(self):
        old = self.remember('Temporary SQLite trial', valid_from='2025-01-01T00:00:00Z', valid_until='2025-02-01T00:00:00Z')
        self.call('correct', id=old['id'], revision=1, content='SQLite production', valid_from='2026-01-01T00:00:00Z')
        self.assertEqual(self.call('context', query='SQLite', as_of='2025-06-01T00:00:00Z')['memories'], [])

    def test_correction_agreeing_with_remaining_fact_resolves_conflict(self):
        self.remember('Use SQLite', key='database')
        wrong = self.remember('Use PostgreSQL', key='database')
        current = self.call('inspect', id=wrong['id'])
        fixed = self.call('correct', id=current['id'], revision=current['revision'], content='Use SQLite')
        self.assertEqual(fixed['status'], 'active')
        self.assertEqual(len(self.call('context', query='SQLite PostgreSQL', budget=3000)['memories']), 1)


if __name__ == '__main__':
    unittest.main()
