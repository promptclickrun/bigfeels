"""Sync cost and recovery behavior, through the real client against an in-process cloud."""
import tempfile
import unittest
from pathlib import Path

from bigfeels_mem import cloud, sync
from bigfeels_mem.cloud import BackgroundSync, connect, resync, sync_once
from bigfeels_mem.store import Principal, Store
from cloud_harness import FakeCloud, PASSWORD

P = Principal('test', ('owner',))


class SyncCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cloud = FakeCloud(self.root)
        self.addCleanup(self.cloud.close)

    def device(self, name):
        directory = self.root / name
        return directory, Store(directory / 'memory.sqlite')

    def pair(self, name, invite=False):
        directory, store = self.device(name)
        if invite:
            _, remote = cloud.configured_client(self.root / 'a')
            code = remote.request('/v1/devices/invite', {})['code']
            connect(directory, self.cloud.url, code, invite=True, allow_local=True)
        else:
            connect(directory, self.cloud.url, PASSWORD, allow_local=True)
        return directory, store

    def remember(self, store, text, **kw):
        return store.dispatch(P, 'remember', {'space': 'owner', 'content': text, **kw})

    def test_joining_device_does_not_upload_what_it_just_downloaded(self):
        a_dir, a = self.device('a')
        for i in range(30):
            self.remember(a, f'Shared fact number {i}')
        connect(a_dir, self.cloud.url, PASSWORD, allow_local=True)
        sync_once(a_dir)
        mark = len(self.cloud.requests)
        b_dir, b = self.pair('b', invite=True)
        sync_once(b_dir)
        self.assertEqual(30, b.dispatch(P, 'status', {})['memories'])
        # Only the empty `spaces` marker connect queues may go up; no memory, evidence or edge rows.
        sent = [r for r in self.cloud.pushes(mark) if r['changes'] > 1]
        self.assertEqual([], sent, 'a joining device must not echo the vault back')
        with b.connection() as c:
            self.assertEqual(0, c.execute('SELECT COUNT(*) FROM sync_log').fetchone()[0])

    def test_local_edit_after_pull_still_reaches_the_cloud(self):
        a_dir, a = self.device('a')
        first = self.remember(a, 'Edited elsewhere')
        connect(a_dir, self.cloud.url, PASSWORD, allow_local=True)
        sync_once(a_dir)
        b_dir, b = self.pair('b', invite=True)
        sync_once(b_dir)
        self.remember(b, 'Written on b after joining')
        b.dispatch(P, 'correct', {'id': first['id'], 'content': 'Corrected on b', 'revision': 1})
        sync_once(b_dir)
        sync_once(a_dir)
        self.assertEqual(a.dispatch(P, 'status', {})['memories'], b.dispatch(P, 'status', {})['memories'])
        self.assertIn('Corrected on b', str(a.dispatch(P, 'search', {'query': 'Corrected', 'budget': 4000})))

    def test_idle_cycle_is_one_request(self):
        a_dir, a = self.device('a')
        self.remember(a, 'Something to sync once')
        connect(a_dir, self.cloud.url, PASSWORD, allow_local=True)
        sync_once(a_dir)
        mark = len(self.cloud.requests)
        result = sync_once(a_dir)
        self.assertEqual(0, result['moved'])
        self.assertEqual(['/v1/sync/pull'], [r['path'] for r in self.cloud.window(mark)])

    def test_revision_does_not_resend_known_evidence(self):
        a_dir, a = self.device('a')
        base = self.remember(a, 'A much supported claim')
        for i in range(12):
            a.dispatch(P, 'observe', {'space': 'owner', 'source': 't', 'source_event_id': f'e{i}', 'session_id': 's',
                                      'content': f'Evidence {i}', 'queue_extraction': False})
        with a.connection(True) as c:
            ids = [r[0] for r in c.execute("SELECT id FROM evidence WHERE source='t'")]
            c.executemany('INSERT OR IGNORE INTO supports VALUES (?,?)', [(base['id'], e) for e in ids])
        connect(a_dir, self.cloud.url, PASSWORD, allow_local=True)
        sync_once(a_dir)
        b_dir, b = self.pair('b', invite=True)
        sync_once(b_dir)
        with a.connection(True) as c:
            c.execute('UPDATE memories SET revision=revision+1 WHERE id=?', (base['id'],))
        sync_once(a_dir)
        with b.connection() as c:
            cursor = sync.state(c, 'pulled', 0)
        with self.cloud.store.connection() as c:
            full = sync.changes(c, ['owner'], cursor)
            thin = sync.changes(c, ['owner'], cursor, known_through=cursor)
        # The revised memory is the only row b lacks; its twelve evidence rows are already known.
        self.assertGreater(len(full['changes']), 10)
        self.assertEqual(['memories'], [x['table'] for x in thin['changes']])
        sync_once(b_dir)
        self.assertEqual(2, b.dispatch(P, 'inspect', {'id': base['id']})['revision'])

    def test_restored_cloud_is_detected_and_refilled(self):
        a_dir, a = self.device('a')
        self.remember(a, 'Saved before the backup')
        connect(a_dir, self.cloud.url, PASSWORD, allow_local=True)
        sync_once(a_dir)
        backup = self.cloud.snapshot('backup.sqlite')
        later = self.remember(a, 'Saved after the backup')
        sync_once(a_dir)
        self.cloud.restore(backup)
        # The restored cloud has fewer journal entries than this device has already read.
        self.cloud.service = type(self.cloud.service)(self.cloud.store, PASSWORD, self.cloud.spaces)
        sync_once(a_dir)
        _, remote = cloud.configured_client(a_dir)
        exported = remote.request('/v1/export', {})
        self.assertIn(later['id'], [m['id'] for m in exported['memories']], 'lost rows must be uploaded again')

    def test_deletions_survive_a_restore(self):
        a_dir, a = self.device('a')
        doomed = self.remember(a, 'Delete me for good')
        connect(a_dir, self.cloud.url, PASSWORD, allow_local=True)
        sync_once(a_dir)
        backup = self.cloud.snapshot('backup.sqlite')
        preview = a.dispatch(P, 'forget_preview', {'id': doomed['id']})
        a.dispatch(P, 'forget', {'id': doomed['id'], 'plan_token': preview['plan_token']})
        sync_once(a_dir)
        self.cloud.restore(backup)
        sync_once(a_dir)
        _, remote = cloud.configured_client(a_dir)
        self.assertNotIn(doomed['id'], [m['id'] for m in remote.request('/v1/export', {})['memories']])

    def test_idle_backoff_grows_then_resets(self):
        delays = [BackgroundSync.idle_delay(30, n) for n in range(0, 9)]
        self.assertEqual([30, 30, 30], delays[:3])
        self.assertEqual(sorted(delays), delays)
        self.assertEqual(cloud.IDLE_MAX_SECONDS, delays[-1])
        self.assertEqual(5, BackgroundSync.idle_delay(5, 0))

    def test_large_push_is_compressed(self):
        a_dir, a = self.device('a')
        for i in range(40):
            self.remember(a, f'A reasonably long memory number {i} ' + 'padding ' * 30)
        connect(a_dir, self.cloud.url, PASSWORD, allow_local=True)
        sync_once(a_dir)
        sync_once(a_dir)
        self.assertTrue(any(r['gzip'] for r in self.cloud.pushes()), 'the second request onward should be gzip')
        self.assertEqual(40, self.cloud.store.dispatch(P, 'status', {})['memories'])


