"""Transactional evidence, knowledge, access control, and durable processing."""
from contextlib import contextmanager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from .privacy import redact
from .retrieval import (
    claims_compatible, cosine, fts_query, lexical_matches, lexical_score,
    token_cost,
)
from .schema import JOB_DIAGNOSTIC_COLUMNS, SCHEMA, migrate_schema, validate_schema


SAFE_JOB_ERRORS = frozenset({'provider_error', 'invalid_candidates'})
SAFE_REJECTION_REASONS = frozenset({
    'invalid_candidate', 'superseded_claim', 'mixed_invalid_candidates',
})
MAX_EXTRACTION_ATTEMPTS = 3
DELETE_PLAN_TTL_SECONDS = 300


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


def normalized_claim(value):
    return ' '.join(value.casefold().strip().rstrip('.!?').split())


def grounded_claim_content(source, quote, claim):
    """Use the concise claim unless its quote omits same-sentence context."""
    offset = source.find(quote)
    if offset < 0:
        return claim
    left = max((source.rfind(mark, 0, offset) for mark in '.!?;\n'), default=-1)
    quote_end = offset + len(quote)
    if quote.rstrip().endswith(('.', '!', '?')):
        right = quote_end
    else:
        endings = [position for mark in '.!?;\n'
                   if (position := source.find(mark, quote_end)) >= 0]
        right = min(endings) + 1 if endings else len(source)
    segment = source[left + 1:right]
    leading = len(segment) - len(segment.lstrip())
    sentence = segment.strip()
    relative = offset - left - 1 - leading
    surrounding = (sentence[:relative] + ' '
                   + sentence[relative + len(quote):]).casefold()
    surrounding_words = re.findall(r'[^\W_]+', surrounding, re.UNICODE)
    meaningful = [word for word in surrounding_words
                  if word not in {'a', 'an', 'i', 'the', 'we'}]
    negations = {'not', 'no', 'never', 'without', 'unless'}
    negative_outcomes = {'fail', 'failed', 'failure', 'denied', 'error', 'unsuccessful'}
    source_negative = bool(negative_outcomes.intersection(quote.casefold().replace(':', ' ').split()))
    claim_negative = bool(negative_outcomes.intersection(claim.casefold().replace(':', ' ').split()))
    claim_words = set(re.findall(r'[^\W_]+', claim.casefold(), re.UNICODE))
    if meaningful or (negations.intersection(set(surrounding_words)) - claim_words):
        return sentence
    if source_negative != claim_negative:
        return sentence
    return claim


