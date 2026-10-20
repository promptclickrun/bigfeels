"""The vault as a folder of markdown files over WebDAV, so Obsidian and file managers can read and edit it.

Layout:  /dav/<space>/<topic>/<name>--<id12>.md

* One file per current memory (active or disputed). `topic` is the first part of the memory's key,
  lowercased; `name` comes from the key, or from the first words of an unkeyed memory.
* `id12` is the first 12 hex digits of the lineage's first memory. A correction makes a new memory,
  but the file keeps its name and shows the newest version, so an edit never renames a file.
* The folder and name are cosmetic: the file is found by `id12` alone, so moving or renaming it is harmless.

Writes go through the same engine operations an agent uses, so they keep history, redaction and sync:
  PUT an existing file    -> correct (the frontmatter `id` guards against a stale copy)
  PUT a new .md file      -> remember
  DELETE a file           -> forget, including the sources behind it (throttled)
Only the body is editable. Keep `id` in the frontmatter so a stale copy is refused; `kind` and `key` apply to new files.
"""
import json
import re
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote, unquote

from .store import MemoryError, Principal

KINDS = ('fact', 'preference', 'decision', 'episode', 'procedure', 'task')
SHOWN = ('active', 'disputed')
LISTING_SECONDS = 5
MAX_FILE_BYTES = 64_000
DELETES_PER_MINUTE = 20
MARKDOWN = re.compile(r'\.md$', re.I)
FILE_ID = re.compile(r'--([0-9a-f]{12})\.md$')
XML = {'Content-Type': 'application/xml; charset=utf-8'}


def clean(text, limit):
    """A safe path segment: ASCII letters, digits, dash and underscore only."""
    text = re.sub(r'[^A-Za-z0-9_-]+', '-', text or '').strip('-')
    return text[:limit].strip('-')


def topic_of(key):
    first = re.split(r'[.:/]', key or '')[0] if key else ''
    return clean(first, 40).lower() or '_unkeyed'


def file_name(root):
    label = clean(root['key'], 60) if root['key'] else clean(' '.join(root['content'].split()[:8]), 50)
    return (label or 'note') + '--' + root['id'][4:16] + '.md'


def escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def http_date(stamp):
    try:
        moment = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        moment = datetime.fromtimestamp(0, timezone.utc)
    return format_datetime(moment.astimezone(timezone.utc), usegmt=True)


def render(m):
    """The markdown file for one memory. Strings are JSON-quoted, which is valid YAML."""
    head = [('id', m['id']), ('lineage', m['lineage']), ('revision', m['revision']), ('kind', m['kind']),
            ('status', m['status']), ('basis', m['basis']), ('key', m['key']), ('space', m['space']),
            ('recorded', m['recorded_at']), ('valid_from', m['valid_from'])]
    lines = [f'{name}: {json.dumps(value, ensure_ascii=False)}' for name, value in head]
    return '---\n' + '\n'.join(lines) + '\n---\n' + m['content'].strip('\n') + '\n'


def parse(text):
    """Split an uploaded file into (frontmatter fields, body). Unreadable frontmatter counts as none."""
    text = text.replace('\r\n', '\n').lstrip('\ufeff')
    fields, body = {}, text
    if text.startswith('---\n'):
        end = text.find('\n---\n', 3)
        if end > 0:
            body = text[end + 5:]
            for line in text[4:end].split('\n'):
                name, sep, value = line.partition(':')
                if not sep or not name.strip().isidentifier():
                    continue
                value = value.strip()
                try:
                    fields[name.strip()] = json.loads(value)
                except ValueError:
                    fields[name.strip()] = value
    return fields, body.strip('\n')


