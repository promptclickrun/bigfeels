"""Write model-extracted memory for BEAM conversations.

Each message is observed as evidence dated by BEAM's time anchor, the way a
host captures turns, then bigfeels' own batched extraction writes memories
from it under the store's default policy. The extractor is a shell command
that reads a prompt on stdin and prints the model's JSON reply.

    python -m benchmarks.harness.beam_extract --beam-dir BEAM/chats \
        --stores /tmp/beam-stores-model --extractor 'COMMAND' \
        --embedding-model nomic-embed-text --embedding-url http://127.0.0.1:11434/v1

A missing store is created, holding extracted memory only, as a host
integration would. --combine RAW OUT then writes copies of the raw-message
stores from beam_retrieval.py with the same extracted memory added, so both
variants share one extraction. It resumes: observed messages are skipped,
and unfinished extraction jobs are retried.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .beam_answers import ask
from .beam_retrieval import CHUNK_CHARS, PRINCIPAL, SPACE, TIERS, embed_missing, messages
from bigfeels_mem.providers import (  # noqa: E402
    OpenAIProvider, batch_extraction_messages, extraction_messages, parse_batch_extraction,
    parse_extraction)
from bigfeels_mem.store import Store  # noqa: E402


def as_prompt(chat):
    """Flatten chat messages for a CLI model that takes one prompt."""
    return '\n\n'.join(f'[{m["role"]}]\n{m["content"]}' for m in chat)


def json_object(reply):
    """CLI models may wrap JSON in prose or fences; pass bigfeels the object only."""
    start, end = reply.find('{'), reply.rfind('}')
    return reply[start:end + 1] if 0 <= start < end else reply


class CommandExtractor:
    """bigfeels extractor backed by a model CLI; calls are capped globally."""

    def __init__(self, command, slots):
        self.command, self.slots = command, slots

    def extract(self, evidence):
        with self.slots:
            return parse_extraction(json_object(ask(self.command, as_prompt(extraction_messages(evidence)))))

    def extract_batch(self, items):
        with self.slots:
            reply = ask(self.command, as_prompt(batch_extraction_messages(items)))
            return parse_batch_extraction(json_object(reply), len(items))


def anchor_time(anchor, fallback):
    """BEAM anchors look like 'March-15-2024'; keep the last good one."""
    try:
        return datetime.strptime(anchor, '%B-%d-%Y').replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return fallback


def observe_messages(store, history, roles):
    """Queue each message from roles for extraction, dated by its time anchor."""
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for sequence, (source_id, role, anchor, text) in enumerate(messages(history)):
        day = anchor_time(anchor, when)
        # Keep file order within a day without claiming real clock times.
        when = when + timedelta(seconds=1) if day.date() == when.date() else day
        if role not in roles:
            continue
        for number, start in enumerate(range(0, max(len(text), 1), CHUNK_CHARS), 1):
            # Some histories reuse message IDs, so the event ID includes position.
            store.dispatch(PRINCIPAL, 'observe', {
                'space': SPACE, 'source': 'beam', 'source_event_id': f'{source_id}:{sequence}:{number}',
                'session_id': history.name, 'speaker': role, 'captured': True,
                'content': text[start:start + CHUNK_CHARS], 'occurred_at': when.isoformat()})


def combine(raw_db, model_db, out_db):
    """Copy a raw-message store and add a model store's evidence and memory."""
    with closing(sqlite3.connect(raw_db)) as raw, closing(sqlite3.connect(out_db)) as out:
        raw.backup(out)
    Store(out_db)  # Bring the copy to the current schema first.
    with closing(sqlite3.connect(out_db)) as c:
        c.execute('ATTACH ? AS model', (str(model_db),))
        for table in ('evidence', 'memories', 'claims', 'supports', 'relations', 'vectors', 'jobs'):
            columns = ','.join(row[1] for row in c.execute(f'PRAGMA main.table_info({table})'))
            c.execute(f'INSERT INTO main.{table}({columns}) SELECT {columns} FROM model.{table}')
        c.commit()
    sidecars = [json.loads(Path(db).with_suffix('.json').read_text()) for db in (raw_db, model_db)]
    sidecars[0]['owners'].update(sidecars[1]['owners'])
    Path(out_db).with_suffix('.json').write_text(json.dumps(sidecars[0]))


