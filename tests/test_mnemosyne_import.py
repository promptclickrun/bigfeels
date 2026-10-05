"""Synthetic v1.3 migration fixtures; no private stores or host profiles."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bigfeels_mem.store import Principal, Store


def fixture():
    def row(identifier, content, **extra):
        return dict(id=identifier, content=content, source='conversation',
                    timestamp='2026-01-01T10:00:00Z', session_id='session-A',
                    importance=0.5, metadata_json='{"source_id":"original-event"}',
                    created_at='2026-01-01T10:01:00Z', **extra)
    def canonical(identifier, body, version, start, end):
        return dict(id=identifier, owner_id='persona-A', category='identity', name='tea',
                    body=body, source='explicit', confidence=1.0, version=version,
                    valid_from=start, valid_until=end, created_at=start)
    return {
        'mnemosyne_export': {'version': '1.3', 'export_date': '2026-03-01T00:00:00Z',
                             'source_db': '/synthetic/mnemosyne.db', 'device_id': 'fixture'},
        'working_memory': [
            row('same-id', 'Use SQLite for the orchid project.', scope='global',
                valid_until=None, superseded_by=None, veracity='stated'),
            row('session-only', 'Session secret: jasmine.', scope='session',
                valid_until=None, superseded_by=None, veracity='stated'),
        ],
        'episodic_memory': [row('episode', 'Orchid release retrospective.', rowid=1,
                                summary_of='same-id', scope='global',
                                valid_until=None, superseded_by=None)],
        'legacy_memories': [row('same-id', 'Legacy orchid note.')],
        'canonical_facts': [
            canonical(1, 'I prefer mint tea.', 1, '2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z'),
            canonical(2, 'I prefer jasmine tea.', 2, '2026-02-01T00:00:00Z', None),
        ],
        'episodic_embeddings': [{'embedding': [1, 2]}],
        'legacy_embeddings': [{'embedding_json': '[3, 4]'}],
        'scratchpad': [{'content': 'Excluded scratchpad marker.'}],
        'consolidation_log': [{'summary_preview': 'Excluded audit marker.'}],
        'sync_events': [{'payload': 'Excluded sync marker.'}],
        'triples': [{'subject': 'Excluded derived marker.'}],
        'annotations': [{'value': 'Excluded annotation marker.'}],
    }


class MnemosyneImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'export.json'
        self.data = self.root / 'Imported Data'
        self.receipt = self.root / 'private' / 'receipt.json'
        self.payload = fixture()
        self.write_source()

    def write_source(self):
        self.source.write_text(json.dumps(self.payload, ensure_ascii=False), encoding='utf-8')

    def run_import(self, *extra):
        return subprocess.run([
            sys.executable, '-m', 'bigfeels_mem.cli', '--data-dir', str(self.data),
            'import-mnemosyne', str(self.source), '--source-id', 'default-profile',
            '--space', 'owner', '--receipt', str(self.receipt), *extra,
        ], text=True, capture_output=True, timeout=10)

    def successful(self, *extra):
        result = self.run_import(*extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def records(self):
        receipt = json.loads(self.receipt.read_text(encoding='utf-8'))
        return receipt, {(r['table'], str(r['old_id'])): r for r in receipt['records']}

    def test_import_preserves_content_provenance_history_and_source_bytes(self):
        before, modified = self.source.read_bytes(), self.source.stat().st_mtime_ns
        result = self.successful()
        self.assertEqual(result['status'], 'imported')
        self.assertEqual(result['source_records'], 6)
        self.assertEqual(result['verified_records'], 6)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.source.stat().st_mtime_ns, modified)
        receipt, records = self.records()
        self.assertEqual(receipt['source_sha256'], hashlib.sha256(before).hexdigest())
        self.assertNotIn('Use SQLite', self.receipt.read_text())
        self.assertEqual(len({r['memory_id'] for r in records.values()}), 6)
        store = Store(self.data / 'memory.sqlite')
        principal = Principal('test', tuple(receipt['spaces']))
        for table in ('working_memory', 'episodic_memory', 'legacy_memories', 'canonical_facts'):
            for row in self.payload[table]:
                mapping = records[table, str(row['id'])]
                memory = store.dispatch(principal, 'inspect', {'id': mapping['memory_id']})
                self.assertEqual(memory['content'], row.get('content', row.get('body')))
                self.assertEqual(memory['outcome'], 'unspecified')
                provenance = json.loads(memory['evidence'][0]['content'])
                self.assertEqual(provenance['row'], row)
                self.assertEqual(provenance['table'], table)
                self.assertEqual(memory['evidence'][0]['source_event_id'], str(row['id']))
        old = store.dispatch(principal, 'inspect', {'id': records['canonical_facts', '1']['memory_id']})
        new = store.dispatch(principal, 'inspect', {'id': records['canonical_facts', '2']['memory_id']})
        self.assertEqual((old['status'], old['revision'], new['revision']), ('superseded', 1, 2))
        self.assertIn({'source_id': new['id'], 'target_id': old['id'], 'kind': 'supersedes'}, old['relations'])
        self.assertEqual(old['valid_until'], '2026-02-01T00:00:00.000000Z')
        self.assertEqual(old['recorded_at'], '2026-01-01T00:00:00.000000Z')
        if os.name != 'nt':
            self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)

    def test_scope_isolation_history_recall_and_excluded_sections(self):
        self.successful()
        receipt, records = self.records()
        store = Store(self.data / 'memory.sqlite')
        owner = Principal('test', ('owner',))
        self.assertEqual(store.dispatch(owner, 'context', {'query': 'jasmine'})['memories'], [])
        canonical_space = records['canonical_facts', '2']['space']
        self.assertNotEqual(canonical_space, 'owner')
        self.assertNotEqual(records['working_memory', 'session-only']['space'], canonical_space)
        p = Principal('test', (canonical_space,))
        current = store.dispatch(p, 'context', {'query': 'tea'})['memories']
        historic = store.dispatch(p, 'context', {'query': 'tea', 'as_of': '2026-01-15T00:00:00Z'})['memories']
        self.assertEqual([m['content'] for m in current], ['I prefer jasmine tea.'])
        self.assertEqual([m['content'] for m in historic], ['I prefer mint tea.'])
        exported = store.dispatch(Principal('test', tuple(receipt['spaces'])), 'export', {})
        self.assertNotIn('Excluded', json.dumps(exported))
        self.assertEqual(len(exported['memories']), 6)
        self.assertEqual(exported['jobs'], [])
        with store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM vectors').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM credentials').fetchone()[0], 0)

    def test_replay_verifies_and_recovers_a_missing_receipt_without_duplication(self):
        first = self.successful()
        receipt_bytes = self.receipt.read_bytes()
        again = self.successful()
        self.assertEqual(again['status'], 'already_imported')
        self.assertEqual(again['target_sha256'], first['target_sha256'])
        self.assertEqual(self.receipt.read_bytes(), receipt_bytes)
        self.receipt.unlink()
        self.assertEqual(self.successful()['status'], 'already_imported')
        self.assertEqual(self.receipt.read_bytes(), receipt_bytes)

    def test_changed_or_deleted_destination_is_never_overwritten_or_resurrected(self):
        self.successful()
        _, records = self.records()
        store = Store(self.data / 'memory.sqlite')
        p = Principal('test', ('owner',))
        mid = records['working_memory', 'same-id']['memory_id']
        def snapshot():
            bundle = store.dispatch(p, 'export', {})
            return {key: value for key, value in bundle.items() if key != 'exported_at'}
        store.dispatch(p, 'correct', {'id': mid, 'revision': 1, 'content': 'Use PostgreSQL instead.'})
        before = snapshot()
        self.assertNotEqual(self.run_import().returncode, 0)
        self.assertEqual(snapshot(), before)
        plan = store.dispatch(p, 'forget_preview', {'id': mid})
        store.dispatch(p, 'forget', {'id': mid, 'plan_token': plan['plan_token']})
        before = snapshot()
        self.assertNotEqual(self.run_import().returncode, 0)
        self.assertEqual(snapshot(), before)

    def test_dry_run_and_bad_inputs_create_no_destination(self):
        result = self.successful('--dry-run')
        self.assertEqual(result['status'], 'dry_run')
        self.assertFalse(self.data.exists())
        self.assertFalse(self.receipt.exists())
        cases = []
        wrong_version = fixture(); wrong_version['mnemosyne_export']['version'] = '1.2'; cases.append(wrong_version)
        duplicate = fixture(); duplicate['working_memory'].append(copy.deepcopy(duplicate['working_memory'][0])); cases.append(duplicate)
        dangling = fixture(); dangling['working_memory'][0]['superseded_by'] = 'missing'; cases.append(dangling)
        crossing = fixture(); crossing['working_memory'][0]['superseded_by'] = 'session-only'; cases.append(crossing)
        missing = fixture(); del missing['canonical_facts']; cases.append(missing)
        unknown = fixture(); unknown['working_memory'][0]['scope'] = 'private-unknown'; cases.append(unknown)
        for payload in cases:
            self.payload = payload; self.write_source()
            with self.subTest(payload=payload['mnemosyne_export']['version']):
                self.assertNotEqual(self.run_import().returncode, 0)
                self.assertFalse(self.data.exists())
                self.assertFalse(self.receipt.exists())

    def test_naive_timestamps_require_explicit_source_timezone(self):
        self.payload['working_memory'][0]['timestamp'] = '2026-01-01T10:00:00'
        self.write_source()
        self.assertNotEqual(self.run_import().returncode, 0)
        self.assertFalse(self.data.exists())
        self.successful('--naive-timezone', '+02:00')
        _, records = self.records()
        memory = Store(self.data / 'memory.sqlite').dispatch(Principal('test', ('owner',)), 'inspect',
            {'id': records['working_memory', 'same-id']['memory_id']})
        self.assertEqual(memory['valid_from'], '2026-01-01T08:00:00.000000Z')
        self.assertEqual(json.loads(memory['evidence'][0]['content'])['row']['timestamp'], '2026-01-01T10:00:00')

    def test_receipt_cannot_overwrite_source_or_unrelated_file(self):
        self.receipt = self.source
        before = self.source.read_bytes()
        self.assertNotEqual(self.run_import().returncode, 0)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(self.data.exists())
        self.receipt = self.root / 'unrelated.json'
        self.receipt.write_text('{"important":true}', encoding='utf-8')
        self.assertNotEqual(self.run_import().returncode, 0)
        self.assertEqual(self.receipt.read_text(), '{"important":true}')
        self.assertFalse(self.data.exists())

    def test_database_failure_rolls_back_every_imported_record(self):
        store = Store(self.data / 'memory.sqlite')
        with store.connection(True) as c:
            c.execute("CREATE TRIGGER fail_import BEFORE INSERT ON relations BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        self.assertNotEqual(self.run_import().returncode, 0)
        with store.connection() as c:
            for table in ('memories', 'evidence', 'supports', 'relations', 'memory_fts'):
                self.assertEqual(c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertFalse(self.receipt.exists())

    def test_working_supersession_retains_history_and_deletion_closure(self):
        old = self.payload['working_memory'][0]
        new = copy.deepcopy(old)
        new.update(id='replacement', content='Use PostgreSQL for the orchid project.',
                   timestamp='2026-02-01T10:00:00Z', created_at='2026-02-01T10:01:00Z')
        old['superseded_by'] = 'replacement'
        self.payload['working_memory'].append(new)
        self.write_source()
        self.successful()
        _, records = self.records()
        store = Store(self.data / 'memory.sqlite')
        p = Principal('test', ('owner',))
        old_id = records['working_memory', 'same-id']['memory_id']
        new_id = records['working_memory', 'replacement']['memory_id']
        current = store.dispatch(p, 'context', {'query': 'PostgreSQL'})['memories']
        self.assertEqual([r['id'] for r in current], [new_id])
        preview = store.dispatch(p, 'forget_preview', {'id': old_id})
        self.assertEqual({r['id'] for r in preview['memories']}, {old_id, new_id})

    def test_invalid_encoding_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        raw = self.source.read_text(encoding='utf-8')
        for content in (raw.encode('utf-16'), raw.replace('"version": "1.3"', '"version":"1.2","version":"1.3"').encode(),
                        raw.replace('"importance": 0.5', '"importance": NaN').encode()):
            self.source.write_bytes(content)
            self.assertNotEqual(self.run_import().returncode, 0)
            self.assertFalse(self.data.exists())

    def test_invalid_timezone_offset_is_not_silently_normalized(self):
        self.assertNotEqual(self.run_import('--naive-timezone', '+02:99').returncode, 0)
        self.assertFalse(self.data.exists())

    def test_source_and_receipt_cannot_collide_with_sqlite_sidecars(self):
        for role in ('source', 'receipt', 'source_alias'):
            for suffix in ('-wal', '-shm', '-journal'):
                with self.subTest(role=role, suffix=suffix):
                    self.data = self.root / (role + suffix)
                    self.source = self.root / 'export.json'
                    self.receipt = self.root / 'receipt.json'
                    collision = self.data / ('memory.sqlite' + suffix)
                    if role in ('source', 'source_alias'):
                        self.data.mkdir()
                        if role == 'source':
                            self.source = collision
                            self.write_source()
                        else:
                            os.link(self.source, collision)
                    else:
                        self.receipt = collision
                    before = self.source.read_bytes()
                    result = self.run_import()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.source.read_bytes(), before)
                    self.assertFalse((self.data / 'memory.sqlite').exists())

    def test_normal_mnemosyne_invalidation_preserves_overlapping_source_interval(self):
        old = self.payload['working_memory'][0]
        new = copy.deepcopy(old)
        new.update(id='replacement', content='Use PostgreSQL.', timestamp='2026-02-01T10:00:00Z')
        old.update(superseded_by='replacement', valid_until='2026-02-01T10:00:01Z')
        self.payload['working_memory'].append(new)
        self.write_source()
        self.successful()
        _, records = self.records()
        stored = Store(self.data / 'memory.sqlite').dispatch(Principal('test', ('owner',)), 'inspect',
            {'id': records['working_memory', 'same-id']['memory_id']})
        self.assertEqual(stored['valid_until'], '2026-02-01T10:00:01.000000Z')
        self.assertEqual(stored['status'], 'superseded')

    def test_supersession_does_not_promote_inferred_candidates(self):
        old = self.payload['working_memory'][0]
        new = copy.deepcopy(old)
        new.update(id='replacement', content='Use PostgreSQL.', timestamp='2026-02-01T10:00:00Z')
        old.update(superseded_by='replacement', veracity='inferred')
        self.payload['working_memory'].append(new)
        self.write_source()
        self.successful()
        store = Store(self.data / 'memory.sqlite')
        p = Principal('test', ('owner',))
        recalled = store.dispatch(p, 'context', {'query': 'SQLite', 'as_of': '2026-01-15T00:00:00Z'})
        self.assertEqual(recalled['memories'], [])
        _, records = self.records()
        old_memory = store.dispatch(p, 'inspect', {'id': records['working_memory', 'same-id']['memory_id']})
        self.assertEqual(old_memory['status'], 'candidate')
        self.assertEqual(len(old_memory['relations']), 1)


if __name__ == '__main__':
    unittest.main()
