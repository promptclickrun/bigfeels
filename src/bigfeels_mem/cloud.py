"""Explicit HTTPS cloud transport and a single sync owner per local database."""
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .client import ClientError, _NoRedirect, _is_loopback
from . import sync
from .store import Store, now

CONFIG_NAME = 'cloud.json'
PLAN_RECOMMENDATION = ('We recommend Cloudflare Workers Paid for larger sync batches and more CPU headroom. '
                       'Cloudflare bills your account directly; local-only use remains free of cloud costs.')


class CloudClient:
    def __init__(self, url, token='', *, allow_local=False):
        if not isinstance(url, str):
            raise ValueError('Cloud URL must be a string')
        if (not isinstance(token, str) or len(token) > 500 or not token.isascii() or
                any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in token)):
            raise ValueError('Invalid cloud credential')
        p = urllib.parse.urlsplit(url)
        local = allow_local and p.scheme == 'http' and _is_loopback(p.hostname or '')
        if (not p.hostname or (p.scheme != 'https' and not local) or p.username or p.password or
                p.path not in ('', '/') or p.query or p.fragment):
            raise ValueError('Cloud URL must be an HTTPS origin (loopback HTTP needs --allow-local)')
        self.url, self.token = url.rstrip('/'), token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if data and len(data) > sync.MAX_BYTES:
            raise ClientError('Sync request exceeds 2 MB', 413)
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.url + path, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(16_000_001)
            if len(raw) > 16_000_000:
                raise ClientError('Cloud response exceeds limit', 502)
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            exc.close()
            # Do not echo arbitrary remote bodies (they can contain credentials).
            raise ClientError(f'Cloud request failed (HTTP {exc.code})', exc.code) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            raise ClientError('Cloud unavailable or response invalid', 503) from None

    def info(self):
        info = self.request('/v1/info')
        if not isinstance(info, dict) or info.get('protocol') != sync.PROTOCOL:
            raise ClientError('Incompatible cloud protocol; update the CLI and Worker together', 409)
        return info


def configuration(data_dir):
    path = Path(data_dir) / CONFIG_NAME
    if not path.exists():
        return None
    if path.stat().st_size > 100_000:
        raise ValueError('Cloud config exceeds limit')
    config = json.loads(path.read_text())
    if (not isinstance(config, dict) or config.get('protocol') != sync.PROTOCOL or not isinstance(config.get('spaces'), list)
            or not config['spaces'] or any(not isinstance(s, str) or not s for s in config['spaces'])
            or not isinstance(config.get('token'), str) or not config['token']
            or not isinstance(config.get('device_id'), str) or not isinstance(config.get('vault_id'), str)):
        raise ValueError('Invalid cloud configuration')
    interval = config.get('interval', 30)
    if type(interval) not in (float, int) or not math.isfinite(interval) or interval < 1:
        raise ValueError('Cloud interval must be at least one second')
    return config


def status(store, spaces=None):
    with store.connection() as c:
        if not sync.enabled(c):
            return {'enabled': False}
        allowed = spaces or sync.state(c, 'spaces', [])
        pending = c.execute('SELECT COUNT(*) FROM sync_log WHERE seq>? AND space IN (' +
                            ','.join('?' for _ in allowed) + ')',
                            (sync.state(c, 'pushed', 0), *allowed)).fetchone()[0]
        return {'enabled': sync.state(c, 'connected', False), 'pending_rows': pending,
                'last_sync': sync.state(c, 'last_sync'), 'last_error': sync.state(c, 'last_error'),
                'recommended_plan': 'Workers Paid'}


def connect(data_dir, url, secret, *, spaces=('owner',), name='device', invite=False, allow_local=False, interval=30):
    from .cli import _private_json
    if configuration(data_dir):
        raise ValueError('Already connected; disconnect before changing cloud accounts or spaces')
    client = CloudClient(url, allow_local=allow_local)
    info = client.info()
    paired = client.request('/v1/devices/join' if invite else '/v1/devices/connect',
                            {'code' if invite else 'password': secret, 'name': name, 'spaces': list(spaces)})
    config = {'protocol': sync.PROTOCOL, 'url': client.url, 'token': paired['token'],
              'device_id': paired['device_id'], 'spaces': paired['spaces'],
              'allow_local': allow_local, 'interval': interval, 'vault_id': info['vault_id']}
    store = Store(Path(data_dir) / 'memory.sqlite')
    with store.connection(True) as c:
        sync.install(c)
        for space in config['spaces']:
            c.execute('INSERT OR IGNORE INTO spaces VALUES (?)', (space,))
        for key, value in {'pushed': 0, 'pulled': 0, 'connected': True, 'spaces': config['spaces'],
                           'last_error': None, 'last_sync': None, 'device_id': config['device_id'], 'lease': {}}.items():
            sync.set_state(c, key, value)
        sync.bootstrap(c, config['spaces'])
    _private_json(Path(data_dir) / CONFIG_NAME, config, replace=False)
    return {'status': 'connected', 'spaces': config['spaces'], 'recommendation': PLAN_RECOMMENDATION}


