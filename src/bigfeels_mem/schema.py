"""Versioned relational source of truth. Search indexes are disposable projections."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO metadata VALUES ('schema_version','1');
CREATE TABLE IF NOT EXISTS spaces (name TEXT PRIMARY KEY);
INSERT OR IGNORE INTO spaces VALUES ('owner');
CREATE TABLE IF NOT EXISTS credentials (digest TEXT PRIMARY KEY, name TEXT NOT NULL, spaces TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, space TEXT NOT NULL REFERENCES spaces(name), identity TEXT UNIQUE NOT NULL,
 source TEXT NOT NULL, source_event_id TEXT NOT NULL, session_id TEXT NOT NULL,
 speaker TEXT NOT NULL, content TEXT, fingerprint TEXT NOT NULL,
 occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, expires_at TEXT);
CREATE TABLE IF NOT EXISTS memories (
 id TEXT PRIMARY KEY, space TEXT NOT NULL REFERENCES spaces(name), content TEXT NOT NULL,
 kind TEXT NOT NULL, basis TEXT NOT NULL, outcome TEXT NOT NULL,
 key TEXT, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 recorded_at TEXT NOT NULL, valid_from TEXT NOT NULL, valid_until TEXT);
CREATE TABLE IF NOT EXISTS supports (
 memory_id TEXT REFERENCES memories(id) ON DELETE CASCADE,
 evidence_id TEXT REFERENCES evidence(id) ON DELETE CASCADE,
 PRIMARY KEY(memory_id,evidence_id));
CREATE TABLE IF NOT EXISTS relations (
 source_id TEXT REFERENCES memories(id) ON DELETE CASCADE,
 target_id TEXT REFERENCES memories(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, PRIMARY KEY(source_id,target_id,kind));
CREATE TABLE IF NOT EXISTS tombstones (id TEXT PRIMARY KEY, identity TEXT, space TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 evidence_id TEXT PRIMARY KEY REFERENCES evidence(id) ON DELETE CASCADE,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 lease_until REAL NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
 error TEXT, lease_token TEXT,
 accepted_count INTEGER NOT NULL DEFAULT 0,
 rejected_count INTEGER NOT NULL DEFAULT 0,
 rejection_reason TEXT);
CREATE TABLE IF NOT EXISTS vectors (
 memory_id TEXT REFERENCES memories(id) ON DELETE CASCADE,
 model TEXT NOT NULL, vector TEXT NOT NULL, PRIMARY KEY(memory_id,model));
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, content, key);
CREATE TRIGGER IF NOT EXISTS memory_insert AFTER INSERT ON memories BEGIN
 INSERT INTO memory_fts(id,content,key) VALUES(new.id,new.content,new.key);
END;
CREATE TRIGGER IF NOT EXISTS memory_delete AFTER DELETE ON memories BEGIN
 DELETE FROM memory_fts WHERE id=old.id;
END;
CREATE TRIGGER IF NOT EXISTS memory_update AFTER UPDATE OF content,key ON memories BEGIN
 DELETE FROM memory_fts WHERE id=old.id;
 INSERT INTO memory_fts(id,content,key) VALUES(new.id,new.content,new.key);
END;
CREATE INDEX IF NOT EXISTS evidence_space ON evidence(space);
CREATE INDEX IF NOT EXISTS memories_space ON memories(space,status);
CREATE INDEX IF NOT EXISTS supports_evidence ON supports(evidence_id);
CREATE INDEX IF NOT EXISTS relations_target ON relations(target_id,kind);
"""


# Version-1 preview databases predate processing diagnostics. Keep the on-disk
# version and export envelope stable while applying an additive, idempotent
# migration. These fields contain counts and controlled reason codes only.
JOB_DIAGNOSTIC_COLUMNS = {
    'accepted_count': 'INTEGER NOT NULL DEFAULT 0',
    'rejected_count': 'INTEGER NOT NULL DEFAULT 0',
    'rejection_reason': 'TEXT',
}

REQUIRED_V1_COLUMNS = {
    'metadata': {'key', 'value'},
    'spaces': {'name'},
    'credentials': {'digest', 'name', 'spaces'},
    'evidence': {
        'id', 'space', 'identity', 'source', 'source_event_id', 'session_id',
        'speaker', 'content', 'fingerprint', 'occurred_at', 'recorded_at',
        'expires_at',
    },
    'memories': {
        'id', 'space', 'content', 'kind', 'basis', 'outcome', 'key', 'status',
        'revision', 'recorded_at', 'valid_from', 'valid_until',
    },
    'supports': {'memory_id', 'evidence_id'},
    'relations': {'source_id', 'target_id', 'kind'},
    'tombstones': {'id', 'identity', 'space'},
    'jobs': {
        'evidence_id', 'state', 'attempts', 'lease_until', 'retry_at', 'error',
        'lease_token',
    },
    'vectors': {'memory_id', 'model', 'vector'},
    'memory_fts': {'id', 'content', 'key'},
}
REQUIRED_V1_TRIGGERS = {'memory_insert', 'memory_delete', 'memory_update'}


def validate_schema(connection):
    """Reject nonempty databases that are not a complete supported v1 store."""
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if not set(REQUIRED_V1_COLUMNS) <= tables:
        raise ValueError('incomplete schema')
    version = connection.execute(
        "SELECT value FROM metadata WHERE key='schema_version'"
    ).fetchone()
    if version is None or version[0] != '1':
        raise ValueError('unsupported schema version')
    for table, required in REQUIRED_V1_COLUMNS.items():
        present = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        if not required <= present:
            raise ValueError('incomplete schema')
    triggers = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    if not REQUIRED_V1_TRIGGERS <= triggers:
        raise ValueError('incomplete schema')


def migrate_schema(connection):
    """Bring a supported version-1 database to the latest additive layout."""
    validate_schema(connection)
    present = {row[1] for row in connection.execute('PRAGMA table_info(jobs)')}
    for name, definition in JOB_DIAGNOSTIC_COLUMNS.items():
        if name not in present:
            connection.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
