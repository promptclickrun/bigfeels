"""Sync cost and recovery behavior, through the real client against an in-process cloud."""
import re
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from bigfeels_mem.cloud_service import CloudService
from bigfeels_mem.store import MemoryError as StoreError
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


class RemoteSetupTests(unittest.TestCase):
    def test_setup_does_not_create_a_database_in_a_cloud_only_directory(self):
        import argparse
        from bigfeels_mem.setup_hermes import run_setup
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            fake = FakeCloud(root)
            self.addCleanup(fake.close)
            directory = root / 'remote'
            connect(directory, fake.url, PASSWORD, allow_local=True, remote=True)
            run_setup(directory, argparse.Namespace(hermes_home=str(root / 'h'), profiles=['default'], apply=False))
            self.assertFalse((directory / 'memory.sqlite').exists())

    def test_recall_gives_up_quickly_but_saves_may_take_longer(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            fake = FakeCloud(root)
            self.addCleanup(fake.close)
            directory = root / 'remote'
            connect(directory, fake.url, PASSWORD, allow_local=True, remote=True)
            client = cloud.RemoteClient(directory, timeout=30)
            seen = []
            real = client.client.request
            client.client.request = lambda path, payload=None, **kw: (seen.append((path, kw.get('timeout'))), real(path, payload, **kw))[1]
            client.call('context', {'query': 'x', 'spaces': ['owner'], 'budget': 100})
            client.call('remember', {'space': 'owner', 'content': 'A save'})
            self.assertEqual([('/v1/context', cloud.RECALL_TIMEOUT_SECONDS), ('/v1/remember', 30)], seen)


class BackupTests(unittest.TestCase):
    def test_backup_of_a_cloud_only_vault_is_a_restorable_store_without_deleted_memories(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            fake = FakeCloud(root)
            self.addCleanup(fake.close)
            seed = Store(root / 'seed' / 'memory.sqlite')
            keep = [seed.dispatch(P, 'remember', {'space': 'owner', 'content': f'Backed up fact {i}'}) for i in range(230)]
            doomed = seed.dispatch(P, 'remember', {'space': 'owner', 'content': 'Deleted before the backup'})
            connect(root / 'seed', fake.url, PASSWORD, allow_local=True)
            sync_once(root / 'seed')
            _, admin = cloud.configured_client(root / 'seed')
            preview = admin.request('/v1/forget_preview', {'id': doomed['id']})
            admin.request('/v1/forget', {'id': doomed['id'], 'plan_token': preview['plan_token']})
            code = admin.request('/v1/devices/invite', {})['code']
            agent = root / 'agent'
            connect(agent, fake.url, code, invite=True, allow_local=True, remote=True)
            result = cloud.backup(agent, root / 'snapshot')
            self.assertEqual(230, result['memories'])
            self.assertGreater(result['pages'], 1, 'a vault larger than one page is paged, not exported at once')
            snapshot = Store(root / 'snapshot' / 'memory.sqlite')
            self.assertEqual(230, snapshot.dispatch(P, 'status', {})['memories'])
            self.assertIn(keep[7]['id'], str(snapshot.dispatch(P, 'search', {'query': 'Backed up fact 7', 'budget': 4000})))
            self.assertEqual(0, len(snapshot.dispatch(P, 'search', {'query': 'Deleted before', 'budget': 4000})['memories']))
            with snapshot.connection() as c:
                self.assertEqual(0, c.execute('SELECT COUNT(*) FROM sync_log').fetchone()[0])
            with self.assertRaises(ValueError):
                cloud.backup(agent, root / 'snapshot')
            self.assertFalse((agent / 'memory.sqlite').exists(), 'a backup must not turn a cloud-only directory into a local one')


    def test_backup_retries_a_transient_failure_instead_of_dying(self):
        calls = {'n': 0}

        class Flaky:
            def request(self, path, payload=None, **kw):
                calls['n'] += 1
                if calls['n'] < 3:
                    raise cloud.ClientError('Cloud unavailable or response invalid', 503)
                return {'ok': True}

        with unittest.mock.patch.object(cloud.time, 'sleep'):
            self.assertEqual({'ok': True}, cloud._pull_with_retry(Flaky(), 0))
        self.assertEqual(3, calls['n'])

        class Denied:
            def request(self, path, payload=None, **kw):
                raise cloud.ClientError('Cloud request failed (HTTP 401)', 401)

        with self.assertRaises(cloud.ClientError):
            cloud._pull_with_retry(Denied(), 0)


def _files_fixture(test, spaces=('owner',)):
    from bigfeels_mem.cloud_files import Files
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    store = Store(Path(directory.name) / 'v.sqlite')
    service = CloudService(store, PASSWORD, list(spaces))
    with store.connection(True) as c:
        token = service.mint(c, 'Obsidian', list(spaces))['token']
    files = Files(store, service)

    def call(method, path, body=b'', depth='1', auth=None):
        headers = {'Authorization': auth or 'Bearer ' + token, 'Depth': depth}
        return files.handle(method, path, headers, body, 'https://x')
    return store, call, token


def _hrefs(xml):
    return re.findall(r'<D:href>([^<]+)</D:href>', xml)


class WikiPagesTests(unittest.TestCase):
    """The shared wiki an agent edits: revisions, history, search, spaces."""

    def test_agents_cannot_overwrite_each_others_edits(self):
        store = Store(Path(tempfile.mkdtemp()) / 'v.sqlite')
        self.addCleanup(lambda: store.path.unlink(missing_ok=True))
        alfie, jax = Principal('alfie', ('owner',)), Principal('jax', ('owner',))
        made = store.dispatch(alfie, 'wiki_write', {'path': 'entities/colt.md', 'content': '# Colt', 'base_revision': 0})
        self.assertEqual(1, made['revision'])
        read = store.dispatch(jax, 'wiki_read', {'path': 'entities/colt.md'})
        store.dispatch(alfie, 'wiki_write', {'path': 'entities/colt.md', 'content': '# Colt\nAlfie edit', 'base_revision': 1})
        with self.assertRaises(StoreError) as raised:
            store.dispatch(jax, 'wiki_write', {'path': 'entities/colt.md', 'content': '# Colt\nJax edit', 'base_revision': read['revision']})
        self.assertEqual(409, raised.exception.status)
        self.assertIn('Alfie edit', store.dispatch(jax, 'wiki_read', {'path': 'entities/colt.md'})['content'])
        self.assertEqual([1], [v['revision'] for v in store.dispatch(jax, 'wiki_history', {'path': 'entities/colt.md'})['versions']])
        self.assertEqual('entities/colt.md', store.dispatch(jax, 'wiki_search', {'query': 'alfie edit'})['pages'][0]['path'])
        with self.assertRaises(StoreError):
            store.dispatch(jax, 'wiki_list', {'space': 'private'})

    def test_secrets_are_redacted_and_paths_are_checked(self):
        store = Store(Path(tempfile.mkdtemp()) / 'v.sqlite')
        self.addCleanup(lambda: store.path.unlink(missing_ok=True))
        alfie = Principal('alfie', ('owner',))
        store.dispatch(alfie, 'wiki_write', {'path': 'n.md', 'content': 'api_key = sk-abcdefghijklmnopqrstuv', 'base_revision': 0})
        self.assertNotIn('sk-abc', store.dispatch(alfie, 'wiki_read', {'path': 'n.md'})['content'])
        for bad in ('../x.md', '', 'a/../../b'):
            with self.assertRaises(StoreError):
                store.dispatch(alfie, 'wiki_write', {'path': bad, 'content': 'x', 'base_revision': 0})


class WikiFilesTests(unittest.TestCase):
    """The wiki over WebDAV, as Remotely Save drives it: /dav/<vault name>/<page path>."""

    def test_vault_root_is_the_wiki_and_memories_are_not_files(self):
        store, call, _ = _files_fixture(self)
        store.dispatch(P, 'remember', {'space': 'owner', 'content': 'An atomic memory', 'key': 'k.one'})
        store.dispatch(P, 'wiki_write', {'path': 'index.md', 'content': '# Index\n- [[colt]]', 'base_revision': 0})
        store.dispatch(P, 'wiki_write', {'path': 'entities/colt.md', 'content': '# Colt', 'base_revision': 0})
        status, xml, _ = call('PROPFIND', '/dav/bigfeels/', depth='infinity')
        self.assertEqual(207, status)
        self.assertEqual({'/dav/bigfeels/', '/dav/bigfeels/entities/', '/dav/bigfeels/entities/colt.md', '/dav/bigfeels/index.md'},
                         set(_hrefs(xml)))
        self.assertEqual({'/dav/bigfeels/', '/dav/bigfeels/entities/', '/dav/bigfeels/index.md'},
                         set(_hrefs(call('PROPFIND', '/dav/bigfeels/', depth='1')[1])))
        self.assertEqual(b'# Colt', call('GET', '/dav/bigfeels/entities/colt.md')[1])

    def test_obsidian_edits_create_and_delete_pages_with_history(self):
        store, call, _ = _files_fixture(self)
        self.assertEqual(201, call('MKCOL', '/dav/bigfeels/concepts/')[0])
        self.assertIn('/dav/bigfeels/concepts/', _hrefs(call('PROPFIND', '/dav/bigfeels/', depth='1')[1]))
        self.assertEqual(201, call('PUT', '/dav/bigfeels/concepts/tea.md', b'Likes tea')[0])
        self.assertEqual(204, call('PUT', '/dav/bigfeels/concepts/tea.md', b'Likes green tea')[0])
        page = store.dispatch(P, 'wiki_read', {'path': 'concepts/tea.md'})
        self.assertEqual(('Likes green tea', 2), (page['content'], page['revision']))
        self.assertTrue(page['updated_by'].startswith('files:Obsidian'))
        self.assertEqual(201, call('PUT', '/dav/bigfeels/assets/pic.png', b'\x89PNG\x00\xff')[0])
        self.assertEqual(b'\x89PNG\x00\xff', call('GET', '/dav/bigfeels/assets/pic.png')[1])
        self.assertEqual(204, call('DELETE', '/dav/bigfeels/concepts/')[0])
        self.assertEqual(404, call('GET', '/dav/bigfeels/concepts/tea.md')[0])
        versions = store.dispatch(P, 'wiki_history', {'path': 'concepts/tea.md'})['versions']
        self.assertEqual([3, 2, 1], [v['revision'] for v in versions])
        self.assertTrue(versions[0]['deleted'])

    def test_remotely_save_connection_check_leaves_nothing_behind(self):
        store, call, _ = _files_fixture(self)
        self.assertEqual(207, call('PROPFIND', '/dav/bigfeels/', depth='0')[0])
        self.assertEqual(201, call('MKCOL', '/dav/bigfeels/rs-test-folder-abc/')[0])
        self.assertEqual(201, call('PUT', '/dav/bigfeels/rs-test-folder-abc/rs-test-file-xyz', bytes(100))[0])
        self.assertEqual(204, call('PUT', '/dav/bigfeels/rs-test-folder-abc/rs-test-file-xyz', bytes(200))[0])
        self.assertEqual(bytes(200), call('GET', '/dav/bigfeels/rs-test-folder-abc/rs-test-file-xyz')[1])
        self.assertEqual(204, call('DELETE', '/dav/bigfeels/rs-test-folder-abc/rs-test-file-xyz')[0])
        self.assertEqual(204, call('DELETE', '/dav/bigfeels/rs-test-folder-abc/')[0])
        self.assertEqual(['/dav/bigfeels/'], _hrefs(call('PROPFIND', '/dav/bigfeels/', depth='infinity')[1]))
        with store.connection() as c:
            self.assertEqual(0, c.execute('SELECT COUNT(*) FROM wiki_history').fetchone()[0])

    def test_files_from_the_old_memory_layout_are_not_pushed_back(self):
        store, call, _ = _files_fixture(self)
        old = b'---\nid: "mem_5e2e9e01e1554f35a37c1e571c4ac99f"\nlineage: "mem_5e2e9e01e1554f35a37c1e571c4ac99f"\n---\nx'
        self.assertEqual(204, call('PUT', '/dav/bigfeels/owner/0752db2/x--5e2e9e01e155.md', old)[0])
        self.assertEqual([], store.dispatch(P, 'wiki_list', {})['pages'])

    def test_login_is_required_and_obsidian_settings_keep_no_history(self):
        store, call, token = _files_fixture(self)
        status, _, headers = call('PROPFIND', '/dav/bigfeels/', auth='Basic bm9wZTpub3Bl')
        self.assertEqual(401, status)
        self.assertIn('Basic', headers['WWW-Authenticate'])
        import base64
        basic = 'Basic ' + base64.b64encode(b'bigfeels:' + token.encode()).decode()
        self.assertEqual(207, call('PROPFIND', '/dav/bigfeels/', auth=basic)[0])
        for _ in range(3):
            call('PUT', '/dav/bigfeels/.obsidian/workspace.json', b'{"n":%d}' % _)
        self.assertEqual([], store.dispatch(P, 'wiki_history', {'path': '.obsidian/workspace.json'})['versions'])
        for path in ('/dav/bigfeels/../x.md',):
            with self.assertRaises(StoreError):
                call('PUT', path, b'text')

    def test_a_login_with_several_spaces_gets_one_folder_per_space(self):
        store, call, _ = _files_fixture(self, spaces=('owner', 'family'))
        Store.dispatch(store, Principal('t', ('family',)), 'wiki_write', {'space': 'family', 'path': 'groceries.md', 'content': 'Milk', 'base_revision': 0})
        hrefs = set(_hrefs(call('PROPFIND', '/dav/bigfeels/', depth='infinity')[1]))
        self.assertTrue({'/dav/bigfeels/owner/', '/dav/bigfeels/family/', '/dav/bigfeels/family/groceries.md'} <= hrefs)
        self.assertEqual(b'Milk', call('GET', '/dav/bigfeels/family/groceries.md')[1])
