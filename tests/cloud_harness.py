"""In-process HTTP server around CloudService, for testing the real sync client without Cloudflare.

The Worker adds rate limiting, CORS and OAuth on top of this; sync correctness lives in CloudService
and the engine, which this exercises through the same CloudClient code a device uses.
"""
import gzip
import json
import shutil
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from bigfeels_mem import sync
from bigfeels_mem.cloud_service import CloudService
from bigfeels_mem.store import MemoryError, Store

PASSWORD = 'p' * 40


class FakeCloud:
    def __init__(self, directory, spaces=('owner',), legacy=False):
        # legacy=True behaves like a Worker deployed before head/vault_id/gzip existed.
        self.legacy = legacy
        self.path = Path(directory) / 'cloud.sqlite'
        self.spaces = list(spaces)
        self.store = Store(self.path)
        self.service = CloudService(self.store, PASSWORD, self.spaces)
        self.requests = []
        self.lock = threading.Lock()
        cloud = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def respond(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                if not cloud.legacy:
                    self.send_header('X-Bigfeels-Gzip', '1')
                if 'gzip' in (self.headers.get('Accept-Encoding') or '') and len(raw) > 1024:
                    raw = gzip.compress(raw)
                    self.send_header('Content-Encoding', 'gzip')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def handle_any(self):
                parts = urlsplit(self.path)
                payload = {}
                if self.command == 'POST':
                    raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                    if (self.headers.get('Content-Encoding') or '').lower() == 'gzip':
                        if cloud.legacy:
                            return self.respond(400, {'error': 'Invalid request'})
                        raw = zlib.decompress(raw, 31)
                    payload = json.loads(raw or b'{}')
                token = (self.headers.get('Authorization') or '').removeprefix('Bearer ')
                changes = len(payload.get('changes', [])) if isinstance(payload, dict) else 0
                with cloud.lock:
                    cloud.requests.append({'path': parts.path, 'method': self.command, 'changes': changes,
                                           'gzip': self.headers.get('Content-Encoding') == 'gzip'})
                    try:
                        with cloud.store.connection(True):
                            pass
                        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
                        result = cloud.service.handle(parts.path, payload, token, query, 'http://' + self.headers['Host'])
                        status, body = (202, {}) if result is None else (200, result)
                        if cloud.legacy and isinstance(body, dict):
                            body = {k: v for k, v in body.items() if k not in ('head', 'vault_id') or parts.path == '/v1/info'}
                            if parts.path == '/v1/info':
                                body.pop('head', None)
                            if parts.path == '/v1/sync/pull':
                                # Old Workers sent every dependency, whatever the cursor.
                                with cloud.store.connection() as c:
                                    body = sync.changes(c, cloud.service.authenticate(token)['spaces'], int(query.get('since', 0)))
                    except MemoryError as error:
                        status, body = error.status, {'error': str(error)}
                self.respond(status, body)

            do_GET = do_POST = handle_any

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def snapshot(self, name):
        """Copy the cloud database, to simulate a later point-in-time restore."""
        target = self.path.with_name(name)
        with self.store.connection() as c:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        shutil.copy2(self.path, target)
        return target

    def restore(self, snapshot):
        for suffix in ('-wal', '-shm'):
            Path(str(self.path) + suffix).unlink(missing_ok=True)
        shutil.copy2(snapshot, self.path)

    def window(self, since=0):
        return self.requests[since:]

    def pushes(self, since=0):
        return [r for r in self.requests[since:] if r['path'] == '/v1/sync/push' and r['changes']]
