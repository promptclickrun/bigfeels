"""Authenticated loopback HTTP service and background processing runtime."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import socket
import threading
import time
import urllib.parse

from . import __version__
from .store import MemoryError


MAX_REQUEST_BYTES = 1_000_000
STATIC_DIR = Path(__file__).with_name('static')
STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/style.css': ('style.css', 'text/css; charset=utf-8'),
    '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"


def _is_loopback(hostname):
    if hostname == 'localhost':
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except (TypeError, ValueError):
        return False


def _authority(value):
    try:
        parsed = urllib.parse.urlsplit('//' + value)
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return None
        return parsed.hostname, parsed.port
    except ValueError:
        return None


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _IPv6HTTPServer(_HTTPServer):
    address_family = socket.AF_INET6


def create_server(store, host='127.0.0.1', port=8765, processor=None):
    if not isinstance(host, str) or not _is_loopback(host):
        raise ValueError('Service host must be a loopback address')
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Service port must be between 0 and 65535')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            # Requests can include sensitive paths and headers. The local service
            # deliberately emits no per-request logs.
            return

        def _headers(self, status, content_type, length):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', CSP)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
            self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
            self.end_headers()

        def _send(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
            self._headers(status, 'application/json; charset=utf-8', len(body))
            if self.command != 'HEAD':
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _error(self, status, message):
            self._send(status, {'error': {'status': status, 'message': message}})

        def _request_authority(self):
            host_header = self.headers.get('Host')
            if not host_header:
                return None
            authority = _authority(host_header)
            if not authority:
                return None
            hostname, request_port = authority
            server_port = self.server.server_port
            if not _is_loopback(hostname) or (request_port or 80) != server_port:
                return None
            return hostname, server_port

        def _valid_origin(self, authority):
            if self.headers.get('Sec-Fetch-Site', '').lower() == 'cross-site':
                return False
            origin = self.headers.get('Origin')
            if origin is None:
                return True
            try:
                parsed = urllib.parse.urlsplit(origin)
                if (parsed.scheme != 'http' or parsed.username or parsed.password or
                        parsed.path not in ('', '/') or parsed.query or parsed.fragment):
                    return False
                return parsed.hostname == authority[0] and (parsed.port or 80) == authority[1]
            except ValueError:
                return False

        def _guard_host(self):
            authority = self._request_authority()
            if authority is None:
                self._error(403, 'Invalid request host')
                return None
            return authority

        def do_GET(self):
            self._get(False)

        def do_HEAD(self):
            self._get(True)

        def _get(self, head):
            if self._guard_host() is None:
                return
            path = urllib.parse.urlsplit(self.path).path
            if path == '/health':
                self._send(200, {'status': 'ok', 'version': __version__})
                return
            static = STATIC_FILES.get(path)
            if not static:
                self._error(404, 'Not found')
                return
            filename, content_type = static
            try:
                body = (STATIC_DIR / filename).read_bytes()
            except OSError:
                self._error(404, 'Not found')
                return
            self._headers(200, content_type, len(body))
            if not head:
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_POST(self):
            # A rejected request may leave unread body bytes. Closing every POST
            # keeps those bytes from being parsed as a second request.
            self.close_connection = True
            authority = self._guard_host()
            if authority is None:
                return
            if not self._valid_origin(authority):
                self._error(403, 'Cross-origin requests are not allowed')
                return
            path = urllib.parse.urlsplit(self.path).path
            pieces = path.split('/')
            if len(pieces) != 3 or pieces[:2] != ['', 'v1'] or not pieces[2]:
                self._error(404, 'Unknown operation')
                return
            if self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                self._error(400, 'Transfer encoding is not supported')
                return
            if len(self.headers.get_all('Content-Length', [])) != 1:
                self._error(400, 'Exactly one Content-Length is required')
                return
            try:
                length = int(self.headers.get('Content-Length', ''))
            except ValueError:
                self._error(400, 'Content-Length is required')
                return
            if length < 0:
                self._error(400, 'Invalid Content-Length')
                return
            if length > MAX_REQUEST_BYTES:
                self.close_connection = True
                self._error(413, 'Request body exceeds limit')
                return
            if self.headers.get_content_type() != 'application/json':
                self._error(400, 'Content-Type must be application/json')
                return
            authorization = self.headers.get('Authorization', '')
            if not authorization.startswith('Bearer ') or authorization.count(' ') != 1:
                self._error(401, 'Invalid credential')
                return
            token = authorization[7:]
            try:
                principal = store.authenticate(token)
            except MemoryError as exc:
                self._error(exc.status, str(exc))
                return
            try:
                raw = self.rfile.read(length)
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, 'Request body must be valid JSON')
                return
            except (OSError, TimeoutError):
                self._error(400, 'Request body could not be read')
                return
            if not isinstance(payload, dict):
                self._error(400, 'Request must be an object')
                return
            try:
                if pieces[2] == 'process':
                    if set(payload) - {'limit'}:
                        raise MemoryError('Unknown process option')
                    limit = payload.get('limit', 8)
                    if type(limit) is not int or not 1 <= limit <= 8:
                        raise MemoryError('Processing limit must be between 1 and 8')
                    processed = processor(limit, principal.spaces) if processor else 0
                    result = {'processed': processed}
                    result['status'] = store.dispatch(principal, 'status', {})
                else:
                    result = store.dispatch(principal, pieces[2], payload)
                    if pieces[2] == 'forget_preview':
                        target = payload.get('id')
                        result.get('memories', []).sort(
                            key=lambda item: item.get('id') != target,
                        )
                self._send(200, result)
            except MemoryError as exc:
                self._error(exc.status, str(exc))
            except Exception:
                self._error(500, 'Internal service error')

    server_class = _IPv6HTTPServer if ':' in host else _HTTPServer
    return server_class((host, port), Handler)


class BackgroundProcessor:
    def __init__(self, store, provider=None, interval=1.0, maintenance_interval=60.0):
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
            raise ValueError('Worker interval must be positive')
        self.store = store
        self.provider = provider
        self.interval = interval
        self.maintenance_interval = maintenance_interval
        self._stop = threading.Event()
        self._processing = threading.Lock()
        self._thread = threading.Thread(target=self._run, name='bigfeels-memory-worker', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        # Provider calls are already bounded by their configured timeout. Do not
        # return from the explicit service lifecycle while processing is alive.
        self._thread.join()

    def process_pending(self, limit=8, spaces=None):
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError('Processing limit must be between 1 and 8')
        if not self.provider or self._stop.is_set():
            return 0
        if not self._processing.acquire(blocking=False):
            return 0
        processed = 0
        try:
            if self.provider.can_extract:
                while processed < limit and not self._stop.is_set():
                    if not self.store.process_one(
                            self.provider,
                            self.provider if self.provider.can_embed else None,
                            spaces=spaces):
                        break
                    processed += 1
            if self.provider.can_embed:
                self.store.process_embeddings(self.provider)
        finally:
            self._processing.release()
        return processed

    def _run(self):
        maintenance_at = 0.0
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                if now >= maintenance_at:
                    self.store.maintenance()
                    maintenance_at = now + self.maintenance_interval
                self.process_pending(1)
            except Exception:
                # Provider/store details can contain private material. Status is
                # visible through the authenticated API; the worker stays alive.
                pass
            self._stop.wait(self.interval)


def serve(store, host='127.0.0.1', port=8765, provider=None, worker_interval=1.0, started=None):
    worker = BackgroundProcessor(store, provider, interval=worker_interval)
    httpd = create_server(store, host=host, port=port, processor=worker.process_pending)
    worker.start()
    try:
        if started:
            started(httpd.server_port)
        httpd.serve_forever()
    finally:
        worker.stop()
        httpd.server_close()
