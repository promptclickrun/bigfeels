"""Native local access: filesystem permissions replace network pairing.

Scopes constrain each integration's operations and model submissions. They are
not a sandbox against another process already running as the same OS user.
"""
from pathlib import Path
import threading
import time

from .client import ClientError
from .paths import default_data_dir
from .store import MemoryError, Principal, Store, required, string_list


class LocalClient:
    def __init__(self, data_dir=None, spaces=('owner',), name='local', extractor=None, auto_process=True):
        allowed = string_list(list(spaces), 'spaces')
        if not allowed:
            raise ValueError('At least one memory space is required')
        for space in allowed:
            required({'space': space}, 'space', 200)
        self.principal = Principal(name, tuple(allowed))
        self.data_dir = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self.store = Store(self.data_dir / 'memory.sqlite')
        for space in allowed:
            self.store.create_space(space)
        self.extractor = extractor
        self.store.provider_status = {
            'extraction': 'host' if extractor else 'not_configured',
            'embeddings': 'not_configured',
        }
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._processing = threading.Lock()
        self._thread = None
        if extractor and auto_process:
            self._thread = threading.Thread(target=self._run, name='bigfeels-local-worker', daemon=True)
            self._thread.start()

    def call(self, operation, payload):
        if self._stop.is_set():
            raise ClientError('Local memory is closed', 503)
        try:
            result = self.store.dispatch(self.principal, operation, payload)
        except MemoryError as error:
            raise ClientError(str(error), error.status) from None
        if operation == 'observe':
            self._wake.set()
        if operation == 'forget':
            try:
                self.store.maintenance()
            except Exception:
                # Logical deletion committed; report any remaining physical work.
                result['maintenance'] = 'pending'
        return result

    post = call

    def process_pending(self, limit=8):
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError('Processing limit must be between 1 and 8')
        if not self.extractor or self._stop.is_set():
            return 0
        if not self._processing.acquire(blocking=False):
            return 0
        processed = 0
        try:
            while processed < limit and not self._stop.is_set():
                if not self.store.process_one(self.extractor, spaces=self.principal.spaces):
                    break
                processed += 1
        finally:
            self._processing.release()
        return processed

    def _run(self):
        maintenance_at = 0
        while not self._stop.is_set():
            try:
                if time.monotonic() >= maintenance_at:
                    self.store.maintenance()
                    maintenance_at = time.monotonic() + 60
                self.process_pending()
            except Exception:
                # Queue state persists; never log evidence or provider errors.
                pass
            self._wake.wait(1)
            self._wake.clear()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
