"""Real CLI/stdin MCP pilot contracts with disposable approved knowledge."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from bigfeels_mem.local import LocalClient


ROOT = Path(__file__).resolve().parents[1]


class ScoutPilotTests(unittest.TestCase):
    def run_cli(self, directory, *args, input=None):
        return subprocess.run([sys.executable, '-m', 'bigfeels_mem.cli', '--data-dir', str(directory), *args],
            input=input, text=True, capture_output=True, cwd=ROOT, timeout=20)

    def json_cli(self, directory, *args):
        result = self.run_cli(directory, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_explicit_only_mcp_rejects_capture_and_process_and_preserves_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix='Scout pilot with spaces ') as td:
            directory = Path(td) / 'customer A'
            self.json_cli(directory, 'init')
            # A configured provider must not be loaded or used by explicit-only.
            (directory / 'config.json').write_text(json.dumps({'version':1,'provider':{
                'base_url':'https://model.invalid/v1', 'api_key_env':'SCOUT_TEST_NO_SECRET',
                'extraction_model':'fixture', 'embedding_model':'fixture', 'allow_remote':True}}))
            requests = [
                {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'clientInfo':{'name':'scout-compatible-fixture','version':'1'},'capabilities':{}}},
                {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'memory_observe','arguments':{'space':'customer:a','source':'fixture','source_event_id':'no','session_id':'no','speaker':'document','captured':True,'content':'Do not retain raw document'}}},
                {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'memory_process','arguments':{}}},
                {'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'memory_remember','arguments':{'space':'customer:a','key':'atlas:owner','content':'Atlas account owner is Alice'}}},
                {'jsonrpc':'2.0','id':6,'method':'tools/call','params':{'name':'memory_search','arguments':{'query':'Atlas'}}},
                {'jsonrpc':'2.0','id':7,'method':'tools/call','params':{'name':'memory_remember','arguments':{'space':'customer:b','content':'Must not cross customers'}}},
                {'jsonrpc':'2.0','id':8,'method':'tools/call','params':{'name':'memory_status','arguments':{}}},
            ]
            result = self.run_cli(directory, 'mcp', '--explicit-only', '--space', 'customer:a', input=''.join(json.dumps(r)+'\n' for r in requests))
            self.assertEqual(result.returncode, 0, result.stderr)
            replies = {r['id']:r for r in map(json.loads, result.stdout.splitlines())}
            names = {tool['name'] for tool in replies[2]['result']['tools']}
            self.assertNotIn('memory_observe', names)
            self.assertNotIn('memory_process', names)
            for rid in (3,4):
                self.assertTrue('error' in replies[rid] or replies[rid]['result'].get('isError'))
            saved = replies[5]['result']['structuredContent']
            self.assertIn(saved['id'], json.dumps(replies[6]))
            self.assertTrue(replies[7]['result']['isError'])
            status = replies[8]['result']['structuredContent']
            self.assertEqual(status['evidence'], 1)
            self.assertEqual(status['queue']['pending'], 0)
            self.assertEqual(set(status['providers'].values()), {'not_configured'})
            self.assertEqual(self.json_cli(directory, 'inspect', saved['id'], '--space', 'customer:a')['id'], saved['id'])

    def test_real_export_restore_preserves_history_ids_and_rollback(self):
        with tempfile.TemporaryDirectory(prefix='Scout round trip ') as td:
            source, restored = Path(td)/'source', Path(td)/'restored'
            client = LocalClient(source, spaces=['customer:a'], auto_process=False)
            try:
                first = client.call('remember', {'space':'customer:a','key':'atlas:owner','content':'Atlas account owner is Alice','valid_from':'2026-01-01T00:00:00Z'})
                current = client.call('correct', {'id':first['id'],'revision':first['revision'],'content':'Atlas account owner is Bob','valid_from':'2026-02-01T00:00:00Z'})
                removed = client.call('remember', {'space':'customer:a','content':'Disposable unrelated item'})
                preview = client.call('forget_preview', {'id':removed['id']})
                client.call('forget', {'id':removed['id'],'plan_token':preview['plan_token']})
            finally:
                client.close()
            export = Path(td)/'approved-corpus.json'
            self.json_cli(source, 'export', '--space', 'customer:a', '--output', str(export))
            before = json.loads(export.read_text())
            self.json_cli(restored, 'restore', str(export))
            roundtrip = Path(td)/'roundtrip.json'
            self.json_cli(restored, 'export', '--space', 'customer:a', '--output', str(roundtrip))
            after = json.loads(roundtrip.read_text())
            for section in ('spaces','evidence','memories','supports','relations','tombstones','jobs'):
                self.assertEqual(before[section], after[section], section)
            self.assertEqual(self.json_cli(restored, 'inspect', first['id'], '--space', 'customer:a')['status'], 'superseded')
            self.assertEqual(self.json_cli(restored, 'inspect', current['id'], '--space', 'customer:a')['content'], current['content'])
            self.assertNotEqual(self.run_cli(restored, 'restore', str(export)).returncode, 0)
            # Rollback is routing back to the untouched source, never an overwrite.
            self.assertEqual(self.json_cli(source, 'inspect', current['id'], '--space', 'customer:a')['content'], current['content'])
            self.assertEqual(self.json_cli(restored, 'doctor')['database'], 'ok')


if __name__ == '__main__':
    unittest.main()
