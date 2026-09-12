"""Real service + Python/Node adapters; host lifecycle supplied by contract fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

# Installs the test-only Hermes contract when the optional upstream is absent.
import test_adapters  # noqa: F401
from adapters.hermes import AdapterConfig, BigfeelsMemoryProvider
from bigfeels_mem.client import MemoryClient
from bigfeels_mem.server import create_server
from bigfeels_mem.store import Store


ROOT = Path(__file__).resolve().parents[1]
NODE_BRIDGE = '''
import {pathToFileURL} from 'node:url';
const input=JSON.parse(process.env.BIGFEELS_E2E);
const {createOpenClawAdapter}=await import(pathToFileURL(input.adapter));
const adapter=createOpenClawAdapter({config:{url:input.url,token:input.token,
 ownerSpace:'owner',projectSpaces:['project:app'],writeSpace:'project:app',
 primaryAgentId:'main',budget:3200,timeoutMs:2000,autoCapture:true,autoRecall:true},
 fetchImpl:fetch,isSubagentSessionKey:key=>key.includes(':subagent:'),logger:{warn(){}}});
const ctx={runId:input.run,agentId:'main',sessionKey:'agent:main:main'};
const recalled=await adapter.beforePromptBuild({prompt:input.query,messages:[]},ctx);
if(input.capture)await adapter.agentEnd({runId:input.run,success:true,messages:[
 {role:'user',content:input.query},{role:'assistant',content:'I have recorded that plan.'}]},ctx);
console.log(JSON.stringify({text:recalled?.prependContext??''}));
'''


@unittest.skipUnless(shutil.which('node'), 'Node 22+ is required for cross-agent conformance')
class CrossAgentTests(unittest.TestCase):
    def test_hermes_and_openclaw_share_corrections_and_preserve_project_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'memory.sqlite')
            store.create_space('project:app')
            hermes_token = store.pair('hermes', ['owner', 'project:app'])
            openclaw_token = store.pair('openclaw', ['owner', 'project:app'])
            httpd = create_server(store, port=0)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
            url = f'http://127.0.0.1:{httpd.server_port}'
            provider = BigfeelsMemoryProvider(AdapterConfig(base_url=url, token=hermes_token,
                       owner_space='owner', project_spaces=('project:app',),
                       write_space='project:app', budget=3200, timeout=2))
            provider.initialize('hermes-one', platform='cli', agent_context='primary')

            def node(query, run, capture=False):
                env = dict(os.environ, BIGFEELS_E2E=json.dumps(dict(url=url, token=openclaw_token,
                           adapter=str(ROOT / 'adapters/openclaw/adapter.js'), query=query, run=run, capture=capture)))
                result = subprocess.run(['node', '--input-type=module', '-e', NODE_BRIDGE],
                         env=env, capture_output=True, text=True, timeout=15, cwd=ROOT)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)['text']

            class Extractor:
                def extract(self, evidence):
                    return [dict(content=evidence['content'], quote=evidence['content'],
                                 basis='direct' if evidence['speaker'] == 'user' else 'inferred', kind='decision')]

            def drain():
                for _ in range(12):
                    if not store.process_one(Extractor()):
                        break

            provider.on_turn_start(1, 'The project database is PostgreSQL.')
            provider.sync_turn('The project database is PostgreSQL.', 'A database decision was recorded.',
                               session_id='hermes-one')
            drain()
            self.assertIn('PostgreSQL', node('project database', 'read-one'))
            client = MemoryClient(url, openclaw_token)
            memory = client.call('context', {'query': 'project database', 'budget': 3200})['memories'][0]
            corrected = client.call('correct', {'id': memory['id'], 'revision': memory['revision'],
                                    'content': 'The project database is SQLite.'})
            recalled = node('project database', 'read-two')
            self.assertIn('SQLite', recalled)
            self.assertNotIn('PostgreSQL', recalled)

            node('The release needs a changelog.', 'write-one', capture=True)
            drain()
            provider.on_turn_start(2, 'release changelog')
            self.assertIn('The release needs a changelog.', provider.prefetch('release changelog', session_id='hermes-one'))
            private = MemoryClient(url, store.pair('unlinked-agent', ['owner']))
            self.assertEqual(private.call('context', {'query': 'database changelog'})['memories'], [])
            client.call('forget', {'id': corrected['id']})
            self.assertNotIn('SQLite', node('database', 'after-delete'))
            self.assertNotIn('PostgreSQL', node('database', 'after-delete-two'))
            provider.shutdown()


if __name__ == '__main__':
    unittest.main()
