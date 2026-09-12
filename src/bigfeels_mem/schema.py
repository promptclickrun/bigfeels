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
 error TEXT, lease_token TEXT);
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
"""
