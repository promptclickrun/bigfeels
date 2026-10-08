"""BEAM answer-quality evaluation for bigfeels with any command-line model.

For each BEAM probing question, bigfeels retrieves context through its native
`context` API. A reader model answers with BEAM's released RAG prompt, and a
judge model grades the answer with BEAM's released rubric prompt. Event
ordering uses BEAM's LLM alignment and normalized Kendall tau. Scores follow
the released report: the mean per question of the rubric score (tau_norm for
event ordering).

Models are shell commands that read a prompt on stdin and print the reply,
so any CLI can serve, for example
`copilot -s --model gpt-6-luna --available-tools --disable-builtin-mcps`.
Build stores first with beam_retrieval.py using the same embedding flags.
Results append to JSONL files in --out, so interrupted runs resume.

    python -m benchmarks.harness.beam_answers --beam-dir BEAM/chats \
        --beam-src BEAM --stores /tmp/beam-stores --out /tmp/beam-answers \
        --reader 'READER COMMAND' --judge 'JUDGE COMMAND'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .beam_retrieval import PRINCIPAL, SPACE, TIERS  # also puts src/ on sys.path
from bigfeels_mem.providers import OpenAIProvider  # noqa: E402
from bigfeels_mem.store import Store  # noqa: E402

CATEGORIES = ('abstention', 'contradiction_resolution', 'event_ordering', 'information_extraction',
              'instruction_following', 'knowledge_update', 'multi_session_reasoning',
              'preference_following', 'summarization', 'temporal_reasoning')

# BEAM's llm_equivalence system and user messages, sent as one prompt.
EQUIVALENCE_PROMPT = """
            You are a binary classifier.
            If the TWO snippets describe the SAME event/fact, reply **YES**
            Otherwise reply **NO**. No extra words.
            DO NOT provide any exaplanation.

First snippet: {first} \n
                       Second snippet: {second}
"""


def load_prompts(beam_src):
    """Read BEAM's prompt strings without importing its dependencies."""
    namespace = {}
    exec(compile((Path(beam_src) / 'src' / 'prompts.py').read_text(), 'prompts.py', 'exec'), namespace)
    return namespace['answer_generation_for_rag'], namespace['unified_llm_judge_base_prompt']