def extract_history(db_path, history, extractor, batch, provider, sidecar, roles):
    store = Store(db_path)
    store.create_space(SPACE)
    if not sidecar.exists():
        sidecar.write_text(json.dumps({'owners': {}, 'ambiguous': []}))
    observe_messages(store, history, roles)
    while store.process_batch(extractor, spaces=[SPACE], limit=batch):
        pass
    with store.connection() as c:
        # Credit each extracted fact to its source message for recall checks.
        facts = {r['id']: int(r['source_event_id'].split(':')[0]) for r in c.execute(
            "SELECT m.id,e.source_event_id FROM memories m JOIN supports s ON s.memory_id=m.id "
            "JOIN evidence e ON e.id=s.evidence_id WHERE e.source='beam'")}
        jobs = dict(c.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state').fetchall())
    meta = json.loads(sidecar.read_text())
    meta['owners'].update(facts)
    sidecar.write_text(json.dumps(meta))
    if provider:
        embed_missing(db_path, OpenAIProvider(provider))
    return len(facts), jobs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--beam-dir', required=True, type=Path)
    parser.add_argument('--stores', required=True, type=Path,
                        help='Store directory; missing stores are created, existing ones are modified in place')
    parser.add_argument('--extractor', required=True, help='Shell command: prompt on stdin, JSON reply on stdout')
    parser.add_argument('--tiers', nargs='+', default=list(TIERS), choices=TIERS)
    parser.add_argument('--histories', nargs='+')
    parser.add_argument('--batch', type=int, default=8, help='Messages per extraction call (hosts use up to 8)')
    parser.add_argument('--roles', nargs='+', default=['user', 'assistant'], choices=['user', 'assistant'])
    parser.add_argument('--workers', type=int, default=8, help='Concurrent extraction calls')
    parser.add_argument('--combine', nargs=2, type=Path, metavar=('RAW', 'OUT'),
                        help='Also write raw-message stores from RAW plus the extracted memory to OUT')
    parser.add_argument('--embedding-model')
    parser.add_argument('--embedding-url', default='https://api.openai.com/v1')
    args = parser.parse_args(argv)
    provider = ({'base_url': args.embedding_url, 'embedding_model': args.embedding_model,
                 'allow_remote': True, 'timeout': 60} if args.embedding_model else None)
    extractor = CommandExtractor(args.extractor, threading.BoundedSemaphore(args.workers))
    args.stores.mkdir(parents=True, exist_ok=True)
    jobs = []
    for tier in args.tiers:
        for history in sorted((args.beam_dir / tier).iterdir(), key=lambda p: int(p.name)):
            if not args.histories or history.name in args.histories:
                db = args.stores / f'{tier}-{history.name}.sqlite'
                jobs.append((db, history, db.with_suffix('.json')))
    started = time.perf_counter()
    # One thread per store keeps SQLite writes serial per file; the semaphore
    # bounds concurrent model calls across stores.
    with ThreadPoolExecutor(max(args.workers, 1) * 2) as pool:
        results = pool.map(lambda job: (job[0].stem, *extract_history(
            job[0], job[1], extractor, args.batch, provider, job[2], set(args.roles))), jobs)
        for name, facts, states in results:
            print(f'{name}: {facts} facts, jobs {states}', flush=True)
    if args.combine:
        raw, out = args.combine
        out.mkdir(parents=True, exist_ok=True)
        for db, _, _ in jobs:
            combine(raw / db.name, db, out / db.name)
    print(f'done in {time.perf_counter() - started:.0f}s')


if __name__ == '__main__':
    main()
