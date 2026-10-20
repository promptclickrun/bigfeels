"""Optional bounded reordering of already authorized keyword hits."""
def reorder(result, response):
    memories = result['memories']
    count = min(20, len(memories))
    rows = response.get('response') if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise ValueError('Invalid reranker response')
    order = [row.get('id') for row in rows if isinstance(row, dict)]
    if len(order) != count or any(type(i) is not int for i in order) or set(order) != set(range(count)):
        raise ValueError('Invalid reranker indices')
    return {**result, 'memories': [memories[i] for i in order] + memories[count:], 'rerank': 'workers_ai'}
