import copy
import json
from pathlib import Path
import tempfile
import unittest
from bigfeels_mem import sync
from bigfeels_mem.store import Store, Principal, MemoryError
from bigfeels_mem.cloud_service import CloudService
from bigfeels_mem.cloud_oauth import OAuth
from bigfeels_mem.wiki import export_wiki


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a, self.b = [Store(Path(self.tmp.name) / x / 'memory.sqlite') for x in ('a', 'b')]
        self.p = Principal('test', ('owner',))
        for store in (self.a, self.b):
            with store.connection(True) as c:
                sync.install(c)

    def save(self, store, text='I prefer blue bicycles', **kw):
        return store.dispatch(self.p, 'remember', {'space': 'owner', 'content': text, **kw})

    def page(self, store, since=0, limit=200):
        with store.connection() as c:
            return sync.changes(c, ['owner'], since, limit)

    def apply(self, store, page):
        with store.connection(True) as c:
            return sync.apply(store, c, page, ['owner'])

    def forget(self, store, item):
        preview = store.dispatch(self.p, 'forget_preview', {'id': item})
        return store.dispatch(self.p, 'forget', {'id': item, 'plan_token': preview['plan_token']})

    def test_roundtrip_no_echo_and_keyword_parity(self):
        original = self.save(self.a)
        self.apply(self.b, self.page(self.a))
        self.assertEqual(self.a.dispatch(self.p, 'inspect', {'id': original['id']}), self.b.dispatch(self.p, 'inspect', {'id': original['id']}))
        count = self.page(self.b)['cursor']
        self.apply(self.b, self.page(self.a))
        self.assertEqual(count, self.page(self.b)['cursor'])
        self.assertEqual(self.b.dispatch(self.p, 'status', {})['queue']['pending'], 0)
        a = self.a.dispatch(self.p, 'search', {'query': 'bike', 'budget': 16000, 'as_of': '2099-01-01T00:00:00Z'})
        b = self.b.dispatch(self.p, 'search', {'query': 'bike', 'budget': 16000, 'as_of': '2099-01-01T00:00:00Z'})
        self.assertEqual(a, b)

    def test_offline_delete_cascades_and_late_edges(self):
        original = self.save(self.a)
        self.apply(self.b, self.page(self.a))
        derived = self.save(self.b, 'Another bicycle fact', evidence_ids=original['evidence_ids'])
        stale = self.page(self.b)
        self.forget(self.a, original['id'])
        self.apply(self.b, self.page(self.a))
        self.assertEqual(0, self.b.dispatch(self.p, 'status', {})['memories'])
        self.apply(self.a, stale)
        self.assertEqual(0, self.a.dispatch(self.p, 'status', {})['memories'])
        with self.a.connection() as c:
            self.assertIsNotNone(c.execute('SELECT id FROM tombstones WHERE id=?', (derived['id'],)).fetchone())
            self.assertNotIn('bicycle', json.dumps([dict(r) for r in c.execute('SELECT * FROM sync_log')]))

    def test_late_new_evidence_for_deleted_memory_is_removed(self):
        m = self.save(self.a)
        self.apply(self.b, self.page(self.a))
        event = self.b.dispatch(self.p, 'observe', {'space': 'owner', 'content': 'private new source', 'source': 'test', 'source_event_id': 'x', 'session_id': 'x'})
        with self.b.connection(True) as c:
            c.execute('INSERT INTO supports VALUES (?,?)', (m['id'], event['id']))
        stale = self.page(self.b)
        self.forget(self.a, m['id'])
        self.apply(self.a, stale)
        self.assertEqual(0, self.a.dispatch(self.p, 'status', {})['evidence'])

    def test_correction_beats_high_revision_dispute(self):
        m = self.save(self.a, key='bike')
        self.apply(self.b, self.page(self.a))
        corrected = self.a.dispatch(self.p, 'correct', {'id': m['id'], 'revision': 1, 'content': 'I prefer red bicycles'})
        with self.b.connection(True) as c:
            c.execute("UPDATE memories SET status='disputed',revision=12 WHERE id=?", (m['id'],))
        self.apply(self.a, self.page(self.b))
        self.apply(self.b, self.page(self.a))
        for store in (self.a, self.b):
            old = store.dispatch(self.p, 'inspect', {'id': m['id']})
            self.assertEqual('superseded', old['status'])
            self.assertEqual(12, old['revision'])
            store.dispatch(self.p, 'inspect', {'id': corrected['id']})

    def test_paging_includes_complete_support_and_updated_log(self):
        m = self.save(self.a)
        page = self.page(self.a)
        with self.a.connection(True) as c:
            c.execute("UPDATE memories SET status='disputed' WHERE id=?", (m['id'],))
        update = self.page(self.a, page['cursor'], 1)
        self.assertTrue(any(i['table'] == 'memories' for i in update['changes']))
        self.apply(self.b, update)
        self.assertTrue(self.b.dispatch(self.p, 'inspect', {'id': m['id']})['evidence_ids'])

    def test_unauthorized_rows_roll_back(self):
        self.save(self.a)
        page = self.page(self.a)
        page['changes'].append({'table': 'spaces', 'row': {'name': 'secret'}})
        with self.assertRaises(MemoryError):
            self.apply(self.b, page)
        self.assertEqual(0, self.b.dispatch(self.p, 'status', {})['memories'])

    def test_retention_never_rehydrates_content(self):
        m = self.save(self.a)
        original = self.page(self.a)
        self.apply(self.b, original)
        with self.b.connection(True) as c:
            c.execute('UPDATE evidence SET content=NULL')
        self.apply(self.b, original)
        with self.b.connection() as c:
            self.assertIsNone(c.execute('SELECT content FROM evidence').fetchone()[0])

    def test_service_pairing_scopes_revoke_and_remote_mcp(self):
        s = CloudService(self.a, 'x' * 32, ['owner'])
        with self.assertRaises(MemoryError):
            s.handle('/v1/devices/connect', {'password': 'wrong'})
        token = s.handle('/v1/devices/connect', {'password': 'x' * 32})['token']
        code = s.handle('/v1/devices/invite', {}, token)['code']
        paired = s.handle('/v1/devices/join', {'code': code})
        with self.assertRaises(MemoryError):
            s.handle('/v1/devices/join', {'code': code})
        result = s.handle('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'memory_remember', 'arguments': {'space': 'owner', 'content': 'Remote memory'}}}, paired['token'])
        self.assertFalse(result['result']['isError'])
        s.handle('/v1/devices/revoke', {'device_id': paired['device_id']}, token)
        with self.assertRaises(MemoryError):
            s.handle('/v1/status', {}, paired['token'])

    def test_oauth_bounds_registration_and_authorization(self):
        oauth = OAuth(CloudService(self.a, 'x' * 32, ['owner']), 'https://memory.example')
        for payload in ({'redirect_uris': ['https://client.example/' + 'x' * 2048]},
                        {'redirect_uris': ['https://client.example/callback'], 'extra': 'x' * 16384},
                        {'redirect_uris': ['https://client.example/call\nback']},
                        {'redirect_uris': ['https://client.example/call\u2003back']}):
            with self.assertRaises(MemoryError):
                oauth.handle('/oauth/register', 'POST', payload)
        with self.a.connection() as c:
            self.assertEqual(0, c.execute('SELECT COUNT(*) FROM oauth_clients').fetchone()[0])
        _, client, _ = oauth.handle('/oauth/register', 'POST', {'redirect_uris': ['https://client.example/callback']})
        request = {'client_id': client['client_id'], 'redirect_uri': client['redirect_uris'][0],
                   'resource': oauth.resource, 'response_type': 'code', 'code_challenge_method': 'S256',
                   'code_challenge': 'x' * 43, 'state': 'x' * 2049}
        with self.assertRaises(MemoryError):
            oauth.handle('/oauth/authorize', 'GET', request)
        with self.a.connection() as c:
            self.assertEqual(0, c.execute('SELECT COUNT(*) FROM oauth_grants').fetchone()[0])
        request['state'] = 'valid-state'
        self.assertEqual(200, oauth.handle('/oauth/authorize', 'GET', request)[0])

    def test_pending_edges_are_validated_bounded_and_resolve(self):
        m = self.save(self.a)
        event = self.a.dispatch(self.p, 'observe', {'space': 'owner', 'content': 'Delayed evidence',
            'source': 'test', 'source_event_id': 'delayed', 'session_id': 'session'})
        self.apply(self.b, {'protocol': 1, 'changes': [i for i in self.page(self.a)['changes']
                   if i['table'] != 'evidence' or i['row']['id'] != event['id']]})
        edge = {'table': 'supports', 'row': {'memory_id': m['id'], 'evidence_id': event['id']}}
        self.apply(self.b, {'protocol': 1, 'changes': [edge]})
        invalid = [dict(table='supports', row=dict(memory_id=m['id'], evidence_id='bad')),
                   dict(table='relations', row=dict(source_id=m['id'], target_id='mem_missing', kind='unknown'))]
        for row in invalid:
            with self.assertRaises(MemoryError):
                self.apply(self.b, {'protocol': 1, 'changes': [row]})
        overflow = [{'table': 'supports', 'row': {'memory_id': m['id'], 'evidence_id': f'ev_missing{i}'}}
                    for i in range(sync.MAX_PENDING_PER_SPACE)]
        with self.assertRaises(MemoryError) as error:
            self.apply(self.b, {'protocol': 1, 'changes': overflow})
        self.assertEqual(413, error.exception.status)
        with self.b.connection() as c:
            self.assertEqual(1, c.execute('SELECT COUNT(*) FROM sync_pending').fetchone()[0])
        self.apply(self.b, self.page(self.a))
        with self.b.connection() as c:
            self.assertEqual(0, c.execute('SELECT COUNT(*) FROM sync_pending').fetchone()[0])
            self.assertIsNotNone(c.execute('SELECT 1 FROM supports WHERE memory_id=? AND evidence_id=?',
                                          (m['id'], event['id'])).fetchone())

    def test_same_source_on_two_devices_and_cross_scope_edge(self):
        event = {'space': 'owner', 'content': 'Shared observed source', 'source': 'source',
                 'source_event_id': 'event', 'session_id': 'session'}
        ea = self.a.dispatch(self.p, 'observe', event)
        eb = self.b.dispatch(self.p, 'observe', event)
        self.assertEqual(ea['id'], eb['id'])
        self.apply(self.a, self.page(self.b))
        self.apply(self.b, self.page(self.a))
        with self.a.connection() as c:
            self.assertEqual(1, c.execute('SELECT COUNT(*) FROM evidence').fetchone()[0])
        m = self.save(self.a)
        self.a.create_space('private')
        private = self.a.dispatch(Principal('private', ('private',)), 'remember', {'space': 'private', 'content': 'Secret'})
        with self.assertRaises(MemoryError):
            with self.a.connection(True) as c:
                sync.apply(self.a, c, {'protocol': 1, 'changes': [{'table': 'supports', 'row': {
                    'memory_id': m['id'], 'evidence_id': private['evidence_ids'][0]}}]}, ['owner'])

    def test_local_only_schema_and_rerank_validation(self):
        from bigfeels_mem.cloud import CloudClient
        with self.assertRaises(ValueError):
            CloudClient('https://example.com', 'secret\nheader')
        with self.assertRaises(ValueError):
            CloudClient('http://example.com')
        from bigfeels_mem.cloud_rerank import reorder
        local = Store(Path(self.tmp.name) / 'local.sqlite')
        with local.connection() as c:
            self.assertFalse(sync.enabled(c))
            self.assertEqual('1', c.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0])
        value = {'memories': [{'id': 'a'}, {'id': 'b'}], 'tokens': 12}
        ranked = reorder(value, {'response': [{'id': 1, 'score': 0.9}, {'id': 0, 'score': 0.5}]})
        self.assertEqual(['b', 'a'], [r['id'] for r in ranked['memories']])
        self.assertEqual(12, ranked['tokens'])
        with self.assertRaises(ValueError):
            reorder(value, {'response': [{'id': 2}, {'id': 0}]})

    def test_wiki_stable_and_deletion_cleanup(self):
        m = self.save(self.a)
        vault = Path(self.tmp.name) / 'vault'
        export_wiki(self.a, vault)
        before = {p.name: p.read_bytes() for p in (vault / 'Bigfeels').iterdir()}
        export_wiki(self.a, vault)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (vault / 'Bigfeels').iterdir()})
        self.forget(self.a, m['id'])
        export_wiki(self.a, vault)
        self.assertEqual({'Index.md', '.bigfeels-manifest.json'}, {p.name for p in (vault / 'Bigfeels').iterdir()})
        (vault / 'Bigfeels' / 'Index.md').write_text('My notes')
        with self.assertRaises(ValueError):
            export_wiki(self.a, vault)
        self.assertEqual('My notes', (vault / 'Bigfeels' / 'Index.md').read_text())


if __name__ == '__main__':
    unittest.main()
