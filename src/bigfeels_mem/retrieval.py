"""Deterministic bounded context and replaceable vector scoring."""
import json
import math
import re


# Function words should not make unrelated natural questions look relevant. Keep
# domain words and verbs such as "use" and "prefer": they can be meaningful in
# short memory queries.
_STOP_WORDS = frozenset({
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'been', 'being', 'but', 'by',
    'can', 'could', 'did', 'do', 'does', 'for', 'from', 'had', 'has', 'have',
    'he', 'her', 'hers', 'him', 'his', 'how', 'i', 'if', 'in', 'into', 'is',
    'it', 'its', 'me', 'mine', 'my', 'of', 'on', 'or', 'our', 'ours', 'she',
    'should', 'that', 'the', 'their', 'theirs', 'them', 'they', 'this', 'those',
    'to', 'us', 'was', 'we', 'were', 'what', 'when', 'where', 'which', 'who',
    'why', 'will', 'with', 'would', 'you', 'your', 'yours',
})

# Small concept families improve ordinary lexical fallback without requiring an
# embedding provider. They are language-level vocabulary, not corpus tuning.
_CONCEPTS = (
    frozenset({'bicycle', 'bicycles', 'bike', 'bikes', 'cycle', 'cycles', 'cycling', 'cyclist'}),
)
_CONCEPT_BY_TERM = {term: family for family in _CONCEPTS for term in family}


def _stem(term):
    aliases = {'preferred': 'prefer', 'prefers': 'prefer', 'preferences': 'preference'}
    if term in aliases:
        return aliases[term]
    if len(term) > 5 and term.endswith('ies'):
        return term[:-3] + 'y'
    if len(term) > 5 and term.endswith('ing'):
        base = term[:-3]
        return base[:-1] if len(base) > 2 and base[-1] == base[-2] else base
    if len(term) > 4 and term.endswith('ed'):
        return term[:-2]
    if len(term) > 4 and term.endswith('s'):
        return term[:-1]
    return term


def terms(query, *, expand=True):
    """Return stable, informative lexical terms for a natural query."""
    raw = re.findall(r'[^\W_]+(?:[\'-][^\W_]+)*', query.lower(), re.UNICODE)
    result = []
    for term in raw:
        if term in _STOP_WORDS:
            continue
        for value in (term, _stem(term)):
            if value and value not in result:
                result.append(value)
        if expand:
            family = _CONCEPT_BY_TERM.get(term)
            if family:
                for value in sorted(family):
                    if value not in result:
                        result.append(value)
        if len(result) >= 64:
            break
    return result[:64]


def fts_query(query):
    return ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms(query))


def lexical_matches(query, content, key=None, *, expand=True):
    wanted = set(terms(query, expand=expand))
    if not wanted:
        return set()
    available = set(terms(' '.join(value for value in (content, key) if value), expand=expand))
    return wanted & available


def lexical_score(query, content, key=None):
    """Return a bounded lexical relevance score, or zero to abstain."""
    wanted = set(terms(query))
    matched = lexical_matches(query, content, key)
    if not wanted or not matched:
        return 0.0
    # Coverage favors records answering more of a natural question while still
    # allowing a single distinctive noun to retrieve a concise fact.
    return len(matched) / len(wanted) + min(len(matched), 4) / 10


def claims_compatible(first, second):
    """Conservatively avoid calling elaborations/paraphrases contradictions."""
    a, b = set(terms(first, expand=False)), set(terms(second, expand=False))
    if not a or not b:
        return first.strip().casefold() == second.strip().casefold()
    negations = {'no', 'not', 'never', 'without', 'avoid', 'forbid', 'forbidden'}
    if bool(a & negations) != bool(b & negations):
        return False
    shared = a & b
    if a <= b or b <= a:
        return True
    return len(shared) >= 2 and len(shared) / len(a | b) >= 0.5


def cosine(a, b):
    if not a or len(a) != len(b):
        return 0.0
    norm = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / norm if norm else 0.0


def token_cost(record):
    # UTF-8 byte count is a conservative tokenizer-independent upper bound for
    # common byte/subword tokenizers, including metadata returned to the agent.
    return len(json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