class Files:
    def __init__(self, store, service):
        self.store, self.service = store, service
        self.cache = {}

    # -- reading the vault as files -------------------------------------------------------------

    def _lineages(self, c, spaces):
        """Every current memory as a file: {root_id: memory}, built from two queries."""
        marks = ','.join('?' for _ in spaces)
        rows = {}
        for row in c.execute(f"SELECT * FROM memories WHERE space IN ({marks}) AND (status IN ('active','disputed') "
                             "OR id IN (SELECT target_id FROM relations WHERE kind='supersedes'))", tuple(spaces)):
            rows[row['id']] = dict(row)
        older = {}
        for row in c.execute("SELECT source_id,target_id FROM relations WHERE kind='supersedes'"):
            older[row[0]] = row[1]
        files = {}
        for mid, memory in rows.items():
            if memory['status'] not in SHOWN:
                continue
            root, seen = mid, {mid}
            while root in older and older[root] not in seen:
                root = older[root]
                seen.add(root)
            first = rows.get(root)
            memory['lineage'] = root
            files[root] = (memory, first or memory)
        return files

    def _listing(self, c, space):
        """`_lineages` for one space, reused for a few seconds. A sync plugin on depth 1 lists one folder at a
        time (thousands of requests in a row) and each would otherwise rescan the whole space. Our own writes
        clear it; an agent's save shows up within the window."""
        cached = self.cache.get(space)
        if cached and time.time() - cached[0] < LISTING_SECONDS:
            return cached[1]
        files = self._lineages(c, [space])
        self.cache[space] = (time.time(), files)
        return files

    def _find(self, c, spaces, stem):
        """The (current memory, first memory) pair for an id12, or None."""
        low, high = 'mem_' + stem, 'mem_' + stem + 'g'
        root = c.execute('SELECT * FROM memories WHERE id>=? AND id<?', (low, high)).fetchone()
        if root is None or root['space'] not in spaces:
            return None
        head, seen = dict(root), {root['id']}
        while True:
            nxt = c.execute("SELECT m.* FROM relations r JOIN memories m ON m.id=r.source_id "
                            "WHERE r.target_id=? AND r.kind='supersedes'", (head['id'],)).fetchone()
            if nxt is None or nxt['id'] in seen:
                break
            head = dict(nxt)
            seen.add(head['id'])
        if head['status'] not in SHOWN:
            return None
        head['lineage'] = root['id']
        return head, dict(root)

    # -- WebDAV ---------------------------------------------------------------------------------

    def handle(self, method, path, headers, body, origin):
        """Serve one request. Returns (status, body, headers); body is str or bytes."""
        auth = headers.get('Authorization') or ''
        if auth.startswith('Basic '):
            import base64
            try:
                token = base64.b64decode(auth[6:]).decode().partition(':')[2]
            except ValueError:
                token = ''
        else:
            token = auth.removeprefix('Bearer ')
        try:
            device = self.service.authenticate(token)
        except MemoryError as error:
            if error.status != 401:
                raise
            return 401, 'Sign in with a bigfeels device token as the password', {
                'WWW-Authenticate': 'Basic realm="bigfeels", charset="UTF-8"'}
        spaces = tuple(device['spaces'])
        parts = [unquote(p) for p in path.removeprefix('/dav').split('/') if p]
        if any(p in ('.', '..') or '\\' in p or '\x00' in p for p in parts):
            raise MemoryError('Invalid path', 400)
        # Sync plugins keep a vault inside a folder named after it (Remotely Save: /dav/<vault name>/).
        # A first folder that is not a space is that wrapper: the tree below it is the same one.
        self.base = '/dav'
        if parts and parts[0] not in spaces:
            self.base += '/' + quote(parts.pop(0), safe='')
        method = method.upper()
        if method == 'OPTIONS':
            return 200, '', {'DAV': '1', 'Allow': 'OPTIONS, PROPFIND, GET, HEAD, PUT, DELETE, MKCOL',
                             'MS-Author-Via': 'DAV'}
        if method == 'PROPFIND':
            return self.propfind(parts, (headers.get('Depth') or 'infinity'), spaces, origin)
        if method in ('GET', 'HEAD'):
            return self.get(parts, spaces, method == 'HEAD')
        if method == 'MKCOL':
            # Folders are implied by the memories inside them, so making one only has to say yes.
            return (201 if not parts or parts[0] in spaces else 403), '', {}
        if method == 'PUT':
            return self.put(parts, body, device, spaces)
        if method == 'DELETE':
            return self.delete(parts, device, spaces)
        return 405, 'Method not supported', {'Allow': 'OPTIONS, PROPFIND, GET, HEAD, PUT, DELETE, MKCOL'}

    def _entry(self, href, collection, size=0, modified=None, etag=None):
        props = ('<D:resourcetype><D:collection/></D:resourcetype>' if collection else '<D:resourcetype/>')
        if not collection:
            props += (f'<D:getcontentlength>{size}</D:getcontentlength><D:getcontenttype>text/markdown</D:getcontenttype>'
                      f'<D:getetag>"{etag}"</D:getetag>')
        if modified:
            props += f'<D:getlastmodified>{modified}</D:getlastmodified>'
        return (f'<D:response><D:href>{escape(quote(href))}</D:href><D:propstat><D:prop>{props}</D:prop>'
                '<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>')

    def propfind(self, parts, depth, spaces, origin):
        if parts and parts[0] not in spaces:
            return 404, '', {}
        base = self.base
        entries = []
        with self.store.connection() as c:
            if not parts:
                entries.append(self._entry(base + '/', True))
                if depth != '0':
                    entries.extend(self._entry(f'{base}/{s}/', True) for s in spaces)
                return 207, '<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">' + ''.join(entries) + '</D:multistatus>', XML
            files = self._listing(c, parts[0])
        by_topic = {}
        for memory, first in files.values():
            by_topic.setdefault(topic_of(memory['key'] or first['key']), []).append((memory, first))
        wanted_topic = parts[1].lower() if len(parts) > 1 and not FILE_ID.search(parts[1]) else None
        if len(parts) > 2 and not FILE_ID.search(parts[-1]):
            return 404, '', {}
        if len(parts) > 1 and FILE_ID.search(parts[-1]):
            found = self._file_for(files, parts[-1])
            if not found:
                return 404, '', {}
            memory, first = found
            text = render(memory).encode()
            entries.append(self._entry(f'{base}/{parts[0]}/{topic_of(memory["key"] or first["key"])}/{file_name(first)}',
                                       False, len(text), http_date(memory['recorded_at']), memory['id'][4:16] + '-' + str(memory['revision'])))
        else:
            self_href = f'{base}/{"/".join(parts)}/'.replace('//', '/')
            entries.append(self._entry(self_href, True))
            if depth != '0':
                topics = [wanted_topic] if wanted_topic else sorted(by_topic)
                for topic in topics:
                    by_topic.setdefault(topic, [])
                    if not wanted_topic:
                        entries.append(self._entry(f'{base}/{parts[0]}/{topic}/', True))
                    if wanted_topic or depth == 'infinity':
                        for memory, first in by_topic[topic]:
                            text = render(memory).encode()
                            entries.append(self._entry(f'{base}/{parts[0]}/{topic}/{file_name(first)}', False, len(text),
                                           http_date(memory['recorded_at']), memory['id'][4:16] + '-' + str(memory['revision'])))
        return 207, '<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">' + ''.join(entries) + '</D:multistatus>', XML

    def _file_for(self, files, name):
        match = FILE_ID.search(name)
        if not match:
            return None
        for root, pair in files.items():
            if root[4:16] == match.group(1):
                return pair
        return None

    def _located(self, c, parts, spaces):
        if len(parts) < 2 or parts[0] not in spaces:
            return None
        match = FILE_ID.search(parts[-1])
        return self._find(c, spaces, match.group(1)) if match else None

    def get(self, parts, spaces, head_only):
        with self.store.connection() as c:
            found = self._located(c, parts, spaces)
        if not found:
            return 404, 'No such memory file', {}
        memory, first = found
        text = render(memory)
        return 200, '' if head_only else text, {
            'Content-Type': 'text/markdown; charset=utf-8',
            'ETag': f'"{memory["id"][4:16]}-{memory["revision"]}"', 'Last-Modified': http_date(memory['recorded_at'])}

    def put(self, parts, body, device, spaces):
        if len(parts) < 2 or parts[0] not in spaces or not parts[-1].lower().endswith('.md') or parts[-1].startswith('.'):
            raise MemoryError('Only markdown files inside a space can be saved', 403)
        if len(parts) > 3:
            raise MemoryError('Memory files live one folder deep: <space>/<topic>/<file>.md', 400)
        if len(body) > MAX_FILE_BYTES:
            raise MemoryError('File is too large for one memory', 413)
        try:
            fields, text = parse(body.decode('utf-8'))
        except UnicodeDecodeError:
            raise MemoryError('File must be UTF-8 text') from None
        if not text.strip():
            raise MemoryError('A memory cannot be empty; delete the file to forget it', 400)
        principal = Principal(device['id'], tuple(device['spaces']))
        self.cache.clear()
        with self.store.connection() as c:
            found = self._located(c, parts, spaces)
        if found:
            memory, _ = found
            if text.strip() == memory['content'].strip():
                return 204, '', {}
            # Each correction is a new memory with a new id, so the id in the file's frontmatter says which
            # version the editor started from. A copy of an older version must not overwrite a newer one.
            if 'id' in fields and fields['id'] != memory['id']:
                raise MemoryError('This memory changed after your copy was saved; reload the file', 409)
            self.store.dispatch(principal, 'correct', {'id': memory['id'], 'revision': memory['revision'], 'content': text})
            return 204, '', {}
        kind = fields.get('kind') if fields.get('kind') in KINDS else 'fact'
        payload = {'space': parts[0], 'content': text, 'kind': kind}
        if isinstance(fields.get('key'), str) and fields['key'].strip():
            payload['key'] = fields['key'].strip()
        else:
            # No key in the file: the path becomes the key, so the note keeps its folder when it comes back.
            stem = clean(MARKDOWN.sub('', parts[-1]), 80)
            topic = clean(parts[1], 40) if len(parts) > 2 else ''
            payload['key'] = stem if topic in ('', '_unkeyed') else topic + '.' + stem
        self.store.dispatch(principal, 'remember', payload)
        return 201, '', {}

    def delete(self, parts, device, spaces):
        with self.store.connection() as c:
            found = self._located(c, parts, spaces)
        if not found:
            return 404, '', {}
        self.cache.clear()
        # A sync plugin that sees a folder vanish locally deletes remotely too. Slow that down.
        self.service.limit('dav-delete:' + device['id'], DELETES_PER_MINUTE)
        principal = Principal(device['id'], tuple(device['spaces']))
        preview = self.store.dispatch(principal, 'forget_preview', {'id': found[0]['id']})
        self.store.dispatch(principal, 'forget', {'id': found[0]['id'], 'plan_token': preview['plan_token']})
        return 204, '', {}
