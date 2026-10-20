"""Single-owner OAuth authorization code + S256 PKCE for remote MCP clients."""
import base64
import hashlib
import html
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit
from .store import MemoryError, digest, required, string_list, uid


class OAuth:
    def __init__(self, service, origin):
        self.service, self.origin = service, origin
        self.resource = origin + '/mcp'
        with service.store.connection(True) as c:
            c.execute('CREATE TABLE IF NOT EXISTS oauth_clients (id TEXT PRIMARY KEY, name TEXT, redirects TEXT, expires REAL)')
            c.execute('CREATE TABLE IF NOT EXISTS oauth_grants (digest TEXT PRIMARY KEY, kind TEXT, data TEXT, expires REAL, used INTEGER DEFAULT 0)')

    def put(self, c, kind, data, ttl):
        token = secrets.token_urlsafe(32)
        c.execute('INSERT INTO oauth_grants VALUES (?,?,?,?,0)',
                  (digest(token), kind, json.dumps(data), time.time() + ttl))
        return token

    def get(self, c, token, kind):
        row = c.execute('SELECT * FROM oauth_grants WHERE digest=? AND kind=? AND expires>?',
                        (digest(token), kind, time.time())).fetchone()
        if not row:
            raise MemoryError('invalid_grant', 400)
        return row, json.loads(row['data'])

    def handle(self, path, method, payload, cookie=''):
        if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False).encode()) > 16384:
            raise MemoryError('OAuth request exceeds 16 KB', 413)
        if path in ('/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp'):
            return 200, {'resource': self.resource, 'authorization_servers': [self.origin],
                         'scopes_supported': ['memory'], 'bearer_methods_supported': ['header']}, {}
        if path == '/.well-known/oauth-authorization-server':
            return 200, {'issuer': self.origin, 'authorization_endpoint': self.origin + '/oauth/authorize',
                         'token_endpoint': self.origin + '/oauth/token', 'registration_endpoint': self.origin + '/oauth/register',
                         'response_types_supported': ['code'], 'grant_types_supported': ['authorization_code', 'refresh_token'],
                         'code_challenge_methods_supported': ['S256'], 'token_endpoint_auth_methods_supported': ['none'],
                         'scopes_supported': ['memory']}, {}
        with self.service.store.connection(True) as c:
            c.execute('DELETE FROM oauth_grants WHERE expires<?', (time.time(),))
            c.execute('DELETE FROM oauth_clients WHERE expires<?', (time.time(),))
            if path == '/oauth/register' and method == 'POST':
                redirects = string_list(payload.get('redirect_uris'), 'redirect_uris')
                if not 1 <= len(redirects) <= 10:
                    raise MemoryError('invalid_redirect_uri')
                for redirect in redirects:
                    if len(redirect.encode()) > 2048 or any(ch.isspace() or ord(ch) < 33 or ord(ch) == 127 for ch in redirect):
                        raise MemoryError('invalid_redirect_uri')
                    p = urlsplit(redirect)
                    if (p.username or p.password or p.fragment or not p.hostname or
                            (p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in ('127.0.0.1', 'localhost', '::1')))):
                        raise MemoryError('invalid_redirect_uri')
                if payload.get('token_endpoint_auth_method', 'none') != 'none':
                    raise MemoryError('Only public PKCE clients are supported')
                client_id = uid('client')
                name = required({'name': payload.get('client_name', 'MCP client')}, 'name', 200)
                c.execute('INSERT INTO oauth_clients VALUES (?,?,?,?)',
                          (client_id, name, json.dumps(redirects), time.time() + 365 * 86400))
                return 201, {'client_id': client_id, 'client_name': name, 'redirect_uris': redirects,
                             'token_endpoint_auth_method': 'none', 'grant_types': ['authorization_code', 'refresh_token'],
                             'response_types': ['code']}, {}
            if path == '/oauth/authorize' and method == 'GET':
                state = payload.get('state', '')
                if not isinstance(state, str) or len(state.encode()) > 2048:
                    raise MemoryError('Invalid authorization state')
                client_id = required(payload, 'client_id', 200)
                client = c.execute('SELECT * FROM oauth_clients WHERE id=?', (client_id,)).fetchone()
                if (not client or payload.get('redirect_uri') not in json.loads(client['redirects']) or
                        payload.get('resource') != self.resource or payload.get('response_type') != 'code' or
                        payload.get('code_challenge_method') != 'S256' or
                        not re.fullmatch(r'[A-Za-z0-9_-]{43}', payload.get('code_challenge', '')) or
                        payload.get('scope', 'memory') != 'memory'):
                    raise MemoryError('Invalid authorization request')
                browser = secrets.token_urlsafe(32)
                request = self.put(c, 'consent', {**payload, 'browser': digest(browser), 'name': client['name']}, 600)
                e = html.escape
                spaces = ''.join('<label><input type="checkbox" name="spaces" value="' + e(s, quote=True) +
                                 '" checked> ' + e(s) + '</label><br>' for s in self.service.spaces)
                body = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                        '<title>Connect to Bigfeels</title><main><h1>Connect to your memory</h1><p>Allow <strong>' + e(client['name']) +
                        '</strong> to read, save, correct, export, and delete memory in the selected spaces.</p><p>Callback: ' +
                        e(payload['redirect_uri']) + '</p><form method="post" action="/oauth/authorize">' + spaces +
                        '<input type="hidden" name="request" value="' + request + '"><p><label>Cloud administrator password '
                        '<input type="password" name="password" autocomplete="current-password" required></label></p>'
                        '<button type="submit">Approve connection</button></form><p>Close this page to cancel.</p></main></html>')
                secure = '; Secure' if self.origin.startswith('https:') else ''
                return 200, body, {'Content-Type': 'text/html; charset=utf-8',
                                  'Set-Cookie': 'bigfeels_consent=' + browser + '; HttpOnly; SameSite=Strict; Path=/oauth/authorize; Max-Age=600' + secure}
            if path == '/oauth/authorize' and method == 'POST':
                token = required(payload, 'request', 200)
                row, grant = self.get(c, token, 'consent')
                cookies = dict(part.strip().split('=', 1) for part in cookie.split(';') if '=' in part)
                if row['used'] or not secrets.compare_digest(grant['browser'], digest(cookies.get('bigfeels_consent', ''))):
                    raise MemoryError('Invalid consent session', 403)
                self.service.check_password(payload.get('password'))
                spaces = payload.get('spaces', [])
                if isinstance(spaces, str):
                    spaces = [spaces]
                spaces = string_list(spaces, 'spaces')
                if not spaces or not set(spaces) <= set(self.service.spaces):
                    raise MemoryError('Choose at least one enabled space')
                grant['spaces'] = spaces
                code = self.put(c, 'code', grant, 120)
                c.execute('UPDATE oauth_grants SET used=1 WHERE digest=?', (digest(token),))
                sep = '&' if '?' in grant['redirect_uri'] else '?'
                location = grant['redirect_uri'] + sep + urlencode({'code': code, 'state': grant.get('state', '')})
                return 303, '', {'Location': location, 'Set-Cookie': 'bigfeels_consent=; Max-Age=0; Path=/oauth/authorize; HttpOnly; SameSite=Strict'}
            if path == '/oauth/token' and method == 'POST':
                kind = payload.get('grant_type')
                if payload.get('resource') != self.resource:
                    raise MemoryError('invalid_target')
                if kind == 'authorization_code':
                    supplied = required(payload, 'code', 200)
                    row, grant = self.get(c, supplied, 'code')
                    verifier = required(payload, 'code_verifier', 128)
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
                    if (row['used'] or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier) or
                            grant['client_id'] != payload.get('client_id') or grant['redirect_uri'] != payload.get('redirect_uri') or
                            not secrets.compare_digest(grant['code_challenge'], challenge)):
                        raise MemoryError('invalid_grant')
                    device_id = None
                elif kind == 'refresh_token':
                    supplied = required(payload, 'refresh_token', 200)
                    row, grant = self.get(c, supplied, 'refresh')
                    if grant['client_id'] != payload.get('client_id'):
                        raise MemoryError('invalid_grant')
                    if row['used']:
                        c.execute('DELETE FROM cloud_devices WHERE id=?', (grant['device_id'],))
                        return 400, {'error': 'invalid_grant'}, {}
                    device_id = grant['device_id']
                    if not c.execute('SELECT 1 FROM cloud_devices WHERE id=?', (device_id,)).fetchone():
                        raise MemoryError('invalid_grant')
                    c.execute('DELETE FROM cloud_devices WHERE id=?', (device_id,))
                else:
                    raise MemoryError('unsupported_grant_type')
                paired = self.service.mint(c, grant['name'], grant['spaces'], expires=time.time() + 3600,
                                             audience=self.resource, device_id=device_id)
                refresh = self.put(c, 'refresh', {**grant, 'device_id': paired['device_id']}, 30 * 86400)
                c.execute('UPDATE oauth_grants SET used=1 WHERE digest=?', (digest(supplied),))
                return 200, {'access_token': paired['token'], 'token_type': 'Bearer', 'expires_in': 3600,
                             'refresh_token': refresh, 'scope': 'memory'}, {}
        raise MemoryError('Not found', 404)
