"""Explicit HTTPS cloud transport and a single sync owner per local database."""
import gzip
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
import zlib

from .client import ClientError, _NoRedirect, _is_loopback
from . import __version__, sync
from .store import Store, now

CONFIG_NAME = 'cloud.json'
PLAN_RECOMMENDATION = ('Workers Free can hold a small memory. Workers Paid ($5/month) is recommended for a large first upload '
                       'and more CPU headroom. Cloudflare bills your account directly; local-only use stays free.')
IDLE_MAX_SECONDS = 300
RECALL_TIMEOUT_SECONDS = 4


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
        self.gzip_ok = False
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def request(self, path, payload=None, *, timeout=60):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if data and len(data) > sync.MAX_BYTES:
            raise ClientError('Sync request exceeds 2 MB', 413)
        # Cloudflare's edge rejects urllib's default User-Agent on workers.dev (error 1010).
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                   'Accept-Encoding': 'gzip', 'User-Agent': 'bigfeels-mem/' + __version__}
        # Older Workers cannot read compressed requests; they say so on every reply once they can.
        if data and len(data) > 4096 and self.gzip_ok:
            data = gzip.compress(data, 3)
            headers['Content-Encoding'] = 'gzip'
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.url + path, data=data, headers=headers)
        try:
            # Full 200-reference sync pages take about 12 s on a cold Worker.
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(16_000_001)
                self.gzip_ok = self.gzip_ok or response.headers.get('X-Bigfeels-Gzip') == '1'
                if response.headers.get('Content-Encoding') == 'gzip':
                    raw = zlib.decompressobj(31).decompress(raw, 16_000_001)
            if len(raw) > 16_000_000:
                raise ClientError('Cloud response exceeds limit', 502)
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            exc.close()
            # Do not echo arbitrary remote bodies (they can contain credentials).
            raise ClientError(f'Cloud request failed (HTTP {exc.code})', exc.code) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, zlib.error, OSError):
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


def other_pairing(data_dir, vault_id):
    """Another data directory on this computer paired to the same vault, if one is easy to find."""
    from .paths import default_data_dir
    here = Path(data_dir).expanduser().resolve()
    candidates = [default_data_dir()]
    home = os.environ.get('HERMES_HOME')
    if home:
        candidates.append(Path(home) / 'bigfeels')
        candidates.extend(sorted(Path(home).glob('profiles/*/bigfeels')))
    for candidate in candidates:
        try:
            if candidate.expanduser().resolve() == here:
                continue
            config = configuration(candidate)
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        if config and config.get('vault_id') == vault_id and config.get('mode') != 'remote':
            return str(candidate)
    return None


def is_remote(data_dir):
    """True when this data directory is paired in cloud-only mode (no local copy)."""
    try:
        config = configuration(data_dir)
    except (ValueError, OSError, json.JSONDecodeError):
        return False
    return bool(config) and config.get('mode') == 'remote'


class RemoteClient:
    """Same surface as LocalClient, but every call is answered by the user's own cloud vault.

    Several agents on one host (or on different computers) share one memory with no replica, no sync
    cursor and no duplicate download. The price is a network call per recall and the vault being the
    only copy, so keep Cloudflare's own backups in mind. The cloud runs no extraction model, so
    `observe` stores evidence and explicit saves (`remember`) are the way memories are made.
    """

    def __init__(self, data_dir=None, spaces=('owner',), name='remote', extractor=None, auto_process=True,
                 auto_sync=True, timeout=8):
        from .paths import default_data_dir
        self.data_dir = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self.config, self.client = configured_client(self.data_dir)
        if self.config.get('mode') != 'remote':
            raise ValueError('This data directory is not paired in cloud-only mode')
        allowed = [s for s in spaces if s in self.config['spaces']]
        if not allowed:
            raise ValueError('None of the requested spaces is authorized for this device')
        self.spaces, self.name, self.timeout, self.extractor = tuple(allowed), name, timeout, None
        self._closed = False

    def call(self, operation, payload):
        from .client import OPERATIONS
        if self._closed:
            raise ClientError('Remote memory is closed', 503)
        if operation not in OPERATIONS:
            raise ClientError('Unknown operation', 404)
        if operation == 'process':
            return {'processed': 0}
        if not isinstance(payload, dict):
            raise ClientError('Request must be an object', 400)
        # Recall runs in front of a chat turn: give up quickly and let the agent answer without memory.
        wait = min(self.timeout, RECALL_TIMEOUT_SECONDS) if operation in ('context', 'search') else self.timeout
        return self.client.request('/v1/' + operation, payload, timeout=wait)

    post = call

    def process_pending(self, limit=8):
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError('Processing limit must be between 1 and 8')
        return 0

    def close(self):
        self._closed = True


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


def connect(data_dir, url, secret, *, spaces=('owner',), name='device', invite=False, allow_local=False, interval=30,
            remote=False):
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
    if remote:
        # Cloud-only: no local database, nothing to sync. Every call goes to the vault.
        config['mode'] = 'remote'
        _private_json(Path(data_dir) / CONFIG_NAME, config, replace=False)
        return {'status': 'connected', 'mode': 'remote', 'spaces': config['spaces'], 'recommendation': PLAN_RECOMMENDATION}
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
    result = {'status': 'connected', 'spaces': config['spaces'], 'recommendation': PLAN_RECOMMENDATION}
    twin = other_pairing(data_dir, config['vault_id'])
    if twin:
        result['warning'] = (f'{twin} on this computer is already paired to the same vault. Two stores on one host '
                             'each download and upload the whole memory; share one data directory instead.')
    return result