if __name__ == '__main__':
    unittest.main()


class RemoteModeTests(unittest.TestCase):
    """Cloud-only: no local copy, every call answered by the vault, nothing to sync or duplicate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cloud = FakeCloud(self.root)
        self.addCleanup(self.cloud.close)

    def test_remote_pairing_keeps_no_local_database_and_never_syncs(self):
        directory = self.root / 'agent'
        result = connect(directory, self.cloud.url, PASSWORD, allow_local=True, remote=True)
        self.assertEqual('remote', result['mode'])
        self.assertFalse((directory / 'memory.sqlite').exists())
        self.assertTrue(cloud.is_remote(directory))
        mark = len(self.cloud.requests)
        BackgroundSync(directory).run()  # returns at once: nothing to sync
        self.assertEqual([], self.cloud.window(mark))

    def test_agents_share_one_vault_without_a_replica(self):
        from bigfeels_mem.local import open_client
        one, two = self.root / 'one', self.root / 'two'
        connect(one, self.cloud.url, PASSWORD, allow_local=True, remote=True)
        _, admin = cloud.configured_client(one)
        code = admin.request('/v1/devices/invite', {})['code']
        connect(two, self.cloud.url, code, invite=True, allow_local=True, remote=True)
        first, second = open_client(one), open_client(two)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertIsInstance(first, cloud.RemoteClient)
        saved = first.call('remember', {'space': 'owner', 'content': 'The shared vault holds this'})
        found = second.call('search', {'query': 'shared vault', 'budget': 4000})
        self.assertIn(saved['id'], str(found))
        self.assertEqual({'processed': 0}, second.call('process', {}))
        self.assertFalse((one / 'memory.sqlite').exists() or (two / 'memory.sqlite').exists())

    def test_remote_client_refuses_unknown_operations_and_closed_use(self):
        from bigfeels_mem.client import ClientError
        directory = self.root / 'agent'
        connect(directory, self.cloud.url, PASSWORD, allow_local=True, remote=True)
        client = cloud.RemoteClient(directory)
        with self.assertRaises(ClientError):
            client.call('drop_everything', {})
        client.close()
        with self.assertRaises(ClientError):
            client.call('status', {})

    def test_local_directory_is_not_mistaken_for_remote(self):
        from bigfeels_mem.local import LocalClient, open_client
        directory = self.root / 'plain'
        client = open_client(directory)
        self.addCleanup(client.close)
        self.assertIsInstance(client, LocalClient)


class SharedStoreTests(unittest.TestCase):
    def test_second_store_paired_to_the_same_vault_is_flagged(self):
        import os
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            fake = FakeCloud(root)
            self.addCleanup(fake.close)
            home = root / 'hermes'
            old = os.environ.get('HERMES_HOME')
            os.environ['HERMES_HOME'] = str(home)
            self.addCleanup(lambda: os.environ.__setitem__('HERMES_HOME', old) if old else os.environ.pop('HERMES_HOME', None))
            first = connect(home / 'profiles' / 'one' / 'bigfeels', fake.url, PASSWORD, allow_local=True)
            self.assertNotIn('warning', first)
            second = connect(home / 'bigfeels', fake.url, PASSWORD, allow_local=True)
            self.assertIn('already paired', second['warning'])


class SetupTests(unittest.TestCase):
    def test_setup_prints_commands_and_creates_one_shared_store(self):
        import argparse
        from bigfeels_mem.setup_hermes import run_setup
        with tempfile.TemporaryDirectory() as t:
            home = Path(t) / 'hermes'
            for name in ('archer', 'bob'):
                (home / 'profiles' / name).mkdir(parents=True)
                (home / 'profiles' / name / 'config.yaml').write_text('model: {}\n')
            data = Path(t) / 'shared'
            result = run_setup(data, argparse.Namespace(hermes_home=str(home), profiles=None, apply=False))
            self.assertTrue((data / 'memory.sqlite').exists())
            self.assertEqual(['default', 'archer', 'bob'], result['profiles'])
            joined = '\n'.join(result['commands'])
            self.assertEqual(3, joined.count(f'memory.bigfeels.path {data}'))
            self.assertIn('-p archer config set memory.provider bigfeels', joined)
            self.assertNotIn('applied', result['status'])


class RollbackHeadTests(unittest.TestCase):
    setUp = SyncCostTests.setUp
    device = SyncCostTests.device
    remember = SyncCostTests.remember
    def test_cloud_that_lost_history_is_noticed_even_with_a_low_cursor(self):
        a_dir, a = self.device('a')
        self.remember(a, 'First')
        connect(a_dir, self.cloud.url, PASSWORD, allow_local=True)
        sync_once(a_dir)
        backup = self.cloud.snapshot('early.sqlite')
        for i in range(5):
            self.remember(a, f'Later {i}')
        sync_once(a_dir)
        self.cloud.restore(backup)
        self.cloud.service = type(self.cloud.service)(self.cloud.store, PASSWORD, self.cloud.spaces)
        sync_once(a_dir)
        _, remote = cloud.configured_client(a_dir)
        names = [m['content'] for m in remote.request('/v1/export', {})['memories']]
        self.assertEqual(6, len(names), 'every memory is back on the cloud after the restore')


class OlderWorkerTests(unittest.TestCase):
    """A new client must keep working against a Worker deployed before this change."""

    def test_sync_against_a_worker_without_head_vault_id_or_gzip(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            old = FakeCloud(root, legacy=True)
            self.addCleanup(old.close)
            a_dir, a = root / 'a', Store(root / 'a' / 'memory.sqlite')
            for i in range(3):
                a.dispatch(P, 'remember', {'space': 'owner', 'content': 'Padding ' * 400 + str(i)})
            connect(a_dir, old.url, PASSWORD, allow_local=True)
            sync_once(a_dir)
            _, remote = cloud.configured_client(a_dir)
            code = remote.request('/v1/devices/invite', {})['code']
            b_dir, b = root / 'b', Store(root / 'b' / 'memory.sqlite')
            connect(b_dir, old.url, code, invite=True, allow_local=True)
            sync_once(b_dir)
            self.assertEqual(3, b.dispatch(P, 'status', {})['memories'])
            self.assertFalse(any(r['gzip'] for r in old.requests), 'never send gzip to a Worker that did not advertise it')
            idle = len(old.requests)
            self.assertEqual(0, sync_once(b_dir)['moved'])
            # Without a vault_id on the pull, the client asks /v1/info once: still a handful of requests.
            self.assertLessEqual(len(old.window(idle)), 2)


class DurableObjectLimitTests(unittest.TestCase):
    """Cloudflare's Durable Object SQLite refuses statements with more than 100 bound parameters."""

    def test_recall_stays_under_the_parameter_limit_with_many_contradictions(self):
        import sqlite3
        from contextlib import contextmanager

        class Limited(Store):
            @contextmanager
            def connection(self, write=False):
                with super().connection(write) as c:
                    c.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 100)
                    yield c

        with tempfile.TemporaryDirectory() as t:
            store = Limited(Path(t) / 'memory.sqlite')
            # Many memories that match one query and that the matcher must expand through edges.
            for i in range(150):
                store.dispatch(P, 'remember', {'space': 'owner', 'content': f'Preference note {i} about preferences',
                                               'key': f'pref.{i % 3}', 'kind': 'preference'})
            # Unrelated memories keep "every eligible memory matched" false, which is what makes the
            # matcher expand along edges in batches (the code that once exceeded 100 parameters).
            for i in range(20):
                store.dispatch(P, 'remember', {'space': 'owner', 'content': f'Unrelated gardening fact {i}'})
            found = store.dispatch(P, 'context', {'query': 'preferences', 'budget': 4000})
            self.assertIn(found['status'], ('ok', 'empty'))
            found = store.dispatch(P, 'search', {'query': 'preferences', 'budget': 4000})
            self.assertTrue(found['memories'])
