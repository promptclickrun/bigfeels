"""Read-only Mnemosyne v1.3 export translation and atomic local import.

The source contract is mnemosyne-memory 3.15.1's export_to_file, not its
internal SQLite schema. No Mnemosyne modules, models, or host profiles load.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import MemoryError, required


MAX_SOURCE_BYTES = 64_000_000
SECTIONS = ('working_memory', 'episodic_memory', 'legacy_memories', 'canonical_facts')
EXCLUDED = ('episodic_embeddings', 'legacy_embeddings', 'scratchpad',
            'consolidation_log', 'sync_events', 'triples', 'annotations')
TABLES = ('evidence', 'memories', 'supports', 'relations')


@dataclass
class ImportPlan:
    bundle: dict
    receipt: dict

    def summary(self, status):
        return dict(status=status, source_records=len(self.receipt['records']),
                    verified_records=0 if status == 'dry_run' else len(self.receipt['records']),
                    source_counts=self.receipt['source_counts'],
                    excluded_counts=self.receipt['excluded_counts'],
                    source_sha256=self.receipt['source_sha256'],
                    target_sha256=self.receipt['target_sha256'],
                    spaces=self.receipt['spaces'])


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise MemoryError('Duplicate JSON key in Mnemosyne export')
        value[key] = item
    return value


def _constant(_value):
    raise MemoryError('Nonfinite number in Mnemosyne export')


def _zone(value):
    if value is None:
        return None
    if value == 'UTC':
        return timezone.utc
    try:
        if len(value) == 6 and value[0] in '+-' and value[3] == ':':
            if (not value[1:3].isascii() or not value[4:].isascii()
                    or not value[1:3].isdigit() or not value[4:].isdigit()
                    or int(value[1:3]) > 23 or int(value[4:]) > 59):
                raise ValueError
            return datetime.fromisoformat('2000-01-01T00:00:00' + value).tzinfo
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise MemoryError('Unknown source timezone; use UTC, an IANA zone, or a fixed offset') from None


def _stamp(value, zone):
    if not isinstance(value, str) or not value:
        raise MemoryError('Mnemosyne records require source timestamps')
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise MemoryError('Invalid Mnemosyne timestamp') from None
    if at.tzinfo is None:
        if zone is None:
            raise MemoryError('Naive source timestamps require --naive-timezone')
        local = at.replace(tzinfo=zone)
        if (local.utcoffset() != at.replace(tzinfo=zone, fold=1).utcoffset()
                or local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != at):
            raise MemoryError('Ambiguous or nonexistent source timestamp; supply an export with explicit offsets')
        at = local
    try:
        return at.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except (ValueError, OverflowError):
        raise MemoryError('Invalid Mnemosyne timestamp') from None


def _ordered(bundle):
    return {table: sorted(bundle[table], key=_json) for table in TABLES}


def plan_import(source, *, source_id, space, naive_timezone=None):
    """Validate every source row and construct a deterministic, content-free receipt."""
    required({'source_id': source_id}, 'source_id', 100)
    required({'space': space}, 'space', 200)
    zone = _zone(naive_timezone)
    with Path(source).open('rb') as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise MemoryError('Mnemosyne export exceeds 64 MB limit')
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeDecodeError):
        raise MemoryError('Mnemosyne export is not valid UTF-8 JSON') from None
    if (not isinstance(data, dict) or not isinstance(data.get('mnemosyne_export'), dict)
            or data['mnemosyne_export'].get('version') != '1.3'):
        raise MemoryError('Expected a Mnemosyne v1.3 export')
    if set(data) - {'mnemosyne_export', *SECTIONS, *EXCLUDED}:
        raise MemoryError('Unsupported Mnemosyne export section')
    if any(not isinstance(data.get(section), list) for section in SECTIONS):
        raise MemoryError('Mnemosyne v1.3 export is missing a memory section')
    if any(not isinstance(data.get(section, []), list) for section in EXCLUDED):
        raise MemoryError('Invalid excluded Mnemosyne section')

    bundle = {table: [] for table in TABLES}
    records, source_rows, canonical_slots = {}, {}, {}
    prefix = 'mnemosyne:' + _hash(source_id)[:16]
    receipt = dict(version=1, source_format='mnemosyne-1.3', source_id=source_id,
                   source_sha256=hashlib.sha256(raw).hexdigest(),
                   source_counts={key: len(data[key]) for key in SECTIONS},
                   excluded_counts={key: len(data.get(key, [])) for key in EXCLUDED},
                   naive_timezone=naive_timezone, records=[])
    for table in SECTIONS:
        for row in data[table]:
            if not isinstance(row, dict):
                raise MemoryError('Invalid Mnemosyne memory row')
            old_id = row.get('id')
            canonical = table == 'canonical_facts'
            if ((canonical and (type(old_id) is not int or old_id < 1))
                    or (not canonical and (not isinstance(old_id, str) or not old_id or len(old_id) > 500))):
                raise MemoryError('Invalid Mnemosyne source ID')
            identity = (table, str(old_id))
            if identity in records:
                raise MemoryError('Duplicate Mnemosyne source ID within a table')
            content = required({'content': row.get('body' if canonical else 'content')}, 'content', 16000)
            if canonical:
                owner = required(row, 'owner_id', 200)
                category = required(row, 'category', 200)
                name = required(row, 'name', 200)
                scope = {'owner_id': owner}
                target_space = prefix + ':owner:' + _hash(owner)[:24]
                session = 'canonical:' + owner
                start = _stamp(row.get('valid_from'), zone)
                revision = row.get('version')
                if type(revision) is not int or revision < 1:
                    raise MemoryError('Invalid canonical version')
                key = 'mnemosyne:' + _hash(_json([source_id, owner, category, name]))
                canonical_slots.setdefault((owner, category, name), []).append(identity)
            else:
                session = row.get('session_id')
                if session is None:
                    session = ''
                if not isinstance(session, str) or len(session) > 500:
                    raise MemoryError('Invalid Mnemosyne session ID')
                source_scope = row.get('scope') or 'session'
                if source_scope not in ('global', 'session'):
                    raise MemoryError('Unsupported Mnemosyne scope')
                if source_scope == 'session' and not session:
                    raise MemoryError('Session-scoped memory requires a session ID')
                scope = {'scope': source_scope, 'session_id': session}
                target_space = space if source_scope == 'global' else prefix + ':session:' + _hash(session)[:24]
                start = _stamp(row.get('timestamp') or row.get('created_at'), zone)
                revision, key = 1, None
            end = _stamp(row['valid_until'], zone) if row.get('valid_until') is not None else None
            if end is not None and end < start:
                raise MemoryError('Mnemosyne validity ends before it starts')
            recorded = _stamp(row.get('created_at') or row.get('timestamp') or row.get('valid_from'), zone)
            token = _hash(_json([source_id, table, str(old_id)]))
            mid, eid = 'mem_' + token, 'ev_' + token
            # Preserve the source row exactly, including original temporal and
            # trust signals. Importing is not an independent verification.
            evidence_content = _json({'source_id': source_id, 'table': table, 'row': row,
                                      'export': data['mnemosyne_export']})
            if len(evidence_content) > 100000:
                raise MemoryError('Mnemosyne source row exceeds evidence limit')
            inferred = row.get('veracity') == 'inferred'
            memory = dict(id=mid, space=target_space, content=content,
                          kind='episode' if table == 'episodic_memory' else 'fact',
                          basis='inferred' if inferred else 'direct', outcome='unspecified', key=key,
                          status='superseded' if canonical and end else ('candidate' if inferred else 'active'),
                          revision=revision, recorded_at=recorded, valid_from=start, valid_until=end)
            evidence = dict(id=eid, space=target_space, identity=_hash(_json([source_id, table, str(old_id), target_space])),
                            source='mnemosyne:' + source_id + ':' + table, source_event_id=str(old_id),
                            session_id=session, speaker='import', content=evidence_content,
                            fingerprint=_hash(evidence_content), occurred_at=start,
                            recorded_at=recorded, expires_at=None)
            bundle['memories'].append(memory)
            bundle['evidence'].append(evidence)
            bundle['supports'].append(dict(memory_id=mid, evidence_id=eid))
            records[identity], source_rows[identity] = memory, row
            receipt['records'].append(dict(table=table, old_id=old_id, memory_id=mid, evidence_id=eid,
                                           space=target_space, source_scope=scope,
                                           row_sha256=_hash(_json(row)), content_sha256=_hash(content)))

    successors = {}

    def link(old, new, *, canonical=False):
        if old['id'] == new['id'] or old['space'] != new['space']:
            raise MemoryError('Supersession cannot reference itself or cross scopes')
        if new['valid_from'] < old['valid_from']:
            raise MemoryError('Supersession precedes the original memory')
        # BeamMemory.invalidate stamps the end when invalidation happens, often
        # after the replacement was created. Preserve that source interval.
        # Canonical upserts, in contrast, share one boundary for both versions.
        if canonical and old['valid_until'] is not None and old['valid_until'] > new['valid_from']:
            raise MemoryError('Superseded validity overlaps its replacement')
        # Candidate exclusion also applies to historical recall: supersession
        # is lineage, not new evidence supporting an inferred source claim.
        if old['status'] != 'candidate':
            old['status'] = 'superseded'
        if old['valid_until'] is None:
            old['valid_until'] = new['valid_from']
        successors[old['id']] = new['id']
        bundle['relations'].append(dict(source_id=new['id'], target_id=old['id'], kind='supersedes'))

    by_id = {}
    for identity in records:
        if identity[0] != 'canonical_facts':
            by_id.setdefault(identity[1], []).append(identity)
    for identity, row in source_rows.items():
        target = row.get('superseded_by')
        if target is None or target == '':
            continue
        if not isinstance(target, str):
            raise MemoryError('Invalid supersession source ID')
        candidates = [(identity[0], target)] if (identity[0], target) in records else by_id.get(target, [])
        if len(candidates) != 1:
            raise MemoryError('Missing or ambiguous supersession target')
        link(records[identity], records[candidates[0]])
    for identities in canonical_slots.values():
        history = sorted((records[i] for i in identities), key=lambda item: item['revision'])
        for old, new in zip(history, history[1:]):
            if old['revision'] == new['revision'] or old['valid_until'] is None:
                raise MemoryError('Inconsistent canonical history')
            link(old, new, canonical=True)
    visited = set()
    for origin in successors:
        chain, cursor = set(), origin
        while cursor in successors and cursor not in visited:
            if cursor in chain:
                raise MemoryError('Cyclic Mnemosyne supersession')
            chain.add(cursor)
            cursor = successors[cursor]
        visited.update(chain)

    spaces = sorted({memory['space'] for memory in bundle['memories']} | {space})
    receipt['spaces'] = spaces
    receipt['records'].sort(key=lambda r: (r['table'], str(r['old_id'])))
    receipt['target_sha256'] = _hash(_json(_ordered(bundle)))
    return ImportPlan(bundle, receipt)


def apply_import(store, plan):
    """Apply in one transaction, or verify an identical prior import without writes."""
    try:
        with store.connection(True) as connection:
            existing = {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table}')]
                        for table in TABLES}
            extra = sum(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                        for table in ('tombstones', 'jobs', 'vectors'))
            if any(existing.values()) or extra:
                if extra or _ordered(existing) != _ordered(plan.bundle):
                    raise MemoryError('Import requires an empty destination or an unchanged identical import', 409)
                status = 'already_imported'
            else:
                for space in plan.receipt['spaces']:
                    connection.execute('INSERT OR IGNORE INTO spaces VALUES (?)', (space,))
                for table in TABLES:
                    for row in plan.bundle[table]:
                        columns = ','.join(row)
                        placeholders = ','.join('?' for _ in row)
                        connection.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(row.values()))
                status = 'imported'
            imported = {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table}')]
                        for table in TABLES}
            if _ordered(imported) != _ordered(plan.bundle):
                raise MemoryError('Import verification failed; transaction rolled back', 409)
    except sqlite3.Error:
        raise MemoryError('Import database write failed; transaction rolled back', 409) from None
    return plan.summary(status)