def configured_client(data_dir):
    config = configuration(data_dir)
    if config is None:
        raise ValueError('Not connected; run cloud connect first')
    return config, CloudClient(config['url'], config['token'], allow_local=config.get('allow_local', False))


def resync(store, config):
    """Forget what is known about the cloud and replay everything: the safe answer to a restored backup.

    Local rows, tombstones included, are queued again and the pull cursor returns to the start, so the
    cloud regains anything it lost and this device relearns anything it missed. Local data is untouched.
    """
    with store.connection(True) as c:
        for key in ('pushed', 'pulled', 'head'):
            sync.set_state(c, key, 0)
        c.execute('DELETE FROM sync_log')
        sync.bootstrap(c, config['spaces'])


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
    moved = 0
    checked = restarted = False

    def pull():
        """Apply the cloud's changes. Returns False when stopped."""
        nonlocal moved, checked, restarted
        for _ in range(1000):
            if stop and stop.is_set():
                return False
            with store.connection(True) as c:
                lease(c)
                cursor = sync.state(c, 'pulled', 0)
            page = client.request('/v1/sync/pull?since=' + str(cursor))
            if not checked:
                # A pull names its vault, so an idle cycle needs one request. Older Workers do not.
                vault = page.get('vault_id') or client.info()['vault_id']
                if vault != config['vault_id']:
                    raise ClientError('Cloud database changed; disconnect and reconnect to bootstrap safely', 409)
                checked = True
            head = page.get('head')
            with store.connection() as c:
                seen_head = sync.state(c, 'head', 0)
            if type(head) is int and (cursor > head or head < seen_head):
                # The cloud's history is shorter than what this device has already read: it was restored
                # from a backup. Journal numbers are reused, so the cursor is meaningless. Replay every
                # local row (deletion fences included) and read the cloud again from the start.
                # A restore that has since grown past this device's last view cannot be told apart; the
                # README's recovery steps (`cloud resync`) cover it.
                if restarted:
                    raise ClientError('Cloud history is inconsistent; run cloud resync', 409)
                restarted = True
                resync(store, config)
                continue
            with store.connection(True) as c:
                lease(c)
                # Quiet: rows the cloud just sent must not be queued to go straight back.
                sync.apply(store, c, page, config['spaces'], quiet=True)
                sync.set_state(c, 'pulled', page['cursor'])
                if type(head) is int:
                    sync.set_state(c, 'head', head)
            moved += len(page['changes'])
            if not page['has_more']:
                return True
        raise ClientError('Sync cycle limit reached; run sync again', 429)

    def push():
        """Send local changes. Returns (completed, sent_any)."""
        nonlocal moved
        sent = False
        for _ in range(1000):
            if stop and stop.is_set():
                return False, sent
            with store.connection(True) as c:
                lease(c)
                cursor = sync.state(c, 'pushed', 0)
                page = sync.changes(c, config['spaces'], cursor, assume_held=True)
            if page['changes']:
                client.request('/v1/sync/push', page)
                sent = True
                moved += len(page['changes'])
            with store.connection(True) as c:
                lease(c)
                sync.set_state(c, 'pushed', page['cursor'])
                # Only discard acknowledged references, never a newer
                # concurrent edit of the same row.
                c.execute('DELETE FROM sync_log WHERE seq<=?', (page['cursor'],))
            if not page['has_more']:
                return True, sent
        raise ClientError('Sync cycle limit reached; run sync again', 429)

    try:
        # Pull first so an offline client learns deletions before uploading old data. Both directions
        # use the same merge function. After a push, pull once more to pick up anything that landed
        # meanwhile; an idle cycle (nothing to send) is a single request.
        if not pull():
            return {'status': 'stopped'}
        done, sent = push()
        if not done or (sent and not pull()):
            return {'status': 'stopped'}
        with store.connection(True) as c:
            lease(c)
            sync.set_state(c, 'last_sync', now())
            sync.set_state(c, 'last_error', None)
        return {'status': 'synced', 'moved': moved, **status(store)}
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
    """Keeps one data directory in step with the cloud: quick while there is activity, slow when idle."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='bigfeels-cloud-sync', daemon=True)

    def start(self):
        self.thread.start()
        return self

    @staticmethod
    def idle_delay(interval, idle_cycles):
        """Poll at `interval` for the first few quiet cycles, then double up to IDLE_MAX_SECONDS."""
        if idle_cycles < 3:
            return interval
        return min(interval * 2 ** (idle_cycles - 2), max(IDLE_MAX_SECONDS, interval))

    def run(self):
        if self.stop_event.wait(0.25):
            return
        delay, idle = 1, 0
        while not self.stop_event.is_set():
            try:
                config = configuration(self.data_dir)
                if not config or config.get('mode') == 'remote':
                    return
                result = sync_once(self.data_dir, stop=self.stop_event)
                interval = config.get('interval', 30)
                idle = idle + 1 if result.get('status') == 'synced' and not result.get('moved') else 0
                delay = self.idle_delay(interval, idle)
            except Exception:
                delay = min(max(delay * 2, 5), 300)
            # A local write wakes the loop and ends the quiet streak.
            if self.wake_event.wait(delay):
                idle = 0
            self.wake_event.clear()

    def wake(self):
        self.wake_event.set()

    def close(self):
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=0.1)
