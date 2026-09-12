import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bigfeels_mem.local import LocalClient


class RPCTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def child(self, operation, payload, spaces=('owner',)):
        command = [sys.executable, '-m', 'bigfeels_mem.rpc', '--data-dir', str(self.data)]
        for space in spaces:
            command += ['--space', space]
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate()
        self.addCleanup(cleanup)
        child.stdin.write(json.dumps({'id': 'request', 'operation': operation, 'payload': payload}) + '\n')
        child.stdin.flush()
        return child

    def read(self, child):
        line = child.stdout.readline()
        self.assertTrue(line, 'Bridge must emit a protocol response')
        return json.loads(line)

    def test_native_bridge_roundtrip_needs_no_service_or_credential(self):
        child = self.child('remember', {'space': 'owner', 'content': 'Bridge orchid continuity.'})
        saved = self.read(child)
        self.assertEqual(saved['id'], 'request')
        self.assertEqual(saved['result']['content'], 'Bridge orchid continuity.')
        child = self.child('context', {'query': 'orchid'})
        self.assertEqual(self.read(child)['result']['memories'][0]['id'], saved['result']['id'])
        child = self.child('remember', {'space': 'project:denied', 'content': 'Denied'})
        self.assertEqual(self.read(child)['error']['status'], 403)

    def test_bridge_uses_host_completion_and_redacts_before_submission(self):
        local = LocalClient(self.data)
        self.addCleanup(local.close)
        content = 'I prefer jasmine tea. api_key=private-value'
        local.call('observe', dict(space='owner', source='test', source_event_id='1', session_id='1',
                                  content=content, speaker='user', captured=True))
        child = self.child('process', {})
        request = self.read(child)
        self.assertEqual(request['method'], 'extract')
        self.assertEqual(request['messages'][0]['role'], 'system')
        self.assertNotIn('private-value', json.dumps(request))
        answer = json.dumps({'memories':[{'content':'I prefer jasmine tea.', 'quote':'I prefer jasmine tea.',
                                        'kind':'preference','basis':'direct'}]})
        child.stdin.write(json.dumps({'id':request['id'], 'result':'```json\n'+answer+'\n```'})+'\n')
        child.stdin.flush()
        self.assertEqual(self.read(child)['result']['processed'], 1)
        self.assertTrue(local.call('context', {'query':'jasmine'})['memories'])

    def test_malformed_host_reply_keeps_job_pending_without_echoing_secrets(self):
        local = LocalClient(self.data)
        self.addCleanup(local.close)
        local.call('observe', dict(space='owner', source='test', source_event_id='2', session_id='1',
                                  content='I prefer tea.', speaker='user', captured=True))
        child = self.child('process', {})
        request = self.read(child)
        child.stdin.write(json.dumps({'id':request['id'], 'result':'not json secret-value'})+'\n')
        child.stdin.flush()
        result = self.read(child)
        self.assertEqual(result['result']['processed'], 0)
        self.assertNotIn('secret-value', json.dumps(result))
        self.assertEqual(local.call('status', {})['queue']['pending'], 1)