def ask(command, prompt):
    """Run a model command with the prompt on stdin.

    Failures and hung calls retry with growing pauses, which also rides out
    the short rate limits that CLI tools hit when launched thousands of times.
    """
    for pause in (5, 30, 120, 300, None):
        # A new session lets a timeout stop the whole command, not just its shell.
        process = subprocess.Popen(command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(prompt, timeout=300)
            reply, detail = stdout.strip(), (stderr or stdout).strip()[-300:]
            if process.returncode == 0 and reply:
                return reply
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            detail = 'timed out after 300 seconds'
        if pause:
            time.sleep(pause)
    raise RuntimeError(f'model command failed: {detail}')


def judge_score(reply):
    """Parse BEAM's {"score": ...} judge reply, tolerating surrounding text."""
    match = re.search(r'\{.*\}', reply, re.DOTALL)
    try:
        return float(json.loads(match.group(0))['score'])
    except (AttributeError, KeyError, TypeError, ValueError):
        found = re.search(r'"?score"?\s*:\s*"?([01](?:\.\d+)?)', reply)
        if not found:
            raise ValueError(f'unparseable judge reply: {reply[:200]}')
        return float(found.group(1))


def kendall_tau_b(x, y):
    """Kendall tau-b, matching scipy.stats.kendalltau(variant='b')."""
    concordant = discordant = tied_x = tied_y = 0
    for i in range(len(x)):
        for j in range(i + 1, len(x)):
            dx, dy = (x[i] > x[j]) - (x[i] < x[j]), (y[i] > y[j]) - (y[i] < y[j])
            tied_x += dx == 0
            tied_y += dy == 0
            concordant += dx * dy > 0
            discordant += dx * dy < 0
    pairs = len(x) * (len(x) - 1) // 2
    denominator = math.sqrt((pairs - tied_x) * (pairs - tied_y))
    return (concordant - discordant) / denominator if denominator else math.nan


def event_ordering(judge, reference, answer):
    """BEAM's align_with_llm and event_ordering_score; returns tau_norm and F1."""
    used, system = set(), []
    for line in answer.split('\n'):
        match = None
        # Blank lines never describe an event; skip the model call for them.
        if line.strip():
            for index, item in enumerate(reference):
                if index not in used and 'yes' in ask(judge, EQUIVALENCE_PROMPT.format(
                        first=item, second=line)).lower():
                    match = index
                    break
        if match is None:
            system.append(line)
        else:
            system.append(reference[match])
            used.add(match)
    tp = len(set(reference) & set(system))
    fp = len([x for x in system if x not in reference])
    fn = len([x for x in reference if x not in system])
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    union = list(dict.fromkeys(reference + system))

    def ranks(sequence):
        positions = {item: i + 1 for i, item in enumerate(sequence)}
        return [positions.get(item, len(union) + 1) for item in union]

    tau = kendall_tau_b(ranks(reference), ranks(system))
    # An undefined tau (no comparable pairs) scores zero instead of NaN.
    return {'tau_norm': 0.0 if math.isnan(tau) else (tau + 1) / 2, 'f1': f1}


def questions(beam_dir, tiers, histories, per_category):
    for tier in tiers:
        for history in sorted((Path(beam_dir) / tier).iterdir(), key=lambda p: int(p.name)):
            if histories and history.name not in histories:
                continue
            probes = json.loads((history / 'probing_questions' / 'probing_questions.json').read_text())
            for category in CATEGORIES:
                for index, item in enumerate(probes.get(category, [])[:per_category]):
                    yield {'key': f'{tier}/{history.name}/{category}/{index}', 'tier': tier,
                           'history': history.name, 'category': category, 'index': index,
                           'question': item['question'], 'rubric': item['rubric']}


class Ledger:
    """Append-only JSONL results keyed by question, safe across threads."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        self.rows = {}
        if path.exists():
            for line in path.read_text().splitlines():
                row = json.loads(line)
                self.rows[row['key']] = row

    def add(self, row):
        with self.lock:
            self.rows[row['key']] = row
            with self.path.open('a') as f:
                f.write(json.dumps(row) + '\n')


def summarize(grades):
    lines = ['tier   questions  score   ' + '  '.join(c[:6] for c in CATEGORIES)]
    for tier in (*TIERS, 'all'):
        rows = [g for g in grades if tier in (g['tier'], 'all')]
        if not rows:
            continue
        by_category = {c: [g['score'] for g in rows if g['category'] == c] for c in CATEGORIES}
        lines.append(f'{tier:6} {len(rows):9}  {100 * statistics.mean(g["score"] for g in rows):5.1f}   '
                     + '  '.join(f'{100 * statistics.mean(v):6.1f}' if v else '     -' for v in by_category.values()))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--beam-dir', required=True, type=Path, help='BEAM chats/ directory')
    parser.add_argument('--beam-src', required=True, type=Path, help='BEAM repository root (for src/prompts.py)')
    parser.add_argument('--stores', required=True, type=Path, help='Stores built by beam_retrieval.py')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--reader', required=True, help='Shell command: prompt on stdin, answer on stdout')
    parser.add_argument('--judge', required=True, help='Shell command: prompt on stdin, judgment on stdout')
    parser.add_argument('--tiers', nargs='+', default=list(TIERS), choices=TIERS)
    parser.add_argument('--histories', nargs='+', help='Restrict to these history numbers')
    parser.add_argument('--per-category', type=int, help='At most this many questions per category per history')
    parser.add_argument('--budget', type=int, default=32000)
    parser.add_argument('--context-order', choices=('rank', 'time'), default='rank',
                        help='Present retrieved memories best-first or oldest-first')
    parser.add_argument('--embedding-model')
    parser.add_argument('--embedding-url', default='https://api.openai.com/v1')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    answer_prompt, judge_prompt = load_prompts(args.beam_src)
    provider = ({'base_url': args.embedding_url, 'embedding_model': args.embedding_model,
                 'allow_remote': True, 'timeout': 60} if args.embedding_model else None)
    manifest = {'started_at': datetime.now(timezone.utc).isoformat(), 'reader': args.reader,
                'judge': args.judge, 'budget': args.budget, 'embedding_model': args.embedding_model,
                'context_order': args.context_order,
                'tiers': args.tiers, 'histories': args.histories, 'per_category': args.per_category}
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    pending = list(questions(args.beam_dir, args.tiers, args.histories, args.per_category))
    answers, grades = Ledger(args.out / 'answers.jsonl'), Ledger(args.out / 'grades.jsonl')
    stores, opening = {}, threading.Lock()

    def store_for(item):
        db = args.stores / f'{item["tier"]}-{item["history"]}.sqlite'
        with opening:
            if db not in stores:
                stores[db] = Store(db)
                stores[db].embedder = OpenAIProvider(provider) if provider else None
            return stores[db]

    def answer(item):
        if item['key'] in answers.rows:
            return
        result = store_for(item).dispatch(PRINCIPAL, 'context', {
            'query': item['question'], 'budget': args.budget, 'spaces': [SPACE]})
        memories = result['memories']
        if args.context_order == 'time':
            memories = sorted(memories, key=lambda m: m['valid_from'])
        context = '\n\n'.join(m['content'] for m in memories)
        reply = ask(args.reader, answer_prompt.replace('<context>', context).replace('<question>', item['question']))
        answers.add({**item, 'answer': reply, 'context_bytes': result['tokens'],
                     'memories': [m['id'] for m in result['memories']]})

    def grade(item):
        if item['key'] in grades.rows:
            return
        reply = answers.rows[item['key']]['answer']
        judged = [judge_score(ask(args.judge, judge_prompt.replace('<rubric_item>', rubric)
                                  .replace('<llm_response>', reply))) for rubric in item['rubric']]
        row = {'key': item['key'], 'tier': item['tier'], 'category': item['category'],
               'rubric_scores': judged, 'llm_judge_score': sum(judged) / len(judged)}
        if item['category'] == 'event_ordering':
            row.update(event_ordering(args.judge, item['rubric'], reply))
        row['score'] = row['tau_norm'] if item['category'] == 'event_ordering' else row['llm_judge_score']
        grades.add(row)

    for stage, work in (('answering', answer), ('grading', grade)):
        print(f'{stage} {len(pending)} questions', flush=True)
        with ThreadPoolExecutor(args.workers) as pool:
            failures = [f for f in [pool.submit(work, item) for item in pending] if f.exception()]
        for failure in failures[:5]:
            print(f'  failed: {failure.exception()}', file=sys.stderr)
        if failures:
            print(f'{len(failures)} {stage} failures; rerun to resume', file=sys.stderr)
    done = [grades.rows[item['key']] for item in pending if item['key'] in grades.rows]
    print(summarize(done))


if __name__ == '__main__':
    main()
