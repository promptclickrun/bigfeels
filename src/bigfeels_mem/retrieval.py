"""Deterministic bounded context and replaceable vector scoring."""
import json
import math
import re


def terms(query):
    return list(dict.fromkeys(re.findall(r'[\w]+', query.lower(), re.UNICODE)))[:64]


def fts_query(query):
    return ' OR '.join('"' + t.replace('"', '""') + '"' for t in terms(query))


def cosine(a, b):
    if not a or len(a) != len(b):
        return 0.0
    norm = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / norm if norm else 0.0


def token_cost(record):
    # UTF-8 byte count is a conservative tokenizer-independent upper bound for
    # common byte/subword tokenizers, including metadata returned to the agent.
    return len(json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
