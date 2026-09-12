"""Transactional evidence, knowledge, access control, and durable processing."""
from contextlib import contextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from .privacy import redact
from .retrieval import cosine, fts_query, token_cost
from .schema import SCHEMA


class MemoryError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    name: str
    spaces: tuple


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def stamp(value=None):
    if value is None:
        return now()
    if not isinstance(value, str):
        raise MemoryError('Timestamp must be an ISO-8601 string')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except ValueError:
        raise MemoryError('Timestamp requires a timezone') from None


def uid(prefix):
    return prefix + '_' + uuid.uuid4().hex


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def required(data, key, maximum=100000):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise MemoryError(f'{key} must be a nonempty string up to {maximum} characters')
    return value


def choice(data, key, default, values):
    v = data.get(key, default)
    if not isinstance(v, str) or v not in values:
        raise MemoryError(f'Invalid {key}')
    return v


def string_list(value, name):
    if not isinstance(value, list) or len(value) > 1000 or any(not isinstance(x, str) for x in value):
        raise MemoryError(f'{name} must be a list of strings')
    return list(dict.fromkeys(value))


class Store:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.embedder = None
        self.provider_status = {'extraction': 'not_configured', 'embeddings': 'not_configured'}
        # Reserve the file privately before sqlite creates it (including under
        # permissive process umasks). WAL inherits database permissions.
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with closing(sqlite3.connect(self.path, timeout=10)) as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript(SCHEMA)
            version = c.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
            if version != '1':
                raise MemoryError('Unsupported database schema', 409)

    @contextmanager
    def connection(self, write=False):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA secure_delete=ON')
        try:
            c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def create_space(self, name):
        required({'name': name}, 'name', 200)
        with self.connection(True) as c:
            c.execute('INSERT OR IGNORE INTO spaces VALUES (?)', (name,))
        return {'name': name}

    def pair(self, name, spaces):
        required({'name': name}, 'name', 200)
        spaces = string_list(spaces, 'spaces')
        if not spaces:
            raise MemoryError('At least one space is required')
        token = secrets.token_urlsafe(32)
        with self.connection(True) as c:
            known = {x[0] for x in c.execute('SELECT name FROM spaces')}
            if not set(spaces) <= known:
                raise MemoryError('Create spaces locally before pairing')
            c.execute('INSERT INTO credentials VALUES (?,?,?)', (digest(token), name, json.dumps(spaces)))
        return token

    def revoke(self, token):
        with self.connection(True) as c:
            c.execute('DELETE FROM credentials WHERE digest=?', (digest(token),))

    def authenticate(self, token):
        if not isinstance(token, str) or not token or len(token) > 500:
            raise MemoryError('Invalid credential', 401)
        with self.connection() as c:
            row = c.execute('SELECT name,spaces FROM credentials WHERE digest=?', (digest(token),)).fetchone()
        if row is None:
            raise MemoryError('Invalid credential', 401)
        return Principal(row['name'], tuple(json.loads(row['spaces'])))

    def _scope(self, principal, space):
        if space not in principal.spaces:
            raise MemoryError('Space is not authorized', 403)
        return space

    def _item(self, c, principal, item_id, table):
        required({'id': item_id}, 'id', 200)
        row = c.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone()
        if row is None:
            raise MemoryError('Item not found', 404)
        self._scope(principal, row['space'])
        return dict(row)

    def dispatch(self, principal, operation, payload):
        if not isinstance(principal, Principal):
            raise MemoryError('Invalid credential', 401)
        if not isinstance(payload, dict):
            raise MemoryError('Request must be an object')
        operations = {'observe': self._observe, 'remember': self._remember,
                      'correct': self._correct, 'forget': self._forget,
                      'inspect': self._inspect, 'status': self._status, 'export': self._export}
        if operation in ('search', 'context'):
            return self._search(principal, payload, browsing=operation == 'search')
        handler = operations.get(operation)
        if handler is None:
            raise MemoryError('Unknown operation', 404)
        with self.connection(operation in ('observe', 'remember', 'correct', 'forget')) as c:
            return handler(c, principal, payload)

    def _observe(self, c, p, d):
        space = self._scope(p, required(d, 'space', 200))
        if d.get('captured', True) is False:
            return {'id': None, 'status': 'skipped'}
        if not isinstance(d.get('captured', True), bool):
            raise MemoryError('captured must be boolean')
        source = required(d, 'source', 200)
        event_id = required(d, 'source_event_id', 500)
        identity = digest(json.dumps([space, source, event_id]))
        tomb = c.execute('SELECT id FROM tombstones WHERE identity=?', (identity,)).fetchone()
        if tomb:
            return {'id': tomb[0], 'status': 'deleted'}
        origins = string_list(d.get('origin_ids', []), 'origin_ids')
        if origins:
            for origin in origins:
                tomb = c.execute('SELECT space FROM tombstones WHERE id=?', (origin,)).fetchone()
                if tomb:
                    self._scope(p, tomb[0])
                    return {'id': origin, 'status': 'deleted'}
                e = self._item(c, p, origin, 'evidence')
                if e['space'] != space:
                    raise MemoryError('Echo origins must stay in their original space')
            return {'id': origins[0], 'status': 'echo'}
        content = redact(required(d, 'content'))
        speaker = choice(d, 'speaker', 'user', ('user', 'assistant', 'tool', 'document'))
        session = required(d, 'session_id', 500)
        occurred = stamp(d.get('occurred_at'))
        expires = stamp(d['expires_at']) if d.get('expires_at') else None
        fingerprint = digest(json.dumps([content, speaker, session, d.get('occurred_at'), expires]))
        existing = c.execute('SELECT id,fingerprint FROM evidence WHERE identity=?', (identity,)).fetchone()
        if existing:
            if existing['fingerprint'] != fingerprint:
                raise MemoryError('Source event already exists with different content', 409)
            return {'id': existing['id'], 'status': 'duplicate'}
        eid = uid('ev')
        c.execute('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                  (eid, space, identity, source, event_id, session, speaker, content,
                   fingerprint, occurred, now(), expires))
        c.execute('INSERT INTO jobs(evidence_id) VALUES (?)', (eid,))
        return {'id': eid, 'status': 'queued'}

    def _remember(self, c, p, d):
        space = self._scope(p, required(d, 'space', 200))
        content = redact(required(d, 'content', 16000))
        kind = choice(d, 'kind', 'fact', ('fact', 'preference', 'decision', 'episode', 'procedure', 'task'))
        basis = choice(d, 'basis', 'direct', ('direct', 'observed', 'inferred'))
        outcome = choice(d, 'outcome', 'unspecified', ('unspecified', 'proposed', 'attempted', 'verified', 'failed'))
        if outcome == 'verified' and basis != 'observed':
            raise MemoryError('Verified outcomes require observed evidence')
        start = stamp(d.get('valid_from'))
        end = stamp(d['valid_until']) if d.get('valid_until') else None
        if end and end <= start:
            raise MemoryError('valid_until must follow valid_from')
        key = d.get('key')
        if key is not None:
            required(d, 'key', 500)
        evidence_ids = string_list(d.get('evidence_ids', []), 'evidence_ids')
        for eid in evidence_ids:
            evidence = self._item(c, p, eid, 'evidence')
            if evidence['space'] != space:
                raise MemoryError('Evidence cannot cross memory spaces')
            if outcome == 'verified' and evidence['speaker'] != 'tool':
                raise MemoryError('Verified outcomes require a tool observation')
        # Resolve repeats before synthesizing explicit evidence. Every supplied
        # source still joins lineage, so forgetting cannot leave an orphan copy.
        existing = None
        for row in c.execute("SELECT * FROM memories WHERE space=? AND content=? AND basis=? AND kind=? AND outcome=? AND key IS ? AND status IN ('active','candidate','disputed')",
                             (space, content, basis, kind, outcome, key)):
            same_interval = row['valid_from'] == start and row['valid_until'] == end
            same_current = row['valid_from'] <= start and row['valid_until'] == end and (end is None or end > start)
            if same_interval or same_current:
                existing = row['id']
                break
        if existing:
            c.executemany('INSERT OR IGNORE INTO supports VALUES (?,?)', [(existing, e) for e in evidence_ids])
            return self._memory(c, p, existing)
        if not evidence_ids:
            if outcome == 'verified':
                raise MemoryError('Verified outcomes require source evidence')
            event = self._observe(c, p, dict(space=space, source='explicit:' + p.name,
                      source_event_id=uid('save'), session_id='explicit', speaker='user', content=content))
            evidence_ids = [event['id']]
            c.execute('DELETE FROM jobs WHERE evidence_id=?', (event['id'],))
        status = 'candidate' if basis == 'inferred' else 'active'
        mid = uid('mem')
        conflicts = []
        if key and status == 'active':
            conflicts = list(c.execute("SELECT id FROM memories WHERE space=? AND key=? AND content!=? AND status IN ('active','disputed') AND (valid_until IS NULL OR valid_until>?) AND (? IS NULL OR valid_from<?)",
                                       (space, key, content, start, end, end)))
            if conflicts:
                status = 'disputed'
                for row in conflicts:
                    c.execute("UPDATE memories SET status='disputed',revision=revision+1 WHERE id=?", (row[0],))
        c.execute('INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                  (mid, space, content, kind, basis, outcome, key, status, 1, now(), start, end))
        c.executemany('INSERT INTO supports VALUES (?,?)', [(mid, e) for e in evidence_ids])
        for row in conflicts:
            c.execute('INSERT INTO relations VALUES (?,?,?)', (mid, row[0], 'contradicts'))
        return self._memory(c, p, mid)

    def _memory(self, c, p, mid):
        m = self._item(c, p, mid, 'memories')
        es = list(c.execute('SELECT e.id,e.content FROM evidence e JOIN supports s ON s.evidence_id=e.id WHERE s.memory_id=? ORDER BY e.id', (mid,)))
        m['evidence_ids'] = [e['id'] for e in es]
        m['source_available'] = bool(es) and all(e['content'] is not None for e in es)
        return m

    def _inspect(self, c, p, d):
        mid = required(d, 'id', 200)
        if mid.startswith('ev_'):
            e = self._item(c, p, mid, 'evidence')
            e.pop('fingerprint', None)
            e.pop('identity', None)
            return e
        m = self._memory(c, p, mid)
        m['evidence'] = [self._inspect(c, p, {'id': e}) for e in m['evidence_ids']]
        m['relations'] = [dict(r) for r in c.execute('SELECT * FROM relations WHERE source_id=? OR target_id=?', (mid, mid))]
        return m

    def _correct(self, c, p, d):
        old = self._item(c, p, required(d, 'id', 200), 'memories')
        if type(d.get('revision')) is not int or d['revision'] != old['revision'] or old['status'] == 'superseded':
            raise MemoryError('Memory changed; inspect its current revision', 409)
        at = stamp(d.get('valid_from'))
        if at <= old['valid_from']:
            raise MemoryError('Correction time must follow original validity')
        old_end = min(old['valid_until'], at) if old['valid_until'] else at
        c.execute("UPDATE memories SET status='superseded',valid_until=?,revision=revision+1 WHERE id=?", (old_end, old['id']))
        content = required(d, 'content', 16000)
        correction = self._observe(c, p, dict(space=old['space'], source='correction:' + p.name,
                    source_event_id=uid('correction'), session_id='explicit', speaker='user', content=content))
        c.execute('DELETE FROM jobs WHERE evidence_id=?', (correction['id'],))
        new = self._remember(c, p, dict(space=old['space'], content=content,
                              kind=old['kind'], key=old['key'], basis='direct', valid_from=at,
                              evidence_ids=[correction['id']]))
        c.execute('INSERT INTO relations VALUES (?,?,?)', (new['id'], old['id'], 'supersedes'))
        if new['status'] == 'disputed' and not c.execute("SELECT 1 FROM memories WHERE space=? AND key=? AND content!=? AND status IN ('active','disputed') AND valid_from<=? AND (valid_until IS NULL OR valid_until>?) LIMIT 1", (new['space'], new['key'], new['content'], at, at)).fetchone():
            c.execute("UPDATE memories SET status='active',revision=revision+1 WHERE id=?", (new['id'],))
        return self._memory(c, p, new['id'])

    def _forget(self, c, p, d):
        item_id = required(d, 'id', 200)
        tomb = c.execute('SELECT space FROM tombstones WHERE id=?', (item_id,)).fetchone()
        if tomb:
            self._scope(p, tomb[0])
            return {'memories': 0, 'evidence': 0, 'status': 'deleted'}
        if item_id.startswith('ev_'):
            self._item(c, p, item_id, 'evidence')
            eids = {item_id}
            mids = set()
        else:
            self._item(c, p, item_id, 'memories')
            eids, mids = set(), {item_id}
        # Close over all supporting evidence and derived memories, including
        # corrections, so deleted knowledge cannot survive in a derivative.
        changed = True
        while changed:
            before = (len(eids), len(mids))
            for mid in tuple(mids):
                eids.update(r[0] for r in c.execute('SELECT evidence_id FROM supports WHERE memory_id=?', (mid,)))
                for r in c.execute("SELECT source_id,target_id FROM relations WHERE kind='supersedes' AND (source_id=? OR target_id=?)", (mid, mid)):
                    mids.update(r)
            for eid in tuple(eids):
                mids.update(r[0] for r in c.execute('SELECT memory_id FROM supports WHERE evidence_id=?', (eid,)))
            changed = before != (len(eids), len(mids))
        for eid in eids:
            e = self._item(c, p, eid, 'evidence')
            c.execute('INSERT OR IGNORE INTO tombstones VALUES (?,?,?)', (eid, e['identity'], e['space']))
        for mid in mids:
            m = self._item(c, p, mid, 'memories')
            c.execute('INSERT OR IGNORE INTO tombstones VALUES (?,?,?)', (mid, None, m['space']))
            c.execute('DELETE FROM memories WHERE id=?', (mid,))
        for eid in eids:
            c.execute('DELETE FROM evidence WHERE id=?', (eid,))
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('needs_purge','1')")
        return {'memories': len(mids), 'evidence': len(eids), 'status': 'deleted'}

    def _search(self, p, d, browsing=False):
        query = d.get('query', '')
        if not isinstance(query, str) or len(query) > 8000:
            raise MemoryError('query must be a string up to 8000 characters')
        budget = d.get('budget', 800)
        if type(budget) is not int or not 1 <= budget <= 32000:
            raise MemoryError('budget must be between 1 and 32000')
        spaces = string_list(d.get('spaces', []), 'spaces') or list(p.spaces)
        for space in spaces:
            self._scope(p, space)
        at = stamp(d.get('as_of'))
        include_inactive = d.get('include_inactive', d.get('include_candidates', False))
        if not isinstance(include_inactive, bool):
            raise MemoryError('include_inactive must be boolean')
        include_inactive = include_inactive and browsing
        vector, vector_model, embedding_status = None, None, 'not_configured'
        if query.strip() and self.embedder:
            try:
                vector = self.embedder.embed([redact(query)])[0]
                vector_model = self.embedder.model
                embedding_status = 'available'
            except Exception:
                embedding_status = 'unavailable'
        with self.connection() as c:
            marks = ','.join('?' for _ in spaces)
            predicate = '' if include_inactive else " AND status IN ('active','disputed','superseded') AND valid_from<=? AND (valid_until IS NULL OR valid_until>?)"
            eligible = {r['id']: dict(r) for r in c.execute(
                f'SELECT * FROM memories WHERE space IN ({marks})' + predicate,
                tuple(spaces) if include_inactive else (*spaces, at, at))}
            scores, reasons = {}, {}
            fq = fts_query(query)
            if fq and eligible:
                # Rank a permitted projection so even BM25 corpus statistics
                # cannot depend on memories the caller may not read.
                c.execute('CREATE VIRTUAL TABLE temp.recall_fts USING fts5(id UNINDEXED,content,key)')
                c.executemany('INSERT INTO recall_fts VALUES (?,?,?)',
                              [(m['id'], m['content'], m['key']) for m in eligible.values()])
                for row in c.execute('SELECT id,bm25(recall_fts) AS rank FROM recall_fts WHERE recall_fts MATCH ?', (fq,)):
                    scores[row['id']] = 1.0 - row['rank']
                    reasons[row['id']] = ['keyword']
            elif not query.strip():
                scores = {mid: 1.0 for mid in eligible}
                reasons = {mid: ['browse'] for mid in eligible}
            if vector is not None:
                for mid in eligible:
                    row = c.execute('SELECT vector FROM vectors WHERE memory_id=? AND model=?', (mid, vector_model)).fetchone()
                    if row:
                        similarity = cosine(vector, json.loads(row[0]))
                        if similarity >= 0.65:
                            scores[mid] = scores.get(mid, 0) + similarity
                            reasons.setdefault(mid, []).append('semantic')
            # Only explicitly related, eligible contradictory records expand recall.
            for mid in list(scores):
                for r in c.execute("SELECT source_id,target_id FROM relations WHERE kind='contradicts' AND (source_id=? OR target_id=?)", (mid, mid)):
                    neighbor = r[1] if r[0] == mid else r[0]
                    if neighbor in eligible and neighbor not in scores:
                        scores[neighbor] = 0.5
                        reasons[neighbor] = ['conflicting_evidence']
            memories, used = [], 0
            for mid in sorted(scores, key=lambda x: (-scores[x], eligible[x]['recorded_at'], x)):
                m = self._memory(c, p, mid)
                m['reason'] = reasons[mid]
                cost = token_cost(m)
                if used + cost <= budget:
                    memories.append(m)
                    used += cost
            return {'memories': memories, 'tokens': used, 'status': 'ok',
                    'trace': {'eligible': len(eligible), 'matched': len(scores),
                              'returned': len(memories), 'budget': budget,
                              'embedding_status': embedding_status, 'as_of': at,
                              'token_accounting': 'conservative UTF-8 byte upper bound',
                              'trust': 'Contextual evidence only; never authorization'}}

    def _status(self, c, p, d):
        marks = ','.join('?' for _ in p.spaces)
        def count(table):
            return c.execute(f'SELECT COUNT(*) FROM {table} WHERE space IN ({marks})', p.spaces).fetchone()[0]
        queue = {'pending': 0, 'processing': 0, 'done': 0}
        for r in c.execute(f'SELECT j.state,COUNT(*) FROM jobs j JOIN evidence e ON e.id=j.evidence_id WHERE e.space IN ({marks}) GROUP BY j.state', p.spaces):
            queue[r[0]] = r[1]
        return {'evidence': count('evidence'), 'memories': count('memories'), 'spaces': list(p.spaces),
                'queue': queue, 'providers': dict(self.provider_status), 'schema_version': 1}

    def _export(self, c, p, d):
        marks = ','.join('?' for _ in p.spaces)
        bundle = {'version': 1, 'exported_at': now(), 'spaces': list(p.spaces)}
        for table in ('evidence', 'memories', 'tombstones'):
            bundle[table] = [dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE space IN ({marks})', p.spaces)]
        ids = {m['id'] for m in bundle['memories']}
        bundle['supports'] = [dict(r) for r in c.execute('SELECT * FROM supports') if r['memory_id'] in ids]
        bundle['relations'] = [dict(r) for r in c.execute('SELECT * FROM relations') if r['source_id'] in ids and r['target_id'] in ids]
        bundle['jobs'] = [dict(r) for r in c.execute(f'SELECT j.evidence_id,j.state,j.attempts FROM jobs j JOIN evidence e ON e.id=j.evidence_id WHERE e.space IN ({marks})', p.spaces)]
        return bundle

    def restore(self, bundle):
        if not isinstance(bundle, dict) or bundle.get('version') != 1:
            raise MemoryError('Unsupported export version')
        # Restore is deliberately only into an empty database: merging would
        # require reconciling revisions and deletion histories.
        with self.connection(True) as c:
            if c.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] or c.execute('SELECT COUNT(*) FROM tombstones').fetchone()[0]:
                raise MemoryError('Restore requires an empty database', 409)
            spaces = string_list(bundle.get('spaces'), 'spaces')
            for s in spaces:
                c.execute('INSERT OR IGNORE INTO spaces VALUES (?)', (s,))
            try:
                for table in ('evidence', 'memories', 'tombstones', 'supports', 'relations'):
                    allowed = [r[1] for r in c.execute(f'PRAGMA table_info({table})')]
                    for row in bundle.get(table, []):
                        if not isinstance(row, dict) or set(row) != set(allowed):
                            raise MemoryError('Invalid export record')
                        if 'space' in row and row['space'] not in spaces:
                            raise MemoryError('Invalid export space')
                        c.execute(f'INSERT INTO {table} ({",".join(allowed)}) VALUES ({",".join("?" for _ in allowed)})', [row[k] for k in allowed])
                for job in bundle.get('jobs', []):
                    if not isinstance(job, dict) or set(job) != {'evidence_id', 'state', 'attempts'} or job['state'] not in ('pending', 'processing', 'done') or type(job['attempts']) is not int or job['attempts'] < 0:
                        raise MemoryError('Invalid export job')
                    c.execute('INSERT INTO jobs(evidence_id,state,attempts) VALUES (?,?,?)',
                              (job['evidence_id'], 'done' if job['state'] == 'done' else 'pending', job['attempts']))
                if c.execute('SELECT 1 FROM supports s JOIN memories m ON m.id=s.memory_id JOIN evidence e ON e.id=s.evidence_id WHERE m.space!=e.space LIMIT 1').fetchone():
                    raise MemoryError('Export has evidence crossing spaces')
                if c.execute('SELECT 1 FROM relations r JOIN memories a ON a.id=r.source_id JOIN memories b ON b.id=r.target_id WHERE a.space!=b.space LIMIT 1').fetchone():
                    raise MemoryError('Export has relationships crossing spaces')
                if c.execute('SELECT 1 FROM evidence e JOIN tombstones t ON e.id=t.id OR e.identity=t.identity LIMIT 1').fetchone():
                    raise MemoryError('Export contains deleted evidence')
                if c.execute('SELECT 1 FROM memories m JOIN tombstones t ON m.id=t.id LIMIT 1').fetchone():
                    raise MemoryError('Export contains deleted memories')
            except (sqlite3.Error, TypeError, KeyError):
                raise MemoryError('Invalid export; transaction rolled back') from None
        return {'status': 'restored'}

    def maintenance(self):
        with self.connection(True) as c:
            ids = [r[0] for r in c.execute('SELECT id FROM evidence WHERE expires_at<=? AND content IS NOT NULL', (now(),))]
            for eid in ids:
                c.execute('UPDATE evidence SET content=NULL WHERE id=?', (eid,))
                c.execute('DELETE FROM jobs WHERE evidence_id=?', (eid,))
            if ids:
                c.execute("INSERT OR REPLACE INTO metadata VALUES ('needs_purge','1')")
            purge = c.execute("SELECT value FROM metadata WHERE key='needs_purge'").fetchone()
            if purge and purge[0] == '1':
                c.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")
                c.execute("UPDATE metadata SET value='0' WHERE key='needs_purge'")
            c.execute("UPDATE jobs SET state='pending',lease_token=NULL WHERE state='processing' AND lease_until<?", (time.time(),))
        with closing(sqlite3.connect(self.path)) as c:
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            if purge and purge[0] == '1':
                c.execute('VACUUM')
                c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return {'expired_evidence': len(ids)}

    def process_one(self, extractor, embedder=None, *, spaces=None):
        lease = uid('lease')
        scope_sql = ''
        scope_args = ()
        if spaces is not None:
            scope_args = tuple(string_list(list(spaces), 'spaces'))
            if not scope_args:
                return False
            scope_sql = ' AND e.space IN (' + ','.join('?' for _ in scope_args) + ')'
        with self.connection(True) as c:
            row = c.execute("SELECT e.* FROM jobs j JOIN evidence e ON e.id=j.evidence_id WHERE e.content IS NOT NULL AND (e.expires_at IS NULL OR e.expires_at>?) AND ((j.state='pending' AND j.retry_at<=?) OR (j.state='processing' AND j.lease_until<?))" + scope_sql + " ORDER BY e.recorded_at LIMIT 1", (now(), time.time(), time.time(), *scope_args)).fetchone()
            if not row:
                return False
            e = dict(row)
            c.execute("UPDATE jobs SET state='processing',lease_until=?,lease_token=?,attempts=attempts+1 WHERE evidence_id=?", (time.time()+120, lease, e['id']))
        try:
            candidates = extractor.extract(e)
            if not isinstance(candidates, list) or len(candidates) > 32:
                raise ValueError('Invalid extraction')
            mids = []
            with self.connection(True) as c:
                live = c.execute("SELECT e.content FROM evidence e JOIN jobs j ON j.evidence_id=e.id WHERE e.id=? AND j.lease_token=? AND (e.expires_at IS NULL OR e.expires_at>?)", (e['id'], lease, now())).fetchone()
                if not live or live[0] is None:
                    return False
                p = Principal('extractor', (e['space'],))
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    quote = candidate.get('quote', '')
                    basis = candidate.get('basis', 'inferred')
                    outcome = candidate.get('outcome', 'unspecified')
                    # A substring can omit a negation or critical condition.
                    # Preserve the entire bounded evidence when promoting it;
                    # synthesis remains candidate knowledge.
                    grounded = isinstance(quote, str) and bool(quote.strip()) and quote in e['content']
                    if basis == 'direct' and e['speaker'] != 'user':
                        basis = 'inferred'
                    if basis == 'observed' and e['speaker'] != 'tool':
                        basis = 'inferred'
                    if not grounded:
                        basis = 'inferred'
                    if outcome == 'verified':
                        # A model label is not structured verification. Keep the
                        # full tool result, with no synthesized success claim.
                        outcome = 'unspecified'
                    content = e['content'] if grounded and basis != 'inferred' else candidate.get('content', '')
                    if not isinstance(content, str) or not content.strip():
                        continue
                    if len(content) > 16000:
                        basis = 'inferred'
                        content = candidate.get('content', '')
                    if not isinstance(content, str) or not content.strip():
                        continue
                    payload = {k: candidate[k] for k in ('kind', 'key') if k in candidate}
                    payload.update(space=e['space'], content=content, basis=basis, outcome=outcome,
                                   evidence_ids=[e['id']], valid_from=e['occurred_at'])
                    try:
                        m = self._remember(c, p, payload)
                        mids.append(m['id'])
                    except MemoryError:
                        continue
                c.execute("UPDATE jobs SET state='done',error=NULL,lease_token=NULL WHERE evidence_id=? AND lease_token=?", (e['id'], lease))
            if embedder:
                for mid in mids:
                    self._embed_memory(mid, embedder)
            return True
        except Exception:
            with self.connection(True) as c:
                c.execute("UPDATE jobs SET state='pending',retry_at=?,error='provider_error',lease_token=NULL WHERE evidence_id=? AND lease_token=?", (time.time()+30, e['id'], lease))
            return False

    def _embed_memory(self, mid, embedder):
        with self.connection() as c:
            row = c.execute('SELECT content,revision FROM memories WHERE id=?', (mid,)).fetchone()
        if not row:
            return
        vectors = embedder.embed([row['content']])
        with self.connection(True) as c:
            current = c.execute('SELECT revision FROM memories WHERE id=?', (mid,)).fetchone()
            if current and current[0] == row['revision']:
                c.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?)', (mid, embedder.model, json.dumps(vectors[0])))

    def rebuild_embeddings(self, embedder):
        with self.connection() as c:
            ids = [r[0] for r in c.execute('SELECT id FROM memories')]
        for mid in ids:
            self._embed_memory(mid, embedder)
        return {'indexed': len(ids), 'model': embedder.model}

    def process_embeddings(self, embedder):
        """Index one unindexed record; failures retry on the next worker tick."""
        with self.connection() as c:
            row = c.execute('SELECT id FROM memories WHERE id NOT IN (SELECT memory_id FROM vectors WHERE model=?) ORDER BY recorded_at LIMIT 1', (embedder.model,)).fetchone()
        if not row:
            return False
        self._embed_memory(row[0], embedder)
        return True
