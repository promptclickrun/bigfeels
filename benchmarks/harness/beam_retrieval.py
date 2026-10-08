"""BEAM source-retrieval diagnostic for bigfeels' lexical recall path.

Ingests every BEAM user/assistant message through the native `remember` API
into one isolated store per conversation, then asks each probing question
through the native `context` API at fixed byte budgets. The metric is
annotated-source recall: the share of a question's labeled source messages
that appear in the returned context. It measures evidence retrieval, not
answer quality, and is not comparable to published BEAM answer scores.

Only the question text reaches retrieval. Answers, rubrics, and source labels
are read after ingestion and used only for scoring.

    python -m benchmarks.harness.beam_retrieval --beam-dir /path/to/BEAM/chats \
        --stores /tmp/beam-stores --json-out /tmp/beam.json

Pass `--compare earlier.json` to report paired differences with a
conversation-level bootstrap interval. Pass `--embedding-model` (and
`--embedding-url` for a local OpenAI-compatible server) to index every memory
and rank with keywords and embeddings together.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parent.parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from bigfeels_mem.providers import OpenAIProvider  # noqa: E402
from bigfeels_mem.store import Principal, Store  # noqa: E402

TIERS = ('100K', '500K', '1M')
SPACE = 'beam'
CHUNK_CHARS = 12_000
EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)
PRINCIPAL = Principal('beam', (SPACE,))


def chat_file(history):
    """Follow the released runner: prefer the truncated chat when present."""
    truncated = history / 'chat_trunecated.json'
    return truncated if truncated.exists() else history / 'chat.json'


def messages(history):
    """Yield (source_id, role, time_anchor, text) in file order."""
    for batch in json.loads(chat_file(history).read_text()):
        anchor = batch.get('time_anchor')
        for turn in batch['turns']:
            for message in turn:
                anchor = message.get('time_anchor') or anchor
                yield message['id'], message['role'], anchor, message['content']


def flatten_ids(value):
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [item for part in value for item in flatten_ids(part)]
    return []


def ingest(history, db_path):
    """Save each message (chunked when oversized) and map memory IDs to sources."""
    store = Store(db_path)
    store.create_space(SPACE)
    owners, seen = {}, {}
    sequence = 0
    for source_id, role, anchor, text in messages(history):
        seen.setdefault(source_id, set()).add(sequence)
        chunks = [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)] or ['']
        for number, chunk in enumerate(chunks, 1):
            header = f'[{role} message {source_id}, turn {sequence}'
            header += f', {anchor}' if anchor else ''
            header += f', part {number}/{len(chunks)}]' if len(chunks) > 1 else ']'
            # Synthetic, strictly increasing validity preserves input order
            # without claiming when the fact was historically true.
            moment = (EPOCH + timedelta(minutes=sequence, seconds=number)).isoformat()
            memory = store.dispatch(PRINCIPAL, 'remember', {
                'space': SPACE, 'content': f'{header}\n{chunk}', 'kind': 'episode',
                'valid_from': moment})
            owners[memory['id']] = source_id
        sequence += 1
    ambiguous = sorted(source for source, turns in seen.items() if len(turns) > 1)
    return owners, ambiguous


def embed_missing(db_path, embedder):
    """Index memories without a vector for this model, 32 per provider call."""
    store = Store(db_path)
    with store.connection() as c:
        rows = c.execute('SELECT id,content FROM memories WHERE id NOT IN '
                         '(SELECT memory_id FROM vectors WHERE model=?)', (embedder.model,)).fetchall()
    for start in range(0, len(rows), 32):
        batch = rows[start:start + 32]
        vectors = embedder.embed([row['content'] for row in batch])
        with store.connection(True) as c:
            c.executemany('INSERT OR REPLACE INTO vectors VALUES (?,?,?)',
                          [(row['id'], embedder.model, json.dumps(v)) for row, v in zip(batch, vectors)])


def run_history(job):
    tier, history, stores, budgets, provider = job
    history = Path(history)
    db_path = Path(stores) / f'{tier}-{history.name}.sqlite'
    sidecar = db_path.with_suffix('.json')
    started = time.perf_counter()
    if sidecar.exists():
        meta = json.loads(sidecar.read_text())
        ingest_seconds = None
    else:
        for stale in db_path.parent.glob(db_path.name + '*'):
            stale.unlink()
        owners, ambiguous = ingest(history, db_path)
        meta = {'owners': owners, 'ambiguous': ambiguous}
        sidecar.write_text(json.dumps(meta))
        ingest_seconds = time.perf_counter() - started
    owners, ambiguous = meta['owners'], set(meta['ambiguous'])
    store = Store(db_path)
    if provider:
        store.embedder = OpenAIProvider(provider)
        embed_missing(db_path, store.embedder)
    probes = json.loads((history / 'probing_questions' / 'probing_questions.json').read_text())
    rows = []
    for category, items in probes.items():
        for index, item in enumerate(items):
            sources = set(flatten_ids(item.get('source_chat_ids')))
            if category == 'abstention' or not sources or sources & ambiguous:
                continue
            for budget in budgets:
                began = time.perf_counter()
                result = store.dispatch(PRINCIPAL, 'context', {
                    'query': item['question'], 'budget': budget, 'spaces': [SPACE]})
                elapsed = (time.perf_counter() - began) * 1000
                found = {owners[m['id']] for m in result['memories']} & sources
                rows.append({
                    'tier': tier, 'history': history.name, 'category': category,
                    'index': index, 'budget': budget, 'recall': len(found) / len(sources),
                    'any': bool(found), 'all': found == sources, 'ms': elapsed,
                    'bytes': result['tokens'], 'returned': len(result['memories']),
                })
    return {'tier': tier, 'history': history.name, 'ingest_seconds': ingest_seconds, 'rows': rows}


def key(row):
    return (row['tier'], row['history'], row['category'], row['index'], row['budget'])


def bootstrap(pairs, rounds=2000, seed=20261005):
    """95% interval for a paired mean difference, resampling whole conversations."""
    by_history = {}
    for history, delta in pairs:
        by_history.setdefault(history, []).append(delta)
    groups = list(by_history.values())
    rng = random.Random(seed)
    means = []
    for _ in range(rounds):
        sample = [d for _ in groups for d in rng.choice(groups)]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(rounds * 0.025)], means[int(rounds * 0.975) - 1]


def summarize(rows, baseline=None):
    lines = []
    earlier = {key(r): r for r in baseline or []}
    for budget in sorted({r['budget'] for r in rows}):
        lines.append(f'\nbudget {budget} bytes')
        lines.append('tier  questions  recall%  any%   all%   p50ms  p95ms' + ('  delta_pp  95%_interval' if earlier else ''))
        for tier in TIERS:
            subset = [r for r in rows if r['tier'] == tier and r['budget'] == budget]
            if not subset:
                continue
            ms = sorted(r['ms'] for r in subset)
            line = (f'{tier:5} {len(subset):9}  {100 * statistics.mean(r["recall"] for r in subset):6.2f}'
                    f'  {100 * statistics.mean(r["any"] for r in subset):5.2f}'
                    f'  {100 * statistics.mean(r["all"] for r in subset):5.2f}'
                    f'  {ms[len(ms) // 2]:6.1f} {ms[int(len(ms) * 0.95)]:6.1f}')
            pairs = [(r['history'], r['recall'] - earlier[key(r)]['recall'])
                     for r in subset if key(r) in earlier]
            if pairs:
                low, high = bootstrap(pairs)
                line += f'  {100 * statistics.mean(d for _, d in pairs):+7.2f}  [{100 * low:+.2f}, {100 * high:+.2f}]'
            lines.append(line)
        lines.append('category                  recall%' + ('  delta_pp' if earlier else ''))
        for category in sorted({r['category'] for r in rows}):
            subset = [r for r in rows if r['category'] == category and r['budget'] == budget]
            line = f'{category:25} {100 * statistics.mean(r["recall"] for r in subset):6.2f}'
            deltas = [r['recall'] - earlier[key(r)]['recall'] for r in subset if key(r) in earlier]
            if deltas:
                line += f'  {100 * statistics.mean(deltas):+7.2f}'
            lines.append(line)
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--beam-dir', required=True, type=Path, help='BEAM repository chats/ directory')
    parser.add_argument('--stores', required=True, type=Path, help='Directory for per-history stores (reused when present)')
    parser.add_argument('--tiers', nargs='+', default=list(TIERS), choices=TIERS)
    parser.add_argument('--budgets', nargs='+', type=int, default=[3200, 12800])
    parser.add_argument('--histories', nargs='+', help='Restrict to these history numbers')
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument('--json-out', type=Path)
    parser.add_argument('--compare', type=Path, help='Earlier --json-out for paired differences')
    parser.add_argument('--embedding-model', help='Also rank with this embedding model')
    parser.add_argument('--embedding-url', default='https://api.openai.com/v1',
                        help='OpenAI-compatible base URL; loopback URLs keep content local')
    args = parser.parse_args(argv)
    provider = ({'base_url': args.embedding_url, 'embedding_model': args.embedding_model,
                 'allow_remote': True, 'timeout': 60} if args.embedding_model else None)
    args.stores.mkdir(parents=True, exist_ok=True)
    jobs = []
    for tier in args.tiers:
        for history in sorted((args.beam_dir / tier).iterdir(), key=lambda p: int(p.name)):
            if not args.histories or history.name in args.histories:
                jobs.append((tier, str(history), str(args.stores), args.budgets, provider))
    # Largest first keeps the pool busy at the end of a run.
    jobs.sort(key=lambda job: -chat_file(Path(job[1])).stat().st_size)
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(run_history, jobs))
    rows = sorted((row for result in results for row in result['rows']), key=key)
    ingest = [r['ingest_seconds'] for r in results if r['ingest_seconds'] is not None]
    baseline = json.loads(args.compare.read_text())['rows'] if args.compare else None
    print(summarize(rows, baseline))
    if ingest:
        print(f'\ningested {len(ingest)} histories, median {statistics.median(ingest):.1f}s')
    if args.json_out:
        args.json_out.write_text(json.dumps({'rows': rows, 'ingest_seconds': ingest}))


if __name__ == '__main__':
    main()
