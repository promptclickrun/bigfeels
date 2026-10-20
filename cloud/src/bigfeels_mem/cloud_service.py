"""Self-hosted cloud API. Storage and transport are injected, engine stays shared."""
import json
import secrets
import time
from . import sync
from .client import ClientError
from .mcp import _handle, TOOLS
from .store import MemoryError, Principal, digest, now, required, string_list, uid


class CloudService:
    def __init__(self, store, password, spaces):
        self.store, self.password, self.spaces = store, password, tuple(spaces)
        with store.connection(True) as c:
            sync.install(c)
            c.execute('CREATE TABLE IF NOT EXISTS cloud_devices (id TEXT PRIMARY KEY, digest TEXT UNIQUE, '
                      'name TEXT, spaces TEXT, admin INTEGER, expires REAL, audience TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS cloud_invites (digest TEXT PRIMARY KEY, spaces TEXT, expires REAL)')
            c.execute('CREATE TABLE IF NOT EXISTS cloud_limits (key TEXT PRIMARY KEY, window INTEGER, count INTEGER)')
            if sync.state(c, 'vault_id') is None:
                sync.set_state(c, 'vault_id', uid('vault'))
            for space in spaces:
                c.execute('INSERT OR IGNORE INTO spaces VALUES (?)', (space,))

    def limit(self, key, maximum=600):
        with self.store.connection(True) as c:
            window = int(time.time()) // 60
            c.execute('DELETE FROM cloud_limits WHERE window<?', (window,))
            c.execute('INSERT INTO cloud_limits VALUES (?,?,1) ON CONFLICT(key) DO UPDATE SET count=count+1',
                      (digest(key), window))
            count = c.execute('SELECT count FROM cloud_limits WHERE key=?', (digest(key),)).fetchone()[0]
            if count > maximum:
                raise MemoryError('Rate limit exceeded; retry in one minute', 429)

    def check_password(self, password):
        if len(self.password) < 32:
            raise MemoryError('Administrator must configure a random ADMIN_PASSWORD of at least 32 characters', 503)
        if not isinstance(password, str) or not secrets.compare_digest(digest(password), digest(self.password)):
            raise MemoryError('Invalid credential', 401)

    def mint(self, c, name, spaces, *, admin=False, expires=None, audience=None, device_id=None):
        required({'name': name}, 'name', 200)
        spaces = string_list(spaces, 'spaces')
        if not spaces or not set(spaces) <= set(self.spaces):
            raise MemoryError('Space is not enabled in this deployment', 403)
        token = secrets.token_urlsafe(32)
        device_id = device_id or uid('device')
        c.execute('INSERT INTO cloud_devices VALUES (?,?,?,?,?,?,?)',
                  (device_id, digest(token), name, json.dumps(spaces), int(admin), expires, audience))
        return {'device_id': device_id, 'token': token, 'spaces': spaces}

    def authenticate(self, token, audience=None):
        if not isinstance(token, str) or not token or len(token) > 500:
            raise MemoryError('Invalid credential', 401)
        with self.store.connection() as c:
            row = c.execute('SELECT * FROM cloud_devices WHERE digest=?', (digest(token),)).fetchone()
        if (not row or (row['expires'] is not None and row['expires'] <= time.time()) or
                (row['audience'] is not None and row['audience'] != audience)):
            raise MemoryError('Invalid credential', 401)
        device = dict(row)
        device['spaces'] = [s for s in json.loads(row['spaces']) if s in self.spaces]
        if not device['spaces']:
            raise MemoryError('No authorized spaces', 403)
        return device

    def handle(self, path, payload, token='', query=None, origin=''):
        query = query or {}
        if path == '/v1/info':
            with self.store.connection() as c:
                return {'protocol': sync.PROTOCOL, 'version': '0.1.0', 'vault_id': sync.state(c, 'vault_id'),
                        'head': sync.journal_seq(c), 'configured': len(self.password) >= 32,
                        'recommended_plan': 'Workers Paid', 'max_batch_bytes': sync.MAX_BYTES,
                        'features': ['sync', 'search', 'remote_mcp', 'oauth_pkce']}
        if path == '/v1/devices/connect':
            self.check_password(payload.get('password'))
            with self.store.connection(True) as c:
                return self.mint(c, payload.get('name', 'device'), payload.get('spaces', ['owner']), admin=True)
        if path == '/v1/devices/join':
            code = required(payload, 'code', 200)
            with self.store.connection(True) as c:
                invite = c.execute('SELECT * FROM cloud_invites WHERE digest=? AND expires>?', (digest(code), time.time())).fetchone()
                if not invite:
                    raise MemoryError('Invalid or expired invitation', 401)
                spaces = payload.get('spaces', json.loads(invite['spaces']))
                if not set(string_list(spaces, 'spaces')) <= set(json.loads(invite['spaces'])):
                    raise MemoryError('Invitation does not authorize these spaces', 403)
                paired = self.mint(c, payload.get('name', 'device'), spaces)
                c.execute('DELETE FROM cloud_invites WHERE digest=?', (digest(code),))
                return paired
        device = self.authenticate(token, origin + '/mcp' if path == '/mcp' else None)
        p = Principal(device['id'], tuple(device['spaces']))
        if path.startswith('/v1/devices/'):
            with self.store.connection(True) as c:
                if path.endswith('/invite'):
                    spaces = string_list(payload.get('spaces', device['spaces']), 'spaces')
                    if not spaces or not set(spaces) <= set(device['spaces']):
                        raise MemoryError('Space is not authorized', 403)
                    code, expires = secrets.token_urlsafe(24), time.time() + 600
                    c.execute('DELETE FROM cloud_invites WHERE expires<?', (time.time(),))
                    c.execute('INSERT INTO cloud_invites VALUES (?,?,?)', (digest(code), json.dumps(spaces), expires))
                    return {'code': code, 'spaces': spaces, 'expires_at': expires}
                if path.endswith('/list'):
                    if not device['admin']:
                        raise MemoryError('Administrator credential required', 403)
                    return {'devices': [dict(r) for r in c.execute('SELECT id,name,spaces,admin,expires FROM cloud_devices')]}
                if path.endswith('/revoke'):
                    target = required(payload, 'device_id', 200)
                    if target != device['id'] and not device['admin']:
                        raise MemoryError('Administrator credential required', 403)
                    c.execute('DELETE FROM cloud_devices WHERE id=?', (target,))
                    return {'status': 'revoked'}
                if path.endswith('/rotate'):
                    new = secrets.token_urlsafe(32)
                    c.execute('UPDATE cloud_devices SET digest=? WHERE id=?', (digest(new), device['id']))
                    return {'token': new}
        if path == '/v1/sync/pull':
            try:
                cursor = int(query.get('since', '0'))
            except (ValueError, TypeError):
                raise MemoryError('Invalid cursor') from None
            with self.store.connection() as c:
                page = sync.changes(c, device['spaces'], cursor, known_through=cursor)
                # `head` lets a device notice a restored cloud: its cursor can never exceed it.
                return {**page, 'head': sync.journal_seq(c), 'vault_id': sync.state(c, 'vault_id')}
        if path == '/v1/sync/push':
            with self.store.connection(True) as c:
                return sync.apply(self.store, c, payload, device['spaces'])
        if path == '/mcp':
            return self.mcp(p, payload)
        operation = path.removeprefix('/v1/')
        if operation == 'observe':
            payload = {**payload, 'queue_extraction': False}
        if operation == 'process':
            raise MemoryError('Cloud extraction is not configured; use memory_remember with source evidence', 400)
        if operation == 'forget' and not payload.get('plan_token'):
            raise MemoryError('Preview deletion and supply plan_token')
        return self.store.dispatch(p, operation, payload)

    def mcp(self, principal, request):
        service = self
        class Adapter:
            # Allows deletion preview in the common MCP handler. Cloud capture
            # is explicit; the cloud does not pretend to run an extractor.
            def process_pending(self, limit=8):
                return 0
            def call(self, operation, payload):
                try:
                    if operation == 'observe':
                        payload = {**payload, 'queue_extraction': False}
                    return service.store.dispatch(principal, operation, payload)
                except MemoryError as e:
                    raise ClientError(str(e), e.status) from None
        if isinstance(request, dict) and request.get('method') == 'tools/list':
            return {'jsonrpc': '2.0', 'id': request.get('id'), 'result': {'tools': [
                ({**t, 'description': 'Store source evidence without extraction; use memory_remember to save sourced claims.'}
                 if t['name'] == 'memory_observe' else t)
                for t in TOOLS if t['name'] != 'memory_process']}}
        if (isinstance(request, dict) and request.get('method') == 'tools/call' and
                request.get('params', {}).get('name') == 'memory_process'):
            raise MemoryError('Cloud extraction is unavailable')
        response, _ = _handle(Adapter(), request, True)
        return response
