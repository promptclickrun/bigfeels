import io
import http.client
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

try:
    from bigfeels_mem.client import ClientError, MemoryClient
    from bigfeels_mem.mcp import serve_stdio
    from bigfeels_mem.server import create_server
    from bigfeels_mem.store import Store
except ImportError:
    ClientError = MemoryClient = serve_stdio = create_server = Store = None


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(create_server, 'The service, client, and MCP interfaces are not implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / 'data'
        self.store = Store(self.data_dir / 'memory.sqlite')
        self.store.create_space('project:alpha')
        self.wide_token = self.store.pair('wide', ['owner', 'project:alpha'])
        self.owner_token = self.store.pair('owner-only', ['owner'])
        self.httpd = create_server(self.store, host='127.0.0.1', port=0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base_url = f'http://127.0.0.1:{self.httpd.server_port}'

    def raw(self, path, *, token=None, payload=None, body=None, headers=None, method=None):
        request_headers = dict(headers or {})
        if token:
            request_headers['Authorization'] = 'Bearer ' + token
        if payload is not None:
            body = json.dumps(payload).encode()
            request_headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=request_headers,
            method=method or ('POST' if body is not None else 'GET'),
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            result = exc.code, dict(exc.headers), exc.read()
            exc.close()
            return result

    def run_ui_scenario(self, scenario):
        script_path = Path(__file__).resolve().parents[1] / 'src' / 'bigfeels_mem' / 'static' / 'app.js'
        harness = r'''
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(id = '') {
    this.id = id; this.hidden = false; this.value = ''; this.textContent = '';
    this.className = ''; this.dataset = {}; this.listeners = {}; this.open = false;
    this.children = [];
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
}

const ids = ['welcome', 'workspace', 'disconnect', 'notice', 'connect-form', 'credential',
  'credential-help', 'status-cards', 'query', 'result-count', 'memories', 'trace',
  'trace-values', 'inspect-dialog', 'inspect-content', 'correct-dialog', 'correct-form',
  'correct-content', 'correct-time', 'search-form', 'refresh'];
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
elements.workspace.hidden = true;
elements.disconnect.hidden = true;
global.document = {
  getElementById: id => elements[id],
  createElement: tag => new Element(tag),
  querySelectorAll: () => []
};
global.window = {confirm: () => true};
const response = value => ({ok: true, json: async () => value});
const memory = content => ({
  id: `mem-${content}`, content, kind: 'fact', status: 'active', basis: 'direct',
  outcome: 'unspecified', revision: 1, space: 'owner', source_available: true,
  valid_from: '2026-01-01T00:00:00Z', reason: ['keyword']
});
const renderedText = node => [node.textContent, ...node.children.map(renderedText)].join(' ');
''' + scenario
        return subprocess.run(
            ['node', '-e', harness, str(script_path)], text=True,
            capture_output=True, timeout=8,
        )

    def test_real_http_roundtrip_enforces_bearer_scope_and_safe_errors(self):
        client = MemoryClient(self.base_url, self.wide_token)
        saved = client.call('remember', {
            'space': 'project:alpha',
            'content': 'Deploy through the staging gate',
            'kind': 'procedure',
        })
        found = client.call('search', {'query': 'staging', 'spaces': ['project:alpha'], 'budget': 1600})
        self.assertEqual([item['id'] for item in found['memories']], [saved['id']])
        preview = client.call('forget_preview', {'id': saved['id']})
        self.assertEqual(preview['memories'][0]['id'], saved['id'])
        self.assertEqual(client.process_pending(1), 0)

        with self.assertRaises(ClientError) as denied:
            MemoryClient(self.base_url, self.owner_token).call('inspect', {'id': saved['id']})
        self.assertEqual(denied.exception.status, 403)
        self.assertNotIn(self.owner_token, str(denied.exception))

        status, _, body = self.raw('/v1/status', payload={})
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)['error']['message'], 'Invalid credential')

        secret = 'private-token\nInjected: value'
        with self.assertRaises(ClientError) as malformed:
            MemoryClient(self.base_url, secret)
        self.assertEqual(malformed.exception.status, 401)
        self.assertNotIn('private-token', str(malformed.exception))

    def test_service_rejects_invalid_json_cross_origin_host_and_large_bodies(self):
        status, _, body = self.raw(
            '/v1/status', token=self.owner_token, body=b'{broken',
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(status, 400)
        self.assertNotIn('{broken', body.decode())

        status, _, _ = self.raw(
            '/v1/status', token=self.owner_token, payload={},
            headers={'Origin': 'https://attacker.example'},
        )
        self.assertEqual(status, 403)

        status, _, _ = self.raw(
            '/v1/status', token=self.owner_token, payload={},
            headers={'Host': 'attacker.example'},
        )
        self.assertEqual(status, 403)

        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.putrequest('POST', '/v1/status')
        connection.putheader('Authorization', 'Bearer ' + self.owner_token)
        connection.putheader('Content-Type', 'application/json')
        connection.putheader('Content-Length', '1000001')
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()

    def test_public_health_and_ui_reveal_no_memory_and_use_safe_browser_rendering(self):
        MemoryClient(self.base_url, self.owner_token).call(
            'remember', {'space': 'owner', 'content': '<img src=x onerror=alert(1)>'},
        )
        status, headers, body = self.raw('/health')
        health = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(set(health), {'status', 'version'})
        self.assertNotIn('img', str(health))

        status, headers, page = self.raw('/')
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertNotIn(b'<img src=x', page)
        status, _, script = self.raw('/app.js')
        source = script.decode()
        self.assertEqual(status, 200)
        self.assertIn('textContent', source)
        self.assertNotIn('innerHTML', source)
        self.assertNotIn('localStorage', source)
        self.assertNotIn('sessionStorage', source)

    def test_ui_disconnect_invalidates_pending_requests_and_clears_private_detail(self):
        script_path = Path(__file__).resolve().parents[1] / 'src' / 'bigfeels_mem' / 'static' / 'app.js'
        harness = r'''
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(id = '') {
    this.id = id; this.hidden = false; this.value = ''; this.textContent = '';
    this.className = ''; this.dataset = {}; this.listeners = {}; this.open = false;
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  append() {}
  replaceChildren() {}
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
}

const ids = ['welcome', 'workspace', 'disconnect', 'notice', 'connect-form', 'credential',
  'credential-help', 'status-cards', 'query', 'result-count', 'memories', 'trace',
  'trace-values', 'inspect-dialog', 'inspect-content', 'correct-dialog', 'correct-form',
  'correct-content', 'correct-time', 'search-form', 'refresh'];
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
elements.workspace.hidden = true;
elements.disconnect.hidden = true;
global.document = {
  getElementById: id => elements[id],
  createElement: tag => new Element(tag),
  querySelectorAll: () => []
};
global.window = {confirm: () => true};

let release;
const gate = new Promise(resolve => { release = resolve; });
global.fetch = async url => {
  await gate;
  return {
    ok: true,
    json: async () => url.endsWith('/status')
      ? {memories: 0, evidence: 0, spaces: ['owner'], queue: {pending: 0}}
      : {memories: [], tokens: 0, trace: {eligible: 0, matched: 0, returned: 0, budget: 32000}}
  };
};

vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {filename: process.argv[1]});
elements.credential.value = 'paired-token';
const pending = elements['connect-form'].listeners.submit({preventDefault() {}});
elements['inspect-dialog'].open = true;
elements['inspect-content'].textContent = 'private detail';
elements.disconnect.listeners.click();
release();
pending.then(() => {
  if (!elements.workspace.hidden || elements.welcome.hidden || !elements.disconnect.hidden) {
    throw new Error('stale connect response reopened the workspace');
  }
  if (elements['inspect-dialog'].open || elements['inspect-content'].textContent !== '') {
    throw new Error('disconnect retained private inspection state');
  }
}).catch(error => { console.error(error.message); process.exitCode = 1; });
'''
        result = subprocess.run(
            ['node', '-e', harness, str(script_path)], text=True,
            capture_output=True, timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ui_refresh_click_starts_a_new_status_and_search_request(self):
        result = self.run_ui_scenario(r'''
let calls = 0;
global.fetch = async url => {
  calls += 1;
  return response(url.endsWith('/status')
    ? {memories: 0, evidence: 0, spaces: ['owner'], queue: {pending: 0}}
    : {memories: [], tokens: 0});
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {filename: process.argv[1]});
(async () => {
  elements.credential.value = 'paired-token';
  await elements['connect-form'].listeners.submit({preventDefault() {}});
  const before = calls;
  elements.refresh.listeners.click({type: 'click'});
  await new Promise(resolve => setImmediate(resolve));
  if (calls !== before + 2) throw new Error(`refresh made ${calls - before} requests instead of 2`);
})().catch(error => { console.error(error.message); process.exitCode = 1; });
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ui_latest_search_wins_when_an_older_response_finishes_last(self):
        result = self.run_ui_scenario(r'''
let releaseA;
const queryAGate = new Promise(resolve => { releaseA = resolve; });
global.fetch = async (url, options) => {
  if (url.endsWith('/status')) {
    return response({memories: 0, evidence: 0, spaces: ['owner'], queue: {pending: 0}});
  }
  const query = JSON.parse(options.body).query;
  if (query === 'query A') await queryAGate;
  const records = query ? [memory(query === 'query A' ? 'older A result' : 'newer B result')] : [];
  return response({memories: records, tokens: 0});
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {filename: process.argv[1]});
(async () => {
  elements.credential.value = 'paired-token';
  await elements['connect-form'].listeners.submit({preventDefault() {}});
  elements.query.value = 'query A';
  elements['search-form'].listeners.submit({preventDefault() {}});
  await Promise.resolve();
  elements.query.value = 'query B';
  elements['search-form'].listeners.submit({preventDefault() {}});
  await new Promise(resolve => setImmediate(resolve));
  if (!renderedText(elements.memories).includes('newer B result')) throw new Error('newer result did not render');
  releaseA();
  await new Promise(resolve => setImmediate(resolve));
  if (renderedText(elements.memories).includes('older A result')) throw new Error('older result replaced newer search');
})().catch(error => { console.error(error.message); process.exitCode = 1; });
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ui_forget_first_click_previews_collateral_and_submits_exact_token(self):
        result = self.run_ui_scenario(r'''
const calls = [];
let confirmation = '';
global.window.confirm = prompt => { confirmation = prompt; return true; };
global.fetch = async (url, options) => {
  const body = JSON.parse(options.body);
  calls.push({url, body});
  if (url.endsWith('/forget_preview')) return response({
    id: body.id,
    plan_token: 'exact-plan-token',
    memories: [memory('selected record'), memory('collateral record')],
    counts: {memories: 2, evidence: 3}
  });
  if (url.endsWith('/forget')) return response({status: 'deleted', memories: 2, evidence: 3});
  if (url.endsWith('/status')) return response({memories: 0, evidence: 0, spaces: ['owner'], queue: {pending: 0}});
  if (url.endsWith('/search')) return response({memories: [], tokens: 0});
  throw new Error(`unexpected URL ${url}`);
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {filename: process.argv[1]});
const card = renderMemory(memory('selected record'));
const forget = card.children[3].children[2];
(async () => {
  await forget.listeners.click();
  if (!calls[0].url.endsWith('/forget_preview')) throw new Error('first click did not preview');
  if (!confirmation.includes('collateral record') || !confirmation.includes('3 evidence')) {
    throw new Error('preview did not show collateral deletion');
  }
  if (!calls[1].url.endsWith('/forget') || calls[1].body.plan_token !== 'exact-plan-token') {
    throw new Error('forget did not preserve the preview token');
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mcp_stdio_initializes_lists_schemas_and_proxies_tool_calls(self):
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-06-18',
                'capabilities': {},
                'clientInfo': {'name': 'fixture', 'version': '1'},
            }},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                'name': 'memory_remember',
                'arguments': {'space': 'owner', 'content': 'Use bounded MCP calls'},
            }},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {
                'name': 'memory_search',
                'arguments': {'query': 'bounded', 'budget': 1600},
            }},
        ]
        stdin = io.StringIO(''.join(json.dumps(item) + '\n' for item in requests))
        stdout = io.StringIO()
        serve_stdio(MemoryClient(self.base_url, self.owner_token), stdin, stdout)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]

        self.assertEqual(responses[0]['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(responses[0]['result']['serverInfo']['name'], 'bigfeels-mem')
        tools = responses[1]['result']['tools']
        self.assertEqual(
            {tool['name'] for tool in tools},
            {f'memory_{name}' for name in (
                'observe', 'remember', 'context', 'search', 'inspect', 'correct',
                'forget_preview', 'forget', 'process', 'status', 'export',
            )},
        )
        remember = next(tool for tool in tools if tool['name'] == 'memory_remember')
        self.assertEqual(remember['inputSchema']['required'], ['space', 'content'])
        context = next(tool for tool in tools if tool['name'] == 'memory_context')
        search = next(tool for tool in tools if tool['name'] == 'memory_search')
        self.assertNotIn('include_inactive', context['inputSchema']['properties'])
        self.assertEqual(search['inputSchema']['properties']['include_inactive']['type'], 'boolean')
        self.assertFalse(responses[2]['result']['isError'])
        self.assertEqual(responses[3]['result']['structuredContent']['memories'][0]['content'], 'Use bounded MCP calls')

    def test_mcp_reports_protocol_and_application_errors_without_leaking_inputs(self):
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-06-18', 'capabilities': {},
                'clientInfo': {'name': 'fixture', 'version': '1'},
            }},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                'name': 'memory_inspect', 'arguments': {'id': 'private-input'},
            }},
        ]
        stdin = io.StringIO(''.join(json.dumps(item) + '\n' for item in requests))
        stdout = io.StringIO()
        serve_stdio(MemoryClient(self.base_url, self.owner_token), stdin, stdout)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(responses[0]['error']['code'], -32002)
        self.assertTrue(responses[2]['result']['isError'])
        self.assertNotIn('private-input', json.dumps(responses[2]))

    def cli(self, data_dir, *args, env=None):
        command = [sys.executable, '-m', 'bigfeels_mem.cli', '--data-dir', str(data_dir), *args]
        merged_env = dict(os.environ)
        source = str(Path(__file__).resolve().parents[1] / 'src')
        merged_env['PYTHONPATH'] = source + os.pathsep + merged_env.get('PYTHONPATH', '')
        if env:
            merged_env.update(env)
        return subprocess.run(command, text=True, capture_output=True, env=merged_env, timeout=8)

    def test_cli_init_space_pair_export_restore_maintenance_and_doctor(self):
        source_dir = Path(self.tmp.name) / 'cli-source'
        restored_dir = Path(self.tmp.name) / 'cli-restored'
        result = self.cli(source_dir, 'init')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((source_dir / 'memory.sqlite').exists())
        self.assertTrue((source_dir / 'config.json').exists())
        self.assertEqual(self.cli(source_dir, 'space', 'project:cli').returncode, 0)

        paired = self.cli(source_dir, 'pair', 'fixture', '--space', 'owner', '--space', 'project:cli')
        self.assertEqual(paired.returncode, 0, paired.stderr)
        token = json.loads(paired.stdout)['token']
        source_store = Store(source_dir / 'memory.sqlite')
        source_store.dispatch(
            source_store.authenticate(token), 'remember',
            {'space': 'owner', 'content': 'Portable export record'},
        )
        export_path = Path(self.tmp.name) / 'bundle.json'
        exported = self.cli(
            source_dir, 'export', '--output', str(export_path), '--token-env', 'TEST_MEMORY_TOKEN',
            env={'TEST_MEMORY_TOKEN': token},
        )
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertNotIn(token, exported.stdout + exported.stderr + export_path.read_text())

        self.assertEqual(self.cli(restored_dir, 'init').returncode, 0)
        restored = self.cli(restored_dir, 'restore', str(export_path))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.cli(restored_dir, 'maintenance').returncode, 0)
        doctor = self.cli(restored_dir, 'doctor')
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        report = json.loads(doctor.stdout)
        self.assertEqual(report['database'], 'ok')
        self.assertNotIn('Portable export record', doctor.stdout)

    def test_cli_export_preserves_parent_mode_refuses_overwrite_and_revoke_is_safe(self):
        source_dir = Path(self.tmp.name) / 'cli-security'
        self.assertEqual(self.cli(source_dir, 'init').returncode, 0)
        paired = self.cli(source_dir, 'pair', 'fixture', '--space', 'owner')
        token = json.loads(paired.stdout)['token']

        export_parent = Path(self.tmp.name) / 'existing-parent'
        export_parent.mkdir(mode=0o755)
        os.chmod(export_parent, 0o755)
        parent_mode = stat.S_IMODE(export_parent.stat().st_mode)
        output = export_parent / 'memory.json'
        environment = {'TEST_MEMORY_TOKEN': token}
        first = self.cli(source_dir, 'export', '--output', str(output), '--token-env', 'TEST_MEMORY_TOKEN', env=environment)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(stat.S_IMODE(export_parent.stat().st_mode), parent_mode)
        if os.name != 'nt':
            self.assertEqual(parent_mode, 0o755)
        original = output.read_bytes()
        second = self.cli(source_dir, 'export', '--output', str(output), '--token-env', 'TEST_MEMORY_TOKEN', env=environment)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(output.read_bytes(), original)

        revoked = self.cli(source_dir, 'revoke', '--token-env', 'TEST_MEMORY_TOKEN', env=environment)
        self.assertEqual(revoked.returncode, 0, revoked.stderr)
        self.assertNotIn(token, revoked.stdout + revoked.stderr)
        with self.assertRaises(Exception) as denied:
            Store(source_dir / 'memory.sqlite').authenticate(token)
        self.assertEqual(denied.exception.status, 401)

    def test_cli_doctor_does_not_change_database_permissions(self):
        data_dir = Path(self.tmp.name) / 'doctor-read-only'
        self.assertEqual(self.cli(data_dir, 'init').returncode, 0)
        database = data_dir / 'memory.sqlite'
        os.chmod(database, 0o640)
        before = database.stat()
        before_content = database.read_bytes()
        result = self.cli(data_dir, 'doctor')
        after = database.stat()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(database.read_bytes(), before_content)
        if os.name == 'nt':
            permission = json.loads(result.stdout)['privacy']['database_permissions']
            self.assertEqual(permission['model'], 'windows_acl')
            self.assertEqual(permission['status'], 'not_verified')
        else:
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)

    def test_cli_init_does_not_change_existing_data_directory_permissions(self):
        data_dir = Path(self.tmp.name) / 'preexisting-data-dir'
        data_dir.mkdir(mode=0o755)
        os.chmod(data_dir, 0o755)
        directory_mode = stat.S_IMODE(data_dir.stat().st_mode)
        result = self.cli(data_dir, 'init')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(data_dir.stat().st_mode), directory_mode)
        if os.name != 'nt':
            self.assertEqual(directory_mode, 0o755)
            self.assertEqual(stat.S_IMODE((data_dir / 'memory.sqlite').stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((data_dir / 'config.json').stat().st_mode), 0o600)

    def test_cli_configure_stores_only_provider_key_environment_name(self):
        data_dir = Path(self.tmp.name) / 'configured'
        self.assertEqual(self.cli(data_dir, 'init').returncode, 0)
        result = self.cli(
            data_dir, 'configure', '--base-url', 'http://127.0.0.1:9999/v1',
            '--api-key-env', 'LOCAL_PROVIDER_KEY', '--extraction-model', 'local-extract',
            '--embedding-model', 'local-embed', '--timeout', '4', '--no-allow-remote',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((data_dir / 'config.json').read_text())
        self.assertEqual(config['provider']['api_key_env'], 'LOCAL_PROVIDER_KEY')
        self.assertNotIn('api_key', config['provider'])
        self.assertNotIn('secret', json.dumps(config).lower())


if __name__ == '__main__':
    unittest.main()