class Store:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.embedder = None
        self.provider_status = {'extraction': 'not_configured', 'embeddings': 'not_configured'}
        existing = self.path.exists() and self.path.stat().st_size > 0
        if existing:
            # Validate read-only before DDL, journal changes, or chmod. Rejecting
            # an unknown nonempty file must not bless or mutate it as schema v1.
            try:
                uri = self.path.resolve().as_uri() + '?mode=ro'
                with closing(sqlite3.connect(uri, uri=True, timeout=10)) as c:
                    c.execute('PRAGMA query_only=ON')
                    validate_schema(c)
            except (ValueError, sqlite3.Error):
                raise MemoryError('Unsupported database schema', 409)
            try:
                with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as c:
                    c.execute('BEGIN IMMEDIATE')
                    migrate_schema(c)
                    c.commit()
            except (ValueError, sqlite3.Error):
                raise MemoryError('Unsupported database schema', 409)
            self.path.chmod(0o600)
        else:
            # Reserve the file privately before SQLite creates pages. WAL files
            # inherit the database's permissions.
            self.path.touch(mode=0o600, exist_ok=True)
            self.path.chmod(0o600)
            with closing(sqlite3.connect(self.path, timeout=10)) as c:
                c.execute('PRAGMA journal_mode=WAL')
                c.executescript(SCHEMA)
                c.commit()

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
                      'correct': self._correct, 'forget_preview': self._forget_preview,
                      'forget': self._forget,
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
        outcome = choice(d, 'outcome', 'unspecified',
                         ('unspecified', 'proposed', 'attempted', 'attested',
                          'verified', 'failed'))
        if outcome == 'verified':
            # A caller and a referenced tool event are still one attestation,
            # not an independent semantic verifier. Legacy rows are preserved
            # and labeled honestly by _memory, but new claims cannot acquire
            # the old over-trusting label.
            outcome = 'attested'
        if outcome == 'attested' and basis != 'observed':
            raise MemoryError('Caller-attested outcomes require an observed basis')
        start = stamp(d.get('valid_from'))
        end = stamp(d['valid_until']) if d.get('valid_until') else None
        if end and end <= start:
            raise MemoryError('valid_until must follow valid_from')
        key = d.get('key')
        if key is not None:
            required(d, 'key', 500)
        evidence_ids = string_list(d.get('evidence_ids', []), 'evidence_ids')
        if outcome == 'attested' and not evidence_ids:
            raise MemoryError('Caller-attested outcomes require source evidence')
        tool_evidence = False
        for eid in evidence_ids:
            evidence = self._item(c, p, eid, 'evidence')
            if evidence['space'] != space:
                raise MemoryError('Evidence cannot cross memory spaces')
            tool_evidence = tool_evidence or evidence['speaker'] == 'tool'
        if outcome == 'attested' and not tool_evidence:
            raise MemoryError('Caller-attested outcomes require a tool observation')

        # Resolve repeats before synthesizing explicit evidence. Every supplied
        # source still joins lineage, so forgetting cannot leave an orphan copy.
        existing = None
        for row in c.execute("SELECT * FROM memories WHERE space=? AND basis=? AND kind=? AND outcome=? AND key IS ? AND status IN ('active','candidate','disputed')",
                             (space, basis, kind, outcome, key)):
            same_interval = row['valid_from'] == start and row['valid_until'] == end
            same_current = row['valid_from'] <= start and row['valid_until'] == end and (end is None or end > start)
            same_claim = normalized_claim(row['content']) == normalized_claim(content)
            if same_claim and (same_interval or same_current):
                existing = row['id']
                break
        if existing:
            c.executemany('INSERT OR IGNORE INTO supports VALUES (?,?)', [(existing, e) for e in evidence_ids])
            return self._memory(c, p, existing)
        if not evidence_ids:

            event = self._observe(c, p, dict(space=space, source='explicit:' + p.name,
                      source_event_id=uid('save'), session_id='explicit', speaker='user', content=content))
            evidence_ids = [event['id']]
            c.execute('DELETE FROM jobs WHERE evidence_id=?', (event['id'],))
        status = 'candidate' if basis == 'inferred' else 'active'
        mid = uid('mem')
        conflicts = []
        if key and status == 'active':
            possible = list(c.execute(
                "SELECT id,content FROM memories WHERE space=? AND key=? AND content!=? "
                "AND status IN ('active','disputed') AND (valid_until IS NULL OR valid_until>?) "
                "AND (? IS NULL OR valid_from<?)",
                (space, key, content, start, end, end)))
            conflicts = [row for row in possible
                         if not claims_compatible(content, row['content'])]
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
        if m['outcome'] == 'verified':
            m['outcome_trust'] = 'legacy_caller_attested_not_independently_verified'
        return m

    def _context_memory(self, c, p, mid):
        """Return a compact agent-context projection with bounded lineage refs."""
        full = self._memory(c, p, mid)
        fields = ('id', 'space', 'content', 'kind', 'basis', 'outcome', 'key',
                  'status', 'revision', 'valid_from', 'valid_until',
                  'source_available')
        compact = {key: full[key] for key in fields}
        compact['evidence_count'] = len(full['evidence_ids'])
        compact['evidence_ids'] = full['evidence_ids'][:3]
        if len(full['evidence_ids']) > 3:
            compact['evidence_refs_truncated'] = True
        if 'outcome_trust' in full:
            compact['outcome_trust'] = full['outcome_trust']
        return compact

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

    def _forget_plan(self, c, p, item_id):
        """Compute the privacy closure and a revision-bound confirmation token."""
        if item_id.startswith('ev_'):
            self._item(c, p, item_id, 'evidence')
            eids, mids = {item_id}, set()
        else:
            self._item(c, p, item_id, 'memories')
            eids, mids = set(), {item_id}
        # Close over shared evidence and correction lineage. A token binds the
        # complete dependency membership, so a later attachment cannot broaden
        # an already confirmed deletion.
        changed = True
        while changed:
            before = (len(eids), len(mids))
            for mid in tuple(mids):
                eids.update(r[0] for r in c.execute(
                    'SELECT evidence_id FROM supports WHERE memory_id=?', (mid,)))
                for row in c.execute(
                        "SELECT source_id,target_id FROM relations WHERE kind='supersedes' AND (source_id=? OR target_id=?)",
                        (mid, mid)):
                    mids.update(row)
            for eid in tuple(eids):
                mids.update(r[0] for r in c.execute(
                    'SELECT memory_id FROM supports WHERE evidence_id=?', (eid,)))
            changed = before != (len(eids), len(mids))

        memories = [self._item(c, p, mid, 'memories') for mid in sorted(mids)]
        evidence = [self._item(c, p, eid, 'evidence') for eid in sorted(eids)]
        identity = {
            'selected': item_id,
            'principal': p.name,
            'spaces': sorted(p.spaces),
            'memories': [[m['id'], m['space'], m['revision'], m['status'],
                          m['valid_from'], m['valid_until']] for m in memories],
            'evidence': [[e['id'], e['space'], e['identity'], e['fingerprint']]
                         for e in evidence],
        }
        plan_hash = digest(json.dumps(identity, sort_keys=True, separators=(',', ':')))
        return memories, evidence, plan_hash

    def _forget_preview(self, c, p, d):
        item_id = required(d, 'id', 200)
        tomb = c.execute('SELECT space FROM tombstones WHERE id=?', (item_id,)).fetchone()
        if tomb:
            self._scope(p, tomb[0])
            return {'id': item_id, 'plan_token': None, 'memories': [],
                    'counts': {'memories': 0, 'evidence': 0}, 'status': 'deleted'}
        memories, evidence, plan_hash = self._forget_plan(c, p, item_id)
        issued_at = int(time.time())
        token = f'{issued_at}.{digest(f"{issued_at}:{plan_hash}")}'
        return {
            'id': item_id,
            'plan_token': token,
            'plan_expires_at': issued_at + DELETE_PLAN_TTL_SECONDS,
            'memories': [{'id': m['id'], 'content': m['content'],
                          'revision': m['revision'], 'space': m['space']}
                         for m in memories],
            'counts': {'memories': len(memories), 'evidence': len(evidence)},
        }

    def _forget(self, c, p, d):
        item_id = required(d, 'id', 200)
        tomb = c.execute('SELECT space FROM tombstones WHERE id=?', (item_id,)).fetchone()
        if tomb:
            self._scope(p, tomb[0])
            return {'memories': 0, 'evidence': 0, 'status': 'deleted'}
        memories, evidence, plan_hash = self._forget_plan(c, p, item_id)
        supplied = d.get('plan_token')
        if supplied is not None:
            supplied = required(d, 'plan_token', 200)
            try:
                issued_text, supplied_digest = supplied.split('.', 1)
                issued_at = int(issued_text)
            except (ValueError, TypeError):
                raise MemoryError('Deletion plan changed; preview it again', 409) from None
            current_time = int(time.time())
            expected = digest(f'{issued_at}:{plan_hash}')
            if (issued_at > current_time + 5
                    or current_time - issued_at > DELETE_PLAN_TTL_SECONDS
                    or not secrets.compare_digest(supplied_digest, expected)):
                raise MemoryError('Deletion plan changed; preview it again', 409)
        else:
            selected_memories = 0 if item_id.startswith('ev_') else 1
            if len(memories) > selected_memories:
                raise MemoryError('Deletion affects collateral memories; preview and confirm the current plan', 409)

        for e in evidence:
            c.execute('INSERT OR IGNORE INTO tombstones VALUES (?,?,?)',
                      (e['id'], e['identity'], e['space']))
        for m in memories:
            c.execute('INSERT OR IGNORE INTO tombstones VALUES (?,?,?)',
                      (m['id'], None, m['space']))
            c.execute('DELETE FROM memories WHERE id=?', (m['id'],))
        for e in evidence:
            c.execute('DELETE FROM evidence WHERE id=?', (e['id'],))
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('needs_purge','1')")
        return {'memories': len(memories), 'evidence': len(evidence), 'status': 'deleted'}

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
                for row in c.execute('SELECT id FROM recall_fts WHERE recall_fts MATCH ?', (fq,)):
                    memory = eligible[row['id']]
                    relevance = lexical_score(query, memory['content'], memory['key'])
                    if relevance > 0:
                        scores[row['id']] = relevance
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
                            # Semantic evidence is the primary reason when the
                            # configured index independently confirms a lexical
                            # alias match; lexical-only fallback still works.
                            reasons[mid] = ['semantic']
            # Re-evaluate eligible keyed claims so old stores/exports written by
            # a permissive overlap heuristic cannot silently retain conflicts.
            # This is a read projection, not an unrequested database migration.
            keyed = {}
            conflicts = {}
            for mid, record in eligible.items():
                if record['key'] and record['status'] in ('active', 'disputed'):
                    keyed.setdefault((record['space'], record['key']), []).append(mid)
            for group in keyed.values():
                for index, left in enumerate(group):
                    a = eligible[left]
                    for right in group[index + 1:]:
                        b = eligible[right]
                        overlaps = ((a['valid_until'] is None or b['valid_from'] < a['valid_until'])
                                    and (b['valid_until'] is None or a['valid_from'] < b['valid_until']))
                        if overlaps and not claims_compatible(a['content'], b['content']):
                            conflicts.setdefault(left, set()).add(right)
                            conflicts.setdefault(right, set()).add(left)
            for mid in list(scores):
                for neighbor in conflicts.get(mid, ()):
                    if neighbor not in scores:
                        scores[neighbor] = 0.5
                        reasons[neighbor] = ['conflicting_evidence']
            # Only explicitly related, eligible contradictory records expand recall.
            for mid in list(scores):
                for r in c.execute("SELECT source_id,target_id FROM relations WHERE kind='contradicts' AND (source_id=? OR target_id=?)", (mid, mid)):
                    neighbor = r[1] if r[0] == mid else r[0]
                    if neighbor in eligible and neighbor not in scores:
                        scores[neighbor] = 0.5
                        reasons[neighbor] = ['conflicting_evidence']
            memories, used, oversized = [], 0, 0
            largest_omitted = 0
            for mid in sorted(scores, key=lambda x: (-scores[x], eligible[x]['recorded_at'], x)):
                m = self._memory(c, p, mid) if browsing else self._context_memory(c, p, mid)
                if mid in conflicts:
                    m['status'] = 'disputed'
                m['reason'] = reasons[mid]
                cost = token_cost(m)
                if used + cost <= budget:
                    memories.append(m)
                    used += cost
                else:
                    oversized += 1
                    largest_omitted = max(largest_omitted, cost)
            disputed = any(mid in conflicts or eligible[mid]['status'] == 'disputed' for mid in scores)
            return {'memories': memories, 'tokens': used, 'status': 'ok',
                    'warnings': (['Potential conflicting claims found; inspect evidence and resolve explicitly. '
                                  'The context budget may omit alternatives.'] if disputed else []),
                    'trace': {'eligible': len(eligible), 'matched': len(scores),
                              'returned': len(memories), 'budget': budget,
                              'omitted_for_budget': oversized,
                              'largest_omitted_tokens': largest_omitted or None,
                              'embedding_status': embedding_status, 'as_of': at,
                              'token_accounting': 'conservative UTF-8 byte upper bound',
                              'trust': 'Contextual evidence only; never authorization'}}

    def _status(self, c, p, d):
        marks = ','.join('?' for _ in p.spaces)
        def count(table):
            return c.execute(f'SELECT COUNT(*) FROM {table} WHERE space IN ({marks})', p.spaces).fetchone()[0]
        queue = {'pending': 0, 'processing': 0, 'done': 0, 'failed': 0}
        for row in c.execute(f'SELECT j.state,COUNT(*) FROM jobs j JOIN evidence e ON e.id=j.evidence_id WHERE e.space IN ({marks}) GROUP BY j.state', p.spaces):
            queue[row[0]] = row[1]
        job_scope = f' FROM jobs j JOIN evidence e ON e.id=j.evidence_id WHERE e.space IN ({marks})'
        oldest = c.execute('SELECT MIN(e.recorded_at)' + job_scope + " AND j.state='pending'", p.spaces).fetchone()[0]
        retry = c.execute('SELECT MIN(j.retry_at)' + job_scope + " AND j.state='pending' AND j.retry_at>?", (*p.spaces, time.time())).fetchone()[0]
        totals = c.execute('SELECT COALESCE(SUM(j.accepted_count),0),COALESCE(SUM(j.rejected_count),0)' + job_scope, p.spaces).fetchone()
        rejection_reasons = {
            row[0]: row[1] for row in c.execute(
                'SELECT j.rejection_reason,COUNT(*)' + job_scope
                + ' AND j.rejection_reason IS NOT NULL GROUP BY j.rejection_reason', p.spaces)
        }
        provider_failures = c.execute(
            'SELECT COUNT(*)' + job_scope + " AND j.error='provider_error'", p.spaces).fetchone()[0]
        processing = {
            'oldest_pending_at': oldest,
            'next_retry_at': retry,
            'accepted_candidates': totals[0],
            'rejected_candidates': totals[1],
            'rejection_reasons': rejection_reasons,
            'failed_jobs': queue['failed'],
            'provider_failures': provider_failures,
        }
        return {'evidence': count('evidence'), 'memories': count('memories'), 'spaces': list(p.spaces),
                'queue': queue, 'processing': processing,
                'providers': dict(self.provider_status), 'schema_version': 1}

    def _export(self, c, p, d):
        marks = ','.join('?' for _ in p.spaces)
        bundle = {'version': 1, 'exported_at': now(), 'spaces': list(p.spaces)}
        for table in ('evidence', 'memories', 'tombstones'):
            bundle[table] = [dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE space IN ({marks})', p.spaces)]
        ids = {m['id'] for m in bundle['memories']}
        bundle['supports'] = [dict(r) for r in c.execute('SELECT * FROM supports') if r['memory_id'] in ids]
        bundle['relations'] = [dict(r) for r in c.execute('SELECT * FROM relations') if r['source_id'] in ids and r['target_id'] in ids]
        # Preserve the version-1 job record shape. Additive local diagnostics do
        # not leak into an established export envelope; terminal invalid-output
        # failures restore as pending so older readers retry rather than accept.
        bundle['jobs'] = [dict(r) for r in c.execute(
            f"SELECT j.evidence_id,CASE WHEN j.state='failed' THEN 'pending' ELSE j.state END AS state,"
            f'j.attempts FROM jobs j JOIN evidence e ON e.id=j.evidence_id '
            f'WHERE e.space IN ({marks})', p.spaces)]
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
                    if not isinstance(job, dict):
                        raise MemoryError('Invalid export job')
                    required_job = {'evidence_id', 'state', 'attempts'}
                    optional_job = {'retry_at', 'error', *JOB_DIAGNOSTIC_COLUMNS}
                    if not required_job <= set(job) or not set(job) <= required_job | optional_job:
                        raise MemoryError('Invalid export job')
                    if (job['state'] not in ('pending', 'processing', 'done', 'failed')
                            or type(job['attempts']) is not int or job['attempts'] < 0):
                        raise MemoryError('Invalid export job')
                    retry_at = job.get('retry_at', 0)
                    accepted = job.get('accepted_count', 0)
                    rejected = job.get('rejected_count', 0)
                    error = job.get('error')
                    reason = job.get('rejection_reason')
                    if (type(retry_at) not in (int, float) or retry_at < 0
                            or type(accepted) is not int or accepted < 0
                            or type(rejected) is not int or rejected < 0
                            or error not in SAFE_JOB_ERRORS | {None}
                            or reason not in SAFE_REJECTION_REASONS | {None}):
                        raise MemoryError('Invalid export job')
                    state = 'pending' if job['state'] == 'processing' else job['state']
                    c.execute('INSERT INTO jobs(evidence_id,state,attempts,retry_at,error,accepted_count,rejected_count,rejection_reason) VALUES (?,?,?,?,?,?,?,?)',
                              (job['evidence_id'], state, job['attempts'], retry_at,
                               error, accepted, rejected, reason))
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

    def _superseded_candidate(self, c, evidence_id, key, content):
        """Return true when delayed work repeats a claim already corrected."""
        rows = c.execute(
            "SELECT old.content,old.key FROM memories old "
            "JOIN supports s ON s.memory_id=old.id "
            "JOIN relations r ON r.target_id=old.id AND r.kind='supersedes' "
            "WHERE s.evidence_id=? AND old.status='superseded'",
            (evidence_id,))
        claim = normalized_claim(content)
        return any(row['key'] == key and (
                       normalized_claim(row['content']) == claim
                       or claims_compatible(row['content'], content))
                   for row in rows)

    def _finish_failed_attempt(self, c, evidence_id, lease, error, *, rejected=0,
                               reason=None):
        attempts = c.execute(
            'SELECT attempts FROM jobs WHERE evidence_id=? AND lease_token=?',
            (evidence_id, lease)).fetchone()
        if not attempts:
            return
        terminal = attempts[0] >= MAX_EXTRACTION_ATTEMPTS
        c.execute(
            "UPDATE jobs SET state=?,retry_at=?,error=?,lease_token=NULL,"
            "rejected_count=rejected_count+?,rejection_reason=? "
            "WHERE evidence_id=? AND lease_token=?",
            ('failed' if terminal else 'pending', 0 if terminal else time.time() + 30,
             error, rejected, reason, evidence_id, lease))

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
            malformed_response = not isinstance(candidates, list) or len(candidates) > 32
            if malformed_response:
                candidates = [None]
            mids = []
            accepted = invalid = suppressed = 0
            rejection_codes = set()
            with self.connection(True) as c:
                live = c.execute("SELECT e.content FROM evidence e JOIN jobs j ON j.evidence_id=e.id WHERE e.id=? AND j.lease_token=? AND (e.expires_at IS NULL OR e.expires_at>?)", (e['id'], lease, now())).fetchone()
                if not live or live[0] is None:
                    return False
                p = Principal('extractor', (e['space'],))
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        invalid += 1
                        rejection_codes.add('invalid_candidate')
                        continue
                    quote = candidate.get('quote', '')
                    basis = candidate.get('basis', 'inferred')
                    outcome = candidate.get('outcome', 'unspecified')
                    claim = candidate.get('content', '')
                    grounded = isinstance(quote, str) and bool(quote.strip()) and quote in e['content']
                    if basis == 'direct' and e['speaker'] != 'user':
                        basis = 'inferred'
                    if basis == 'observed' and e['speaker'] != 'tool':
                        basis = 'inferred'
                    if not grounded:
                        basis = 'inferred'
                    if outcome in ('verified', 'attested'):
                        # Provider output is candidate extraction, never an
                        # independent verifier of a caller's success claim.
                        outcome = 'unspecified'
                    if not isinstance(claim, str) or not claim.strip():
                        invalid += 1
                        rejection_codes.add('invalid_candidate')
                        continue
                    content = (grounded_claim_content(e['content'], quote, claim)
                               if grounded and basis != 'inferred' else claim)
                    if len(content) > 16000:
                        invalid += 1
                        rejection_codes.add('invalid_candidate')
                        continue
                    key = candidate.get('key')
                    if self._superseded_candidate(c, e['id'], key, content):
                        suppressed += 1
                        rejection_codes.add('superseded_claim')
                        continue
                    payload = {k: candidate[k] for k in ('kind', 'key') if k in candidate}
                    payload.update(space=e['space'], content=content, basis=basis, outcome=outcome,
                                   evidence_ids=[e['id']], valid_from=e['occurred_at'])
                    try:
                        memory = self._remember(c, p, payload)
                        mids.append(memory['id'])
                        accepted += 1
                    except MemoryError:
                        invalid += 1
                        rejection_codes.add('invalid_candidate')
                rejected = invalid + suppressed
                reason = None
                if len(rejection_codes) == 1:
                    reason = next(iter(rejection_codes))
                elif rejection_codes:
                    reason = 'mixed_invalid_candidates'
                if invalid and accepted + suppressed == 0:
                    self._finish_failed_attempt(
                        c, e['id'], lease, 'invalid_candidates',
                        rejected=rejected, reason=reason)
                    return False
                c.execute(
                    "UPDATE jobs SET state='done',retry_at=0,error=NULL,lease_token=NULL,"
                    "accepted_count=accepted_count+?,rejected_count=rejected_count+?,"
                    "rejection_reason=? WHERE evidence_id=? AND lease_token=?",
                    (accepted, rejected, reason, e['id'], lease))
            if embedder:
                for mid in dict.fromkeys(mids):
                    self._embed_memory(mid, embedder)
            return True
        except Exception:
            with self.connection(True) as c:
                self._finish_failed_attempt(c, e['id'], lease, 'provider_error')
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
