"""The shared wiki: curated markdown pages that every agent and the owner read and edit.

Memories are the atomic recall layer agents search. The wiki is the compiled layer on top: a small set of
interlinked pages (SCHEMA.md, index.md, log.md, entities/, concepts/, ...) in the style of an LLM wiki.
It lives in the same vault, so cloud-only agents, Obsidian (over WebDAV) and any markdown client see one copy.

Pages belong to a space and are addressed by a relative path such as `entities/colt-coan.md`. Every write
keeps the previous version in `wiki_history`, and an agent's write must name the revision it read, so two
agents editing one page cannot silently overwrite each other. WebDAV clients cannot send a revision; their
writes win, and the history keeps what they replaced.
"""
from .privacy import redact
from .store import MemoryError, now

MAX_PAGE_BYTES = 1_000_000      # Durable Object SQLite refuses values over 2 MB
MAX_PAGES = 10_000
HISTORY_PER_PAGE = 20
SEARCH_RESULTS = 20

OPERATIONS = ('wiki_list', 'wiki_read', 'wiki_search', 'wiki_history', 'wiki_write', 'wiki_delete')
WRITES = ('wiki_write', 'wiki_delete')


def clean_path(path):
    """A relative page path, or MemoryError. Obsidian names may hold spaces and any Unicode."""
    if not isinstance(path, str):
        raise MemoryError('path must be a string')
    parts = [p for p in path.replace('\\', '/').split('/') if p]
    if not parts or len(path) > 500 or any(p in ('.', '..') or '\x00' in p or len(p) > 200 for p in parts):
        raise MemoryError('Invalid wiki path')
    return '/'.join(parts)


def _row(c, space, path):
    return c.execute('SELECT * FROM wiki_pages WHERE space=? AND path=?', (space, path)).fetchone()


def _text(body):
    try:
        return bytes(body).decode('utf-8')
    except UnicodeDecodeError:
        return None


def summary(row):
    return {'path': row['path'], 'size': len(row['body']), 'revision': row['revision'],
            'updated_at': row['updated_at'], 'updated_by': row['updated_by']}


def rows(c, space):
    return c.execute('SELECT * FROM wiki_pages WHERE space=? ORDER BY path', (space,)).fetchall()


def list_pages(c, space, prefix=''):
    start = clean_path(prefix) + '/' if isinstance(prefix, str) and prefix.strip('/') else ''
    return [summary(r) for r in rows(c, space) if r['path'].startswith(start)]


def read(c, space, path):
    row = _row(c, space, clean_path(path))
    if row is None:
        raise MemoryError('No such wiki page', 404)
    text = _text(row['body'])
    page = summary(row)
    if text is None:
        page['binary'] = True
    else:
        page['content'] = text
    return page


def raw(c, space, path):
    """(bytes, summary) for a file, or None."""
    row = _row(c, space, path)
    return (bytes(row['body']), summary(row)) if row else None


def write(c, space, path, body, author, base_revision=None, *, require_revision=True, keep_history=True):
    """Create or replace a page. `body` is str (agents) or bytes (WebDAV).

    With `require_revision`, replacing an existing page needs the revision the writer read, and a stale one
    is refused with 409. A new page takes `base_revision` 0 or None.
    """
    path = clean_path(path)
    if isinstance(body, str):
        body = body.encode('utf-8')
    if len(body) > MAX_PAGE_BYTES:
        raise MemoryError('Wiki page exceeds 1 MB', 413)
    old = _row(c, space, path)
    if old is None:
        if require_revision and base_revision not in (None, 0):
            raise MemoryError('Wiki page does not exist; create it with base_revision 0', 409)
        if c.execute('SELECT COUNT(*) FROM wiki_pages WHERE space=?', (space,)).fetchone()[0] >= MAX_PAGES:
            raise MemoryError('The wiki holds the maximum number of pages', 413)
        previous = c.execute('SELECT MAX(revision) FROM wiki_history WHERE space=? AND path=?', (space, path)).fetchone()[0]
        revision = (previous or 0) + 1
    else:
        if require_revision and base_revision != old['revision']:
            raise MemoryError(f'Wiki page changed (now revision {old["revision"]}); read it again before writing', 409)
        if bytes(old['body']) == body:
            return summary(old)
        if keep_history:
            _keep(c, space, path, old['revision'], old['body'], old['updated_at'], old['updated_by'])
        revision = old['revision'] + 1
    c.execute('INSERT OR REPLACE INTO wiki_pages VALUES (?,?,?,?,?,?)', (space, path, body, revision, now(), author))
    return summary(_row(c, space, path))


