"""Small DB-API adapter over Durable Object SQL; no in-memory database copy."""
from contextlib import contextmanager
from bigfeels_mem.store import Store, now
from bigfeels_mem.schema import SCHEMA, migrate_schema


class Row:
    def __init__(self, value):
        self.value = dict(value)
        self.values = tuple(self.value.values())
    def __getitem__(self, key):
        return self.values[key] if isinstance(key, (int, slice)) else self.value[key]
    def __iter__(self):
        return iter(self.values)
    def keys(self):
        return self.value.keys()


class Cursor:
    def __init__(self, rows):
        self.rows = iter(Row(row) for row in rows)
    def __iter__(self):
        return self.rows
    def fetchone(self):
        return next(self.rows, None)
    def fetchall(self):
        return list(self.rows)


class Connection:
    def __init__(self, sql):
        self.sql = sql
    def execute(self, statement, parameters=()):
        return Cursor(self.sql.exec(statement, *parameters).toArray())
    def executemany(self, statement, rows):
        for row in rows:
            self.execute(statement, row)


class CloudStore(Store):
    def __init__(self, storage):
        self.storage = storage
        self.db = Connection(storage.sql)
        self.embedder = None
        self._vectors = {}
        self.provider_status = {'extraction': 'not_configured', 'embeddings': 'not_configured'}
        existing = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone()
        if not existing:
            storage.sql.exec(SCHEMA)
        else:
            migrate_schema(self.db)

    @contextmanager
    def connection(self, write=False):
        # Every request is wrapped in storage.transactionSync by the caller.
        # This context must never perform I/O or await within that transaction.
        yield self.db

    def _status(self, c, p, d):
        result = super()._status(c, p, d)
        result['cloud'] = {'enabled': True, 'role': 'replica', 'protocol': 1,
                           'recommended_plan': 'Workers Paid'}
        return result

    def maintenance(self):
        self.db.execute('UPDATE evidence SET content=NULL WHERE expires_at<=? AND content IS NOT NULL', (now(),))
        self.db.execute('DELETE FROM jobs WHERE evidence_id IN (SELECT id FROM evidence WHERE content IS NULL)')
        # Cloudflare owns physical page reclamation and PITR retention.
