"""Version-one replication: current rows, monotone merges, permanent delete fences.

The journal contains references only. Applying remote rows records only real
changes, including locally discovered deletion closure. Echoes converge to no-ops.
"""
import json
from collections import deque
from .store import MemoryError, Principal, index_claims, now, required, stamp, digest

PROTOCOL = 1
TABLES = ('spaces', 'evidence', 'memories', 'supports', 'relations', 'tombstones')
KEYS = {'spaces': ('name',), 'supports': ('memory_id', 'evidence_id'),
        'relations': ('source_id', 'target_id', 'kind')}
STATUS = {'candidate': 0, 'active': 1, 'disputed': 2, 'superseded': 3}
MAX_ROWS = 200
MAX_BYTES = 2_000_000
MAX_PENDING_PER_SPACE = 1000
MAX_PENDING_TOTAL = 10000


def key_columns(table):
    return KEYS.get(table, ('id',))


def key_for(table, row):
    return json.dumps([row[k] for k in key_columns(table)], separators=(',', ':'))


def install(c):
    """Opt-in, additive schema; existing version-one clients remain compatible."""
    c.execute('CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS sync_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, '
              'table_name TEXT NOT NULL, row_key TEXT NOT NULL, space TEXT NOT NULL, '
              'UNIQUE(table_name,row_key))')
    c.execute('CREATE TABLE IF NOT EXISTS sync_pending (table_name TEXT, row_key TEXT, '
              'space TEXT NOT NULL, row_json TEXT NOT NULL, PRIMARY KEY(table_name,row_key))')
    c.execute('CREATE TABLE IF NOT EXISTS sync_alias (id TEXT PRIMARY KEY, canonical TEXT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS sync_pending_space ON sync_pending(space)')
    for table in TABLES:
        cols = [r[1] for r in c.execute(f'PRAGMA table_info({table})')]
        key = 'json_array(' + ','.join('new.' + k for k in key_columns(table)) + ')'
        space = ('new.name' if table == 'spaces' else
                 '(SELECT space FROM memories WHERE id=new.memory_id)' if table == 'supports' else
                 '(SELECT space FROM memories WHERE id=new.source_id)' if table == 'relations' else 'new.space')
        for event in ('INSERT', 'UPDATE'):
            condition = '' if event == 'INSERT' else 'WHEN ' + ' OR '.join(f'old.{k} IS NOT new.{k}' for k in cols)
            c.execute(f'CREATE TRIGGER IF NOT EXISTS sync_{table}_{event.lower()} AFTER {event} ON {table} '
                      f'{condition} BEGIN DELETE FROM sync_log WHERE table_name=\'{table}\' AND row_key={key}; '
                      f'INSERT INTO sync_log(table_name,row_key,space) '
                      f'VALUES (\'{table}\',{key},{space}); END')
        oldkey = key.replace('new.', 'old.')
        c.execute(f'CREATE TRIGGER IF NOT EXISTS sync_{table}_delete AFTER DELETE ON {table} BEGIN '
                  f'DELETE FROM sync_log WHERE table_name=\'{table}\' AND row_key={oldkey}; END')
    c.execute('CREATE INDEX IF NOT EXISTS tombstones_identity ON tombstones(identity)')
    c.execute('CREATE INDEX IF NOT EXISTS evidence_expiry ON evidence(expires_at)')


def enabled(c):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_state'").fetchone())


