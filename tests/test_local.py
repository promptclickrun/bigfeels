import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from bigfeels_mem.client import ClientError
from bigfeels_mem.store import Store, Principal

try:
    from bigfeels_mem.local import LocalClient
except ImportError:
    LocalClient = None


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(LocalClient, 'Native local memory client is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'memory'

    def client(self, **kwargs):
        client = LocalClient(self.data, **kwargs)
        self.addCleanup(client.close)
        return client

    def event(self, client, space='owner', content='I prefer jasmine tea.'):
        return client.call('observe', dict(space=space, content=content, speaker='user',
            source='native-test', source_event_id=content, session_id='one', captured=True))

    def test_local_save_reopens_without_pairing_or_network(self):
        first = self.client()
        memory = first.call('remember', {'space': 'owner', 'content': 'Local orchid continuity.'})
        second = self.client()
        self.assertEqual(second.call('context', {'query': 'orchid'})['memories'][0]['id'], memory['id'])
        with first.store.connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM credentials').fetchone()[0], 0)
        self.assertEqual(set(p.name for p in self.data.iterdir()) -
                         {'memory.sqlite', 'memory.sqlite-wal', 'memory.sqlite-shm'}, set())

    def test_native_scope_limits_reads_writes_and_model_submission(self):
        seen = []
        class Extractor:
            def extract(self, evidence):
                seen.append(evidence['content'])
                return [dict(content=evidence['content'], quote=evidence['content'], kind='preference', basis='direct')]
        other = self.client(spaces=('project:private',))
        self.event(other, 'project:private', 'Confidential launch orchid.')
        owner = self.client(extractor=Extractor(), auto_process=False)
        self.event(owner)
        self.assertEqual(owner.process_pending(), 1)
        self.assertEqual(seen, ['I prefer jasmine tea.'])
        self.assertTrue(owner.call('context', {'query': 'jasmine'})['memories'])
        self.assertEqual(owner.call('search', {'query': 'orchid'})['memories'], [])
        with self.assertRaises(ClientError) as denied:
            owner.call('remember', {'space': 'project:private', 'content': 'Unauthorized'})
        self.assertEqual(denied.exception.status, 403)
        self.assertEqual(other.call('status', {})['queue']['pending'], 1)

    def test_failed_host_extraction_remains_durable_and_private(self):
        class Broken:
            def extract(self, evidence):
                raise RuntimeError('secret bearer credential')
        client = self.client(extractor=Broken(), auto_process=False)
        self.event(client)
        self.assertEqual(client.process_pending(), 0)
        status = client.call('status', {})
        self.assertEqual(status['queue']['pending'], 1)
        self.assertNotIn('secret bearer', json.dumps(status))
        self.assertEqual(client.call('context', {'query': 'jasmine'})['memories'], [])

    def test_forget_purges_local_index_without_a_service_worker(self):
        client = self.client(auto_process=False)
        marker = 'nativepurgeuniquelysensitivevalue'
        memory = client.call('remember', {'space':'owner', 'content':marker})
        client.call('forget', {'id':memory['id']})
        with client.store.connection() as connection:
            pending = connection.execute("SELECT value FROM metadata WHERE key='needs_purge'").fetchone()
        self.assertEqual(pending[0], '0')
        self.assertNotIn(marker.encode(), (self.data / 'memory.sqlite').read_bytes())

    def test_background_capture_returns_while_model_is_running(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        class Slow:
            def extract(self, evidence):
                entered.set()
                release.wait(2)
                return []
        client = self.client(extractor=Slow())
        evidence = self.event(client)
        self.assertTrue(entered.wait(2))
        self.assertEqual(client.call('inspect', {'id': evidence['id']})['content'], 'I prefer jasmine tea.')
        release.set()
        client.close()
        self.assertFalse(client._thread.is_alive())

    def test_mcp_default_runs_locally_without_any_token_or_server(self):
        env = dict(os.environ)
        env.pop('BIGFEELS_MEM_TOKEN', None)
        messages = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'clientInfo':{'name':'test','version':'1'},'capabilities':{}}},
            {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'memory_remember','arguments':{'space':'owner','content':'MCP orchid continuity.'}}},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'memory_context','arguments':{'query':'orchid'}}},
            {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'memory_remember','arguments':{'space':'project:other','content':'Denied'}}},
        ]
        result = subprocess.run([sys.executable, '-m', 'bigfeels_mem.cli', '--data-dir', str(self.data), 'mcp'],
            input=''.join(json.dumps(m)+'\n' for m in messages), env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[2]['result']['structuredContent']['memories'][0]['content'], 'MCP orchid continuity.')
        self.assertTrue(responses[3]['result']['isError'])

    def test_native_cli_status_doctor_and_export_need_no_pairing(self):
        client = self.client()
        client.call('remember', {'space':'owner', 'content':'Native CLI jasmine continuity.'})
        env = dict(os.environ)
        env.pop('BIGFEELS_MEM_TOKEN', None)
        def run(*args):
            return subprocess.run([sys.executable, '-m', 'bigfeels_mem.cli', '--data-dir', str(self.data), *args],
                                  env=env, capture_output=True, text=True, timeout=10)
        status = run('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)['memories'], 1)
        doctor = run('doctor')
        self.assertEqual(json.loads(doctor.stdout)['status'], 'ok')
        output = self.data / 'backup.json'
        env['BIGFEELS_MEM_TOKEN'] = 'stale-legacy-token-must-not-change-local-export'
        exported = run('export', '--output', str(output))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertEqual(json.loads(output.read_text())['memories'][0]['content'], 'Native CLI jasmine continuity.')
        missing_token = run('export', '--output', str(self.data / 'denied.json'), '--token-env', 'MISSING_NATIVE_TEST_TOKEN')
        self.assertNotEqual(missing_token.returncode, 0)
        self.assertFalse((self.data / 'denied.json').exists())