def delete(c, space, path, author, *, keep_history=True):
    """Remove a page; its last version stays in history so it can be restored."""
    path = clean_path(path)
    old = _row(c, space, path)
    if old is None:
        raise MemoryError('No such wiki page', 404)
    if keep_history:
        _keep(c, space, path, old['revision'], old['body'], old['updated_at'], old['updated_by'])
        # A deletion is a revision too, so the history shows who removed the page.
        _keep(c, space, path, old['revision'] + 1, None, now(), author)
    c.execute('DELETE FROM wiki_pages WHERE space=? AND path=?', (space, path))
    return {'status': 'deleted', 'path': path}


def history(c, space, path):
    path = clean_path(path)
    found = c.execute('SELECT revision,updated_at,updated_by,body FROM wiki_history '
                      'WHERE space=? AND path=? ORDER BY revision DESC', (space, path)).fetchall()
    return {'path': path, 'versions': [{'revision': r['revision'], 'updated_at': r['updated_at'],
                                        'updated_by': r['updated_by'], 'deleted': r['body'] is None} for r in found]}


def _keep(c, space, path, revision, body, at, author):
    c.execute('INSERT OR REPLACE INTO wiki_history VALUES (?,?,?,?,?,?)', (space, path, revision, body, at, author))
    c.execute('DELETE FROM wiki_history WHERE space=? AND path=? AND revision NOT IN ('
              'SELECT revision FROM wiki_history WHERE space=? AND path=? ORDER BY revision DESC LIMIT ?)',
              (space, path, space, path, HISTORY_PER_PAGE))


def search(c, space, query, limit=SEARCH_RESULTS):
    """Pages whose path or text contains the query's words, best first, each with a short excerpt."""
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise MemoryError('query must be a nonempty string up to 2000 characters')
    words = [w for w in query.lower().split() if len(w) > 1][:20] or [query.lower().strip()]
    found = []
    for row in rows(c, space):
        text = _text(row['body'])
        if text is None:
            continue
        haystack, name = text.lower(), row['path'].lower()
        score = sum(haystack.count(w) + 5 * name.count(w) for w in words)
        if score:
            at = min((haystack.find(w) for w in words if w in haystack), default=0)
            excerpt = ' '.join(text[max(0, at - 120):at + 240].split())
            found.append((score, {**summary(row), 'excerpt': excerpt}))
    found.sort(key=lambda item: (-item[0], item[1]['path']))
    return {'pages': [page for _, page in found[:limit]]}


def dispatch(c, space, operation, payload, author):
    """The agent-facing operations, called by Store.dispatch after the space is authorized."""
    agent = payload.get('agent')
    if agent is not None:
        if not isinstance(agent, str) or not agent.strip() or len(agent) > 100:
            raise MemoryError('agent must be a short name')
        author = f'{author}/{agent.strip()}'
    if operation == 'wiki_list':
        return {'pages': list_pages(c, space, payload.get('prefix', ''))}
    if operation == 'wiki_read':
        return read(c, space, payload.get('path'))
    if operation == 'wiki_search':
        return search(c, space, payload.get('query'))
    if operation == 'wiki_history':
        return history(c, space, payload.get('path'))
    if operation == 'wiki_write':
        content = payload.get('content')
        if not isinstance(content, str) or not content.strip():
            raise MemoryError('content must be nonempty text')
        revision = payload.get('base_revision')
        if revision is not None and type(revision) is not int:
            raise MemoryError('base_revision must be an integer')
        return write(c, space, payload.get('path'), redact(content), author, revision)
    if operation == 'wiki_delete':
        return delete(c, space, payload.get('path'), author)
    raise MemoryError('Unknown operation', 404)
