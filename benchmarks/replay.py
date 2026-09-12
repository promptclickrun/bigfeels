"""Reproducible retrieval regression; deliberately not a model quality claim."""
import argparse
import json
from pathlib import Path
import tempfile
import time

from bigfeels_mem.retrieval import cosine, terms, token_cost
from bigfeels_mem.store import Store


class FixtureEmbedding:
    model = 'fixture-topics-v1'
    calls = 0

    def embed(self, texts):
        self.calls += 1
        topics = [('sqlite', 'postgresql', 'database'), ('bicycle', 'cycling'),
                  ('penguin', 'codename'), ('deployment', 'deploy')]
        return [[float(any(w in t.lower() for w in topic)) for topic in topics] for t in texts]


def run():
    with tempfile.TemporaryDirectory() as temp:
        store = Store(Path(temp) / 'replay.sqlite')
        store.create_space('project:private')
        writer = store.authenticate(store.pair('hermes', ['owner', 'project:private']))
        reader = store.authenticate(store.pair('openclaw', ['owner']))

        def save(content, **kw):
            return store.dispatch(writer, 'remember', dict(space='owner', content=content,
                                  valid_from='2025-01-01T00:00:00Z', **kw))

        old = save('The database is PostgreSQL.', key='database')
        new = store.dispatch(writer, 'correct', dict(id=old['id'], revision=1,
                             content='The database is SQLite.', valid_from='2026-01-01T00:00:00Z'))
        bike = save('I travel by bicycle.')
        save('I propose a deployment.', basis='inferred', kind='task', outcome='proposed')
        store.dispatch(writer, 'remember', dict(space='project:private', content='The secret codename is penguin.'))
        embedder = FixtureEmbedding()
        store.rebuild_embeddings(embedder)
        store.embedder = embedder
        cases = [
            dict(name='current_correction', query='database', expected=[new['content']], forbidden=[old['content']]),
            dict(name='historical_state', query='database', as_of='2025-06-01T00:00:00Z', expected=[old['content']], forbidden=[new['content']]),
            dict(name='semantic_recall', query='cycling', expected=[bike['content']], forbidden=[]),
            dict(name='private_scope', query='penguin codename', expected=[], forbidden=['The secret codename is penguin.']),
            dict(name='unsupported_completion', query='deployment', expected=[], forbidden=['I propose a deployment.']),
            dict(name='unrelated_abstention', query='tropical holiday', expected=[], forbidden=[]),
        ]
        with store.connection() as c:
            owner_records = [store._memory(c, reader, r[0]) for r in c.execute("SELECT id FROM memories WHERE space='owner'")]
        # Curated baseline excludes inferred proposals but retains chronological
        # notes, rather than receiving an oracle resolution of test questions.
        curated = [m for m in owner_records if m['basis'] != 'inferred']
        baseline_vectors = dict(zip([m['id'] for m in owner_records],
                                    embedder.embed([m['content'] for m in owner_records])))
        report = {'fixture': 'continuity-v1', 'model': 'none; deterministic retrieval only',
                  'embedding': embedder.model, 'budget': 3200,
                  'expected_total': sum(len(x['expected']) for x in cases), 'policies': {}}
        for policy in ('no_memory', 'curated_markdown', 'simple_hybrid', 'bigfeels'):
            covered = forbidden = citations = total = abstentions = size = 0
            details = []
            calls_before = embedder.calls
            start = time.perf_counter()
            for case in cases:
                if policy == 'no_memory':
                    found = []
                elif policy == 'curated_markdown':
                    found = curated
                elif policy == 'simple_hybrid':
                    query_terms = set(terms(case['query']))
                    qvector = embedder.embed([case['query']])[0]
                    found = [m for m in owner_records if query_terms & set(terms(m['content']))
                             or cosine(qvector, baseline_vectors[m['id']]) >= .65]
                else:
                    data = dict(query=case['query'], budget=3200)
                    if 'as_of' in case:
                        data['as_of'] = case['as_of']
                    found = store.dispatch(reader, 'context', data)['memories']
                bounded, used = [], 0
                for memory in found:
                    cost = token_cost(memory)
                    if cost + used <= 3200:
                        bounded.append(memory)
                        used += cost
                contents = [m['content'] for m in bounded]
                hits = sum(e in contents for e in case['expected'])
                bad = sum(f in contents for f in case['forbidden'])
                covered += hits
                forbidden += bad
                total += len(bounded)
                citations += sum(bool(m.get('evidence_ids')) for m in bounded)
                abstentions += not bounded and not case['expected']
                size += used
                details.append({'case': case['name'], 'expected_covered': hits,
                                'forbidden_exposures': bad, 'returned': len(bounded)})
            report['policies'][policy] = {
                'expected_covered': covered, 'forbidden_exposures': forbidden,
                'citation_rate': citations / total if total else None,
                'correct_abstentions': abstentions, 'context_bytes': size,
                'elapsed_ms': round((time.perf_counter()-start)*1000, 3),
                'fixture_embedding_calls': embedder.calls-calls_before,
                'paid_provider_calls': 0, 'cases': details}
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rendered = json.dumps(run(), indent=2)
    if args.output:
        args.output.write_text(rendered + '\n')
    print(rendered)
