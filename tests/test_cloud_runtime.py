"""Opt-in integration test against an isolated local Wrangler vault.

Use scripts/test-cloud.sh. Never point this at a personal/production vault.
"""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from bigfeels_mem.cloud import CloudClient, connect, configured_client, disconnect, sync_once
from bigfeels_mem.store import Store, Principal, MemoryError


@unittest.skipUnless(os.environ.get('BIGFEELS_CLOUD_TEST_URL'), 'isolated Workers runtime not requested')
class RuntimeTests(unittest.TestCase):
    def test_two_devices_offline_delete_and_mcp_oauth(self):
        url = os.environ['BIGFEELS_CLOUD_TEST_URL']
        self.assertTrue(url.startswith('http://127.0.0.1:'), 'Runtime test must use isolated loopback HTTP')
        secret = Path(os.environ['BIGFEELS_CLOUD_TEST_SECRET_FILE']).read_text().strip()
        try:
            with urllib.request.urlopen(url + '/cdn-cgi/local/scheduled?cron=0+*+*+*+*') as scheduled:
                self.assertEqual(200, scheduled.status)
        except urllib.error.HTTPError as error:
            diagnostic = error.read(4000).decode(errors='replace')
            error.close()
            self.fail('Scheduled handler failed: ' + diagnostic)
        principal = Principal('runtime', ('owner',))
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t) / 'a', Path(t) / 'b'
            sa, sb = Store(a / 'memory.sqlite'), Store(b / 'memory.sqlite')
            marker = 'bicycle-' + uuid.uuid4().hex
            original = sa.dispatch(principal, 'remember', {'space': 'owner', 'content': marker, 'key': marker})
            sa.create_space('private')
            sa.dispatch(Principal('runtime', ('private',)), 'remember', {'space': 'private', 'content': 'Must stay local'})
            connect(a, url, secret, allow_local=True)
            sync_once(a)
            ca, remote = configured_client(a)
            invitation = remote.request('/v1/devices/invite', {})['code']
            connect(b, url, invitation, invite=True, allow_local=True)
            sync_once(b)
            self.assertEqual(marker, sb.dispatch(principal, 'inspect', {'id': original['id']})['content'])
            self.assertNotIn('private', remote.request('/v1/export', {})['spaces'])
            self.assertEqual(0, sb.dispatch(principal, 'status', {})['queue']['pending'])
            # A new dependent assertion is known only to the offline device.
            derived = sb.dispatch(principal, 'remember', {'space': 'owner', 'content': 'Dependent offline assertion', 'evidence_ids': original['evidence_ids']})
            preview = remote.request('/v1/forget_preview', {'id': original['id']})
            remote.request('/v1/forget', {'id': original['id'], 'plan_token': preview['plan_token']})
            sync_once(b)
            sync_once(a)
            for store in (sa, sb):
                for mid in (original['id'], derived['id']):
                    with self.assertRaises(MemoryError):
                        store.dispatch(principal, 'inspect', {'id': mid})
            exported = remote.request('/v1/export', {})
            self.assertNotIn(marker, json.dumps(exported))
            # Remote save and local/cloud keyword results share the same engine.
            m = remote.request('/v1/remember', {'space': 'owner', 'content': 'Runtime lavender bicycle', 'key': 'runtime.color'})
            sync_once(a)
            query = {'query': 'lavender bicycle', 'budget': 16000, 'as_of': '2099-01-01T00:00:00Z'}
            self.assertEqual(sa.dispatch(principal, 'search', query), remote.request('/v1/search', query))
            tools = remote.request('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
            self.assertIn('memory_forget_preview', [x['name'] for x in tools['result']['tools']])
            # A failure after an earlier mutation must roll back the whole push.
            exported = remote.request('/v1/export', {})
            clean = next(row for row in exported['memories'] if row['id'] == m['id'])
            bad = {**clean, 'content': 'illegal same-ID rewrite'}
            with self.assertRaises(Exception):
                remote.request('/v1/sync/push', {'protocol': 1, 'changes': [
                    {'table': 'memories', 'row': {**clean, 'status': 'disputed'}},
                    {'table': 'memories', 'row': bad}]})
            self.assertEqual(clean['status'], remote.request('/v1/inspect', {'id': m['id']})['status'])
            # Export/restore drill preserves the same keyword results and fences.
            recovered = Store(Path(t) / 'recovered' / 'memory.sqlite')
            recovered.restore(remote.request('/v1/export', {}))
            self.assertEqual(recovered.dispatch(principal, 'search', query), remote.request('/v1/search', query))
            # Force more than one page and ensure the tail is replicated.
            with sa.connection(True) as c:
                for i in range(205):
                    sa._remember(c, principal, {'space': 'owner', 'content': f'Paging runtime sample {i}'})
            sync_once(a)
            sync_once(b)
            self.assertEqual(sa.dispatch(principal, 'status', {})['memories'], sb.dispatch(principal, 'status', {})['memories'])
            self.worker_extras(url, remote)
            self.oauth_flow(url, secret)
            disconnect(b)
            disconnect(a)

    def worker_extras(self, url, remote):
        # The landing page tells the owner the next step and shows no stored data.
        with urllib.request.urlopen(url + '/') as page:
            html = page.read().decode()
            self.assertEqual(200, page.status)
        self.assertIn('bigfeels-mem cloud connect', html)
        self.assertNotIn('lavender', html)
        # A pull names its vault and the journal head, so devices can notice a restored cloud.
        pulled = remote.request('/v1/sync/pull?since=0')
        self.assertTrue(pulled['vault_id'].startswith('vault_'))
        self.assertGreaterEqual(pulled['head'], pulled['cursor'])
        self.assertTrue(remote.request('/v1/info')['configured'])
        # The journal head must never go backwards, even when forgetting removes the newest entry:
        # a falling head would look like a restored cloud and make every device replay its rows.
        fresh = remote.request('/v1/remember', {'space': 'owner', 'content': 'Head probe ' + uuid.uuid4().hex})
        before = remote.request('/v1/info')['head']
        preview = remote.request('/v1/forget_preview', {'id': fresh['id']})
        remote.request('/v1/forget', {'id': fresh['id'], 'plan_token': preview['plan_token']})
        self.assertGreaterEqual(remote.request('/v1/info')['head'], before)
        # The vault as markdown files over WebDAV, through the real Worker route.
        token = remote.token
        def dav(method, path, body=None, headers=None):
            request = urllib.request.Request(url + path, data=body, method=method,
                                             headers={'Authorization': 'Bearer ' + token, **(headers or {})})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as error:
                text = error.read().decode()
                error.close()
                return error.code, text
        status, listing = dav('PROPFIND', '/dav/owner/', headers={'Depth': 'infinity'})
        self.assertEqual(207, status)
        paths = re.findall(r'<D:href>([^<]+\.md)</D:href>', listing)
        self.assertTrue(paths, listing[:300])
        status, text = dav('GET', paths[0])
        self.assertEqual(200, status)
        probe = 'Edited from a markdown file ' + uuid.uuid4().hex
        status, _ = dav('PUT', paths[0], text.split('\n---\n', 1)[0].encode() + b'\n---\n' + probe.encode())
        self.assertEqual(204, status)
        self.assertIn(probe, dav('GET', paths[0])[1])
        status, _ = dav('PUT', '/dav/owner/notes/new-file.md', b'A file made in Obsidian ' + uuid.uuid4().hex.encode())
        self.assertEqual(201, status)
        # Without a login the route answers 401 and asks for Basic credentials.
        try:
            urllib.request.urlopen(urllib.request.Request(url + '/dav/owner/', method='PROPFIND'))
            self.fail('files must require a login')
        except urllib.error.HTTPError as error:
            self.assertEqual((401, True), (error.code, 'Basic' in (error.headers.get('WWW-Authenticate') or '')))
            error.close()
        # A gzip request body is accepted, and an oversized one is refused before it is parsed.
        body = gzip.compress(json.dumps({'protocol': 1, 'changes': []}).encode())
        request = urllib.request.Request(url + '/v1/sync/push', data=body, method='POST', headers={
            'Authorization': 'Bearer ' + remote.token, 'Content-Type': 'application/json',
            'Content-Encoding': 'gzip', 'User-Agent': 'bigfeels-test'})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(200, response.status)
            self.assertEqual('1', response.headers['X-Bigfeels-Gzip'])
        bomb = gzip.compress(b'{"protocol":1,"changes":[],"pad":"' + b'a' * 3_000_000 + b'"}')
        request = urllib.request.Request(url + '/v1/sync/push', data=bomb, method='POST', headers={
            'Authorization': 'Bearer ' + remote.token, 'Content-Type': 'application/json',
            'Content-Encoding': 'gzip', 'User-Agent': 'bigfeels-test'})
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request)
        self.assertEqual(413, refused.exception.code)
        refused.exception.close()

    def oauth_flow(self, url, secret):
        public = CloudClient(url, allow_local=True)
        registered = public.request('/oauth/register', {'client_name': 'Runtime test', 'redirect_uris': ['http://127.0.0.1/callback']})
        verifier = 'a' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        query = {'client_id': registered['client_id'], 'redirect_uri': 'http://127.0.0.1/callback',
                 'resource': url + '/mcp', 'response_type': 'code', 'code_challenge_method': 'S256',
                 'code_challenge': challenge, 'state': 'test-state'}
        with urllib.request.urlopen(url + '/oauth/authorize?' + urllib.parse.urlencode(query)) as response:
            cookie = response.headers['Set-Cookie'].split(';', 1)[0]
            form = response.read().decode()
        consent = re.search('name="request" value="([^"]+)"', form)[1]
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        opener = urllib.request.build_opener(NoRedirect())
        body = urllib.parse.urlencode({'password': secret, 'request': consent, 'spaces': 'owner'}).encode()
        request = urllib.request.Request(url + '/oauth/authorize', data=body, headers={'Content-Type': 'application/x-www-form-urlencoded', 'Cookie': cookie})
        try:
            opener.open(request)
            self.fail('Expected authorization redirect')
        except urllib.error.HTTPError as redirect:
            self.assertEqual(303, redirect.code)
            values = urllib.parse.parse_qs(urllib.parse.urlsplit(redirect.headers['Location']).query)
            self.assertEqual(['test-state'], values['state'])
            code = values['code'][0]
            redirect.close()
        token_payload = {'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier,
                         'redirect_uri': query['redirect_uri'], 'client_id': query['client_id'], 'resource': query['resource']}
        grant = public.request('/oauth/token', token_payload)
        oauth = CloudClient(url, grant['access_token'], allow_local=True)
        self.assertIn('result', oauth.request('/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}))
        with self.assertRaises(Exception):
            public.request('/oauth/token', token_payload)
        with self.assertRaises(Exception):
            oauth.request('/v1/devices/invite', {})
        refreshed = public.request('/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': grant['refresh_token'],
                                                   'client_id': query['client_id'], 'resource': query['resource']})
        self.assertNotEqual(grant['access_token'], refreshed['access_token'])
        with self.assertRaises(Exception):
            oauth.request('/mcp', {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'})
        # Replaying a consumed refresh disables its entire current grant.
        with self.assertRaises(Exception):
            public.request('/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': grant['refresh_token'],
                                            'client_id': query['client_id'], 'resource': query['resource']})
        with self.assertRaises(Exception):
            CloudClient(url, refreshed['access_token'], allow_local=True).request('/mcp', {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/list'})