def configured_client(data_dir):
    config = configuration(data_dir)
    if config is None:
        raise ValueError('Not connected; run cloud connect first')
    return config, CloudClient(config['url'], config['token'], allow_local=config.get('allow_local', False))


def sync_once(data_dir, *, stop=None):
    config, client = configured_client(data_dir)
    store = Store(Path(data_dir) / 'memory.sqlite')
    owner = secrets.token_hex(16)

    def lease(c, acquire=False):
        if not sync.state(c, 'connected', False) or sync.state(c, 'device_id') != config['device_id']:
            raise ClientError('Cloud connection changed; retry with the current configuration', 409)
        current = sync.state(c, 'lease', {})
        if acquire:
            if current.get('until', 0) > time.time():
                return False
        elif current.get('owner') != owner or current.get('until', 0) <= time.time():
            raise ClientError('Sync lease expired; retry', 409)
        sync.set_state(c, 'lease', {'owner': owner, 'until': time.time() + 60})
        return True

    with store.connection(True) as c:
        if not lease(c, True):
            return {'status': 'busy'}
    try:
        info = client.info()
        if info['vault_id'] != config['vault_id']:
            raise ClientError('Cloud database changed; disconnect and reconnect to bootstrap safely', 409)
        # Pull first so an offline client learns deletions before uploading old
        # data. Both directions use the exact same merge function.
        for direction in ('pull', 'push', 'pull'):
            for _ in range(1000):
                if stop and stop.is_set():
                    return {'status': 'stopped'}
                with store.connection(True) as c:
                    lease(c)
                    cursor = sync.state(c, 'pulled' if direction == 'pull' else 'pushed', 0)
                    page = sync.changes(c, config['spaces'], cursor) if direction == 'push' else None
                if direction == 'pull':
                    page = client.request('/v1/sync/pull?since=' + str(cursor))
                    with store.connection(True) as c:
                        lease(c)
                        sync.apply(store, c, page, config['spaces'])
                        sync.set_state(c, 'pulled', page['cursor'])
                else:
                    if page['changes']:
                        client.request('/v1/sync/push', page)
                    with store.connection(True) as c:
                        lease(c)
                        sync.set_state(c, 'pushed', page['cursor'])
                        # Only discard acknowledged references, never a newer
                        # concurrent edit of the same row.
                        c.execute('DELETE FROM sync_log WHERE seq<=?', (page['cursor'],))
                if not page['has_more']:
                    break
            else:
                raise ClientError('Sync cycle limit reached; run sync again', 429)
        with store.connection(True) as c:
            lease(c)
            sync.set_state(c, 'last_sync', now())
            sync.set_state(c, 'last_error', None)
        return {'status': 'synced', **status(store)}
    except Exception as error:
        with store.connection(True) as c:
            sync.set_state(c, 'last_error', 'http_' + str(getattr(error, 'status', 503)))
        raise
    finally:
        with store.connection(True) as c:
            if sync.state(c, 'lease', {}).get('owner') == owner:
                sync.set_state(c, 'lease', {})


def disconnect(data_dir, *, offline=False):
    config, client = configured_client(data_dir)
    if not offline:
        client.request('/v1/devices/revoke', {'device_id': config['device_id']})
    store = Store(Path(data_dir) / 'memory.sqlite')
    with store.connection(True) as c:
        sync.set_state(c, 'connected', False)
        for table in sync.TABLES:
            for event in ('insert', 'update', 'delete'):
                c.execute(f'DROP TRIGGER IF EXISTS sync_{table}_{event}')
        c.execute('DELETE FROM sync_log')
    (Path(data_dir) / CONFIG_NAME).unlink()
    return {'status': 'disconnected', 'revoked': not offline, 'local_data_preserved': True}


class BackgroundSync:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='bigfeels-cloud-sync', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def run(self):
        if self.stop_event.wait(0.25):
            return
        delay = 1
        while not self.stop_event.is_set():
            try:
                config = configuration(self.data_dir)
                if not config:
                    return
                sync_once(self.data_dir, stop=self.stop_event)
                delay = config.get('interval', 30)
            except Exception:
                delay = min(max(delay * 2, 5), 300)
            self.wake_event.wait(delay)
            self.wake_event.clear()

    def wake(self):
        self.wake_event.set()

    def close(self):
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=0.1)