def state(c, key, default=None):
    row = c.execute('SELECT value FROM sync_state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(c, key, value):
    c.execute('INSERT OR REPLACE INTO sync_state VALUES (?,?)', (key, json.dumps(value)))


def bootstrap(c, spaces):
    for table in TABLES:
        for row in c.execute(f'SELECT * FROM {table}').fetchall():
            row = dict(row)
            scope = row_space(c, table, row)
            if scope in spaces:
                c.execute('INSERT OR REPLACE INTO sync_log(table_name,row_key,space) VALUES (?,?,?)',
                          (table, key_for(table, row), scope))


def row_space(c, table, row):
    if table == 'spaces':
        return row['name']
    if table in ('supports', 'relations'):
        mid = row['memory_id'] if table == 'supports' else row['source_id']
        found = c.execute('SELECT space FROM memories WHERE id=?', (mid,)).fetchone()
        return found[0] if found else None
    return row['space']


def journal_seq(c):
    """Highest journal sequence ever issued. It never decreases, even when entries are coalesced."""
    try:
        row = c.execute("SELECT seq FROM sqlite_sequence WHERE name='sync_log'").fetchone()
    except Exception:
        row = c.execute('SELECT MAX(seq) FROM sync_log').fetchone()
    return (row[0] or 0) if row else 0


def _logged(c, table, row):
    """The row's pending journal sequence number, or None when it has no journal entry."""
    found = c.execute('SELECT seq FROM sync_log WHERE table_name=? AND row_key=?',
                      (table, key_for(table, row))).fetchone()
    return found[0] if found else None


def read_row(c, table, key):
    columns = key_columns(table)
    row = c.execute(f'SELECT * FROM {table} WHERE ' + ' AND '.join(k + '=?' for k in columns), key).fetchone()
    return dict(row) if row else None


def changes(c, spaces, since=0, limit=MAX_ROWS, *, known_through=None, assume_held=False):
    """One page of journal references plus the dependency rows a receiver needs to apply them.

    `known_through` is for a cloud answering a pull. The device has already received every journal
    entry up to its cursor, so a dependency whose entry is at or before that sequence is not sent
    again: a revision of a much-supported memory then costs a few rows, not its whole evidence set.
    A dependency with a newer entry, or none, is always sent.

    `assume_held` is for a device pushing. A row with no pending journal entry has been acknowledged
    by the cloud (or came from it) and has not changed since, so it is not sent again. This holds
    while the cloud keeps its history; `cloud.sync_once` re-bootstraps when it detects a rollback,
    and `cloud resync` does it on demand.
    """
    if type(since) is not int or since < 0 or type(limit) is not int or not 1 <= limit <= MAX_ROWS:
        raise MemoryError('Invalid sync cursor or limit')
    if known_through is not None and (type(known_through) is not int or known_through < 0):
        raise MemoryError('Invalid sync cursor or limit')

    def receiver_has(table, row):
        if known_through is None and not assume_held:
            return False
        seq = _logged(c, table, row)
        if assume_held:
            return seq is None
        return seq is not None and seq <= known_through

    args = tuple(spaces)
    logs = list(c.execute('SELECT * FROM sync_log WHERE seq>? AND space IN (' +
                         ','.join('?' for _ in args) + ') ORDER BY seq LIMIT ?', (since, *args, limit)))
    collected = {}
    queue = deque((log['table_name'], read_row(c, log['table_name'], json.loads(log['row_key'])), False)
                  for log in logs)
    while queue:
        table, row, dependency = queue.popleft()
        if not row:
            continue
        key = (table, key_for(table, row))
        if key in collected:
            continue
        if dependency and receiver_has(table, row):
            continue
        if row_space(c, table, row) not in spaces:
            raise MemoryError('Sync dependency crosses spaces', 409)
        row = dict(row)
        if table == 'evidence' and row['expires_at'] and row['expires_at'] <= now():
            row['content'] = None
        collected[key] = {'table': table, 'row': row}
        if len(collected) > 20000:
            raise MemoryError('Connected provenance group exceeds sync row limit', 413)
        if table == 'memories':
            queue.extend(('supports', dict(e), True) for e in c.execute('SELECT * FROM supports WHERE memory_id=?', (row['id'],)))
            queue.extend(('relations', dict(e), True) for e in c.execute("SELECT * FROM relations WHERE kind='supersedes' AND (source_id=? OR target_id=?)", (row['id'], row['id'])))
        elif table == 'supports':
            queue.append(('memories', read_row(c, 'memories', [row['memory_id']]), True))
            queue.append(('evidence', read_row(c, 'evidence', [row['evidence_id']]), True))
        elif table == 'relations':
            for k in ('source_id', 'target_id'):
                queue.append(('memories', read_row(c, 'memories', [row[k]]), True))
    result = {'protocol': PROTOCOL, 'changes': list(collected.values()),
              'cursor': logs[-1]['seq'] if logs else since, 'has_more': len(logs) == limit}
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_BYTES:
        if limit > 1:
            return changes(c, spaces, since, max(1, limit // 2), known_through=known_through, assume_held=assume_held)
        raise MemoryError('A connected provenance group exceeds the 2 MB sync limit', 413)
    return result


def _insert(c, table, row):
    cols = list(row)
    c.execute(f'INSERT OR IGNORE INTO {table} ({",".join(cols)}) VALUES ({",".join("?" for _ in cols)})',
              [row[k] for k in cols])


def _tomb(c, item_id):
    return c.execute('SELECT * FROM tombstones WHERE id=?', (item_id,)).fetchone()


def _erase(store, c, item_id, space):
    table = 'evidence' if item_id.startswith('ev_') else 'memories'
    if read_row(c, table, [item_id]):
        memories, evidence, _ = store._forget_plan(c, Principal('sync', (space,)), item_id)
        for row in evidence + memories:
            _insert(c, 'tombstones', {'id': row['id'], 'identity': row.get('identity'), 'space': space})
        for row in memories:
            c.execute('DELETE FROM memories WHERE id=?', (row['id'],))
        for row in evidence:
            c.execute('DELETE FROM evidence WHERE id=?', (row['id'],))
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('needs_purge','1')")


def _settle(c, rows):
    """Forget journal entries for rows that now equal what the sender has.

    A device that pulls a row must not push it straight back: that echo re-uploads the whole vault
    when a device joins. Only rows still differing from the sender's copy (a local edit that merged
    ahead of it) and rows the sender never mentioned (locally discovered deletions) stay queued.
    """
    for table, row in rows:
        if table == 'spaces':
            key = row
        elif table in ('supports', 'relations'):
            key = dict(row)
            if table == 'supports':
                alias = c.execute('SELECT canonical FROM sync_alias WHERE id=?', (key['evidence_id'],)).fetchone()
                if alias:
                    key['evidence_id'] = alias[0]
        else:
            key = row
        stored = read_row(c, table, [key[k] for k in key_columns(table)])
        if stored is None:
            continue
        if table in ('memories', 'evidence', 'tombstones') and stored != row:
            continue
        c.execute('DELETE FROM sync_log WHERE table_name=? AND row_key=?', (table, key_for(table, stored)))


def apply(store, c, envelope, spaces, *, quiet=False):
    """Merge a page into this replica. `quiet` marks a device applying what the cloud sent it."""
    if not isinstance(envelope, dict) or envelope.get('protocol') != PROTOCOL:
        raise MemoryError('Incompatible sync protocol', 409)
    incoming = envelope.get('changes')
    if not isinstance(incoming, list) or len(incoming) > 20000:
        raise MemoryError('Invalid sync changes')
    columns = {t: {r[1] for r in c.execute(f'PRAGMA table_info({t})')} for t in TABLES}
    rows = []
    for item in incoming:
        if not isinstance(item, dict) or item.get('table') not in TABLES or not isinstance(item.get('row'), dict):
            raise MemoryError('Invalid sync row')
        table, row = item['table'], dict(item['row'])
        if set(row) != columns[table]:
            raise MemoryError('Invalid sync columns')
        for col in key_columns(table):
            required(row, col, 500)
        for value in row.values():
            if value is not None and type(value) not in (str, int):
                raise MemoryError('Invalid sync field type')
        if 'id' in row and not row['id'].startswith(('mem_', 'ev_')):
            raise MemoryError('Invalid sync identifier')
        if table in ('memories', 'evidence') and not row['id'].startswith('mem_' if table == 'memories' else 'ev_'):
            raise MemoryError('Invalid sync identifier')
        if table in ('supports', 'relations'):
            endpoints = (('memory_id', 'mem_'), ('evidence_id', 'ev_')) if table == 'supports' else (
                ('source_id', 'mem_'), ('target_id', 'mem_'))
            for col, prefix in endpoints:
                if not required(row, col, 200).startswith(prefix):
                    raise MemoryError('Invalid sync endpoint identifier')
            if table == 'relations' and row['kind'] not in ('contradicts', 'supersedes'):
                raise MemoryError('Invalid sync relation kind')
        if table not in ('supports', 'relations'):
            space = row_space(c, table, row)
            if space not in spaces:
                raise MemoryError('Space is not authorized', 403)
            c.execute('INSERT OR IGNORE INTO spaces VALUES (?)', (space,))
        if table == 'memories':
            if row['status'] not in STATUS or type(row['revision']) is not int or row['revision'] < 1:
                raise MemoryError('Invalid synced memory')
            required(row, 'content', 16000)
            if (row['kind'] not in ('fact', 'preference', 'decision', 'episode', 'procedure', 'task') or
                    row['basis'] not in ('direct', 'observed', 'inferred') or
                    row['outcome'] not in ('unspecified', 'proposed', 'attempted', 'attested', 'verified', 'failed')):
                raise MemoryError('Invalid memory labels')
            if row['key'] is not None:
                required(row, 'key', 500)
            required(row, 'recorded_at', 100)
            required(row, 'valid_from', 100)
            for col in ('recorded_at', 'valid_from', 'valid_until'):
                if row[col] is not None:
                    row[col] = stamp(row[col])
        if table == 'memories' and row['valid_until'] and row['valid_until'] <= row['valid_from']:
            raise MemoryError('Invalid memory validity interval')
        if table == 'evidence':
            for col in ('source', 'source_event_id', 'session_id', 'fingerprint', 'identity', 'occurred_at', 'recorded_at'):
                required(row, col, 500)
            if row['identity'] != digest(json.dumps([row['space'], row['source'], row['source_event_id']])):
                raise MemoryError('Invalid evidence identity')
            if row['speaker'] not in ('user', 'assistant', 'tool', 'document'):
                raise MemoryError('Invalid evidence speaker')
            if row['content'] is not None:
                required(row, 'content', 100000)
            for col in ('occurred_at', 'recorded_at', 'expires_at'):
                if row[col] is not None:
                    row[col] = stamp(row[col])
            if row['expires_at'] and row['expires_at'] <= now():
                row['content'] = None
        rows.append((table, row))
    for table, row in sorted(rows, key=lambda pair: 0 if pair[0] == 'tombstones' else 1):
        if table == 'spaces':
            continue
        if table == 'tombstones':
            existing = _tomb(c, row['id'])
            if existing:
                if existing['space'] != row['space'] or (existing['identity'] and row['identity'] and existing['identity'] != row['identity']):
                    raise MemoryError('Conflicting tombstone', 409)
                if row['identity'] and not existing['identity']:
                    c.execute('UPDATE tombstones SET identity=? WHERE id=?', (row['identity'], row['id']))
            _insert(c, table, row)
            _erase(store, c, row['id'], row['space'])
            if row['identity']:
                for e in c.execute('SELECT id FROM evidence WHERE identity=?', (row['identity'],)).fetchall():
                    _erase(store, c, e[0], row['space'])
        elif table in ('memories', 'evidence'):
            tomb = _tomb(c, row['id'])
            if tomb:
                if tomb['space'] != row['space']:
                    raise MemoryError('Sync identifier crosses spaces', 403)
                if table == 'evidence' and not tomb['identity']:
                    c.execute('UPDATE tombstones SET identity=? WHERE id=?', (row['identity'], row['id']))
                continue
            if table == 'evidence' and c.execute('SELECT 1 FROM tombstones WHERE identity=?', (row['identity'],)).fetchone():
                _insert(c, 'tombstones', {'id': row['id'], 'identity': row['identity'], 'space': row['space']})
                continue
            old = read_row(c, table, [row['id']])
            if table == 'evidence' and old is None:
                duplicate = c.execute('SELECT * FROM evidence WHERE identity=?', (row['identity'],)).fetchone()
                if duplicate:
                    old = dict(duplicate)
                    if old['fingerprint'] != row['fingerprint'] or old['space'] != row['space']:
                        raise MemoryError('Conflicting evidence identity', 409)
                    c.execute('INSERT OR REPLACE INTO sync_alias VALUES (?,?)', (row['id'], old['id']))
                    row['id'] = old['id']
            if old:
                mutable = {'status', 'revision', 'valid_until'} if table == 'memories' else {'content', 'recorded_at', 'occurred_at'}
                if any(old[k] != row[k] for k in row if k not in mutable):
                    raise MemoryError('Immutable sync fields differ', 409)
                # Write only real changes: replayed echoes then cost no row writes on the cloud.
                if table == 'memories':
                    status = max((old['status'], row['status']), key=STATUS.get)
                    ends = [x for x in (old['valid_until'], row['valid_until']) if x]
                    merged = (status, max(old['revision'], row['revision']), min(ends) if ends else None)
                    if merged != (old['status'], old['revision'], old['valid_until']):
                        c.execute('UPDATE memories SET status=?,revision=?,valid_until=? WHERE id=?',
                                  (*merged, row['id']))
                elif old['content'] is None or row['content'] is None:
                    if old['content'] is not None:
                        c.execute('UPDATE evidence SET content=NULL WHERE id=?', (row['id'],))
                elif old['content'] != row['content']:
                    raise MemoryError('Immutable evidence content differs', 409)
                if table == 'evidence':
                    merged = (min(old['recorded_at'], row['recorded_at']), min(old['occurred_at'], row['occurred_at']))
                    if merged != (old['recorded_at'], old['occurred_at']):
                        c.execute('UPDATE evidence SET recorded_at=?,occurred_at=? WHERE id=?', (*merged, row['id']))
            else:
                _insert(c, table, row)
        else:
            # Edges can arrive ahead of endpoints from older/offline writers.
            scope = row_space(c, table, row)
            if scope is None:
                scope = next((r['space'] for t, r in rows if t == 'memories' and
                              r['id'] == row.get('memory_id', row.get('source_id'))), None)
            if scope not in spaces:
                raise MemoryError('Missing or unauthorized sync endpoint', 403)
            c.execute('INSERT OR REPLACE INTO sync_pending VALUES (?,?,?,?)',
                      (table, key_for(table, row), scope, json.dumps(row)))
    for pending in c.execute('SELECT * FROM sync_pending WHERE space IN (' + ','.join('?' for _ in spaces) + ')', tuple(spaces)).fetchall():
        table, row = pending['table_name'], json.loads(pending['row_json'])
        if table == 'supports':
            alias = c.execute('SELECT canonical FROM sync_alias WHERE id=?', (row['evidence_id'],)).fetchone()
            if alias:
                row['evidence_id'] = alias[0]
            endpoints = [('memories', row['memory_id']), ('evidence', row['evidence_id'])]
        else:
            endpoints = [('memories', row['source_id']), ('memories', row['target_id'])]
        found = [read_row(c, t, [k]) for t, k in endpoints]
        tombs = [_tomb(c, k) for _, k in endpoints]
        scopes = {r['space'] for r in found + tombs if r}
        if len(scopes) > 1 or not scopes <= set(spaces):
            raise MemoryError('Sync dependency crosses spaces', 403)
        dead = any(tombs)
        if dead:
            # Forget all memory endpoints, and correction lineage, even if
            # a tombstone reached this replica before the edge did.
            for (t, k), record in zip(endpoints, found):
                if table == 'supports' or row['kind'] == 'supersedes':
                    space = next(iter(scopes))
                    _erase(store, c, k, space)
                    _insert(c, 'tombstones', {'id': k, 'identity': record.get('identity') if record else None, 'space': space})
        elif all(found):
            _insert(c, table, row)
        else:
            continue
        c.execute('DELETE FROM sync_pending WHERE table_name=? AND row_key=?',
                  (table, pending['row_key']))
    # Bound unresolved references without discarding legitimate offline links.
    # The caller's transaction rolls back an oversized batch in its entirety.
    counts = [r[0] for r in c.execute('SELECT COUNT(*) FROM sync_pending GROUP BY space')]
    if sum(counts) > MAX_PENDING_TOTAL or any(n > MAX_PENDING_PER_SPACE for n in counts):
        raise MemoryError('Pending sync references exceed capacity; send missing endpoints first', 413)
    index_claims(c)
    if quiet:
        _settle(c, rows)
    return {'status': 'applied'}
