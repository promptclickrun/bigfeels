"""The shared wiki over WebDAV, so Obsidian (Remotely Save), Finder and Explorer read and edit it directly.

    /dav/<vault name>/<page path>              a login with one space: the vault is that space's wiki
    /dav/<vault name>/<space>/<page path>      a login with several spaces: one folder per space

Sync plugins put a vault inside a folder named after it (Remotely Save uses `/<vault name>/`), so the first
folder is always that wrapper, whatever it is called. Below it is the wiki exactly as stored: `SCHEMA.md`,
`index.md`, `log.md`, `entities/...`, attachments. Agents edit the same pages through the `wiki_*`
operations, so the owner and every agent see one copy.

The atomic memories agents search are not files: 11,000 one-line records are not a wiki. Agents compile
them into pages.

WebDAV writes win, since a client cannot say which version it edited. Every replaced or deleted page stays
in the page history (last 20 versions), so nothing a sync plugin overwrites is lost. Deleting many pages at
once is throttled, because a plugin turns a local folder delete into a remote one.
"""
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote, unquote

from . import pages
from .store import MemoryError, now

SCRATCH_FILES = 500
MAX_SCRATCH_BYTES = 64_000
MAX_UPLOAD_BYTES = pages.MAX_PAGE_BYTES
DELETES_PER_MINUTE = 60
# Files kept without history: Obsidian's settings change constantly, and sync plugins write throwaway test files.
PRIVATE_PREFIXES = ('.obsidian/', '.trash/', 'rs-test-folder-')
# A file in the old one-file-per-memory layout. Phones that synced it would push 11,000 of them back as pages.
OLD_MEMORY_FILE = b'---\nid: "mem_'
XML = {'Content-Type': 'application/xml; charset=utf-8'}
TYPES = {'md': 'text/markdown; charset=utf-8', 'txt': 'text/plain; charset=utf-8', 'json': 'application/json',
         'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'svg': 'image/svg+xml',
         'pdf': 'application/pdf', 'canvas': 'application/json'}


def escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def http_date(stamp):
    try:
        moment = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        moment = datetime.fromtimestamp(0, timezone.utc)
    return format_datetime(moment.astimezone(timezone.utc), usegmt=True)


def content_type(path):
    return TYPES.get(path.rsplit('.', 1)[-1].lower() if '.' in path else '', 'application/octet-stream')


def multistatus(entries):
    return 207, '<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">' + ''.join(entries) + '</D:multistatus>', XML


class Files:
    def __init__(self, store, service):
        self.store, self.service = store, service

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
        self.author = 'files:' + device['name']
        method = method.upper()
        allow = {'DAV': '1', 'Allow': 'OPTIONS, PROPFIND, GET, HEAD, PUT, DELETE, MKCOL', 'MS-Author-Via': 'DAV'}
        if method == 'OPTIONS':
            return 200, '', allow
        if not parts:
            # The address itself: one folder to open. Remotely Save never lists this; Finder and Explorer do.
            self.base = '/dav'
            if method == 'PROPFIND':
                return multistatus([self._entry('/dav/', True)] +
                                   ([self._entry('/dav/wiki/', True)] if (headers.get('Depth') or 'infinity') != '0' else []))
            return (201, '', {}) if method == 'MKCOL' else (405, 'Open a folder below this address', allow)
        self.base = '/dav/' + quote(parts.pop(0), safe='')
        if len(spaces) == 1:
            return self.wiki(method, spaces[0], self.base, '/'.join(parts), headers, body, device)
        if not parts:
            if method == 'PROPFIND':
                return self.root(spaces, headers.get('Depth') or 'infinity')
            return (201, '', {}) if method == 'MKCOL' else (405, 'Open a space folder', allow)
        if parts[0] in spaces:
            return self.wiki(method, parts[0], f'{self.base}/{parts[0]}', '/'.join(parts[1:]), headers, body, device)
        if method == 'PROPFIND':
            return self.scratch_propfind(parts, headers.get('Depth') or 'infinity')
        if method in ('GET', 'HEAD'):
            return self.scratch_get(parts, method == 'HEAD')
        if method == 'MKCOL':
            return self.scratch_mkcol(parts)
        if method == 'PUT':
            return self.scratch_put(parts, body)
        if method == 'DELETE':
            return self.scratch_delete(parts)
        return 405, 'Method not supported', allow

    def wiki(self, method, space, prefix, path, headers, body, device):
        """One request against a space's wiki; `prefix` is the href of that wiki's root folder."""
        self.prefix = prefix
        if method == 'PROPFIND':
            return self.propfind(space, path, headers.get('Depth') or 'infinity')
        if method in ('GET', 'HEAD'):
            return self.get(space, path, method == 'HEAD')
        if method == 'MKCOL':
            return self.mkcol(space, path)
        if method == 'PUT':
            return self.put(space, path, body)
        if method == 'DELETE':
            return self.delete(space, path, device)
        return 405, 'Method not supported', {'Allow': 'OPTIONS, PROPFIND, GET, HEAD, PUT, DELETE, MKCOL'}

    # -- entries ------------------------------------------------------------------------------------------

    def _entry(self, href, collection, size=0, modified=None, etag=None, kind=None):
        props = ('<D:resourcetype><D:collection/></D:resourcetype>' if collection else '<D:resourcetype/>')
        if not collection:
            props += (f'<D:getcontentlength>{size}</D:getcontentlength><D:getcontenttype>{kind}</D:getcontenttype>'
                      f'<D:getetag>"{etag}"</D:getetag>')
        if modified:
            props += f'<D:getlastmodified>{modified}</D:getlastmodified>'
        return (f'<D:response><D:href>{escape(quote(href))}</D:href><D:propstat><D:prop>{props}</D:prop>'
                '<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>')

    def _page_entry(self, space, page):
        return self._entry(f'{self.prefix}/{page["path"]}', False, page['size'], http_date(page['updated_at']),
                           f'{page["revision"]}-{page["size"]}', content_type(page['path']))

    def _tree(self, c, space):
        """(folders, pages) for a space: every folder that holds a page or was made empty, and every page."""
        found = pages.list_pages(c, space)
        folders = {row[0] for row in c.execute('SELECT path FROM wiki_folders WHERE space=?', (space,))}
        for page in found:
            segments = page['path'].split('/')[:-1]
            folders.update('/'.join(segments[:n]) for n in range(1, len(segments) + 1))
        return folders, found

    def root(self, spaces, depth):
        entries = [self._entry(self.base + '/', True)]
        if depth != '0':
            with self.store.connection(True) as c:
                for space in spaces:
                    entries.append(self._entry(f'{self.base}/{space}/', True))
                    self.prefix = f'{self.base}/{space}'
                    if depth == 'infinity':
                        # One request for the whole tree: far faster for a sync plugin than one per folder.
                        entries.extend(self._below(c, space, '', depth))
                entries.extend(self._scratch_entries(c, [], depth))
        return multistatus(entries)

    def _below(self, c, space, prefix, depth):
        folders, found = self._tree(c, space)
        start = prefix + '/' if prefix else ''
        entries = []
        for folder in sorted(folders):
            if folder.startswith(start) and folder != prefix and (depth == 'infinity' or '/' not in folder[len(start):]):
                entries.append(self._entry(f'{self.prefix}/{folder}/', True))
        for page in found:
            name = page['path']
            if name.startswith(start) and (depth == 'infinity' or '/' not in name[len(start):]):
                entries.append(self._page_entry(space, page))
        return entries

    # -- the wiki ----------------------------------------------------------------------------------------

    def propfind(self, space, path, depth):
        with self.store.connection(True) as c:
            if not path:
                entries = [self._entry(f'{self.prefix}/', True)]
                if depth != '0':
                    entries += self._below(c, space, '', depth)
                return multistatus(entries)
            path = pages.clean_path(path)
            stored = pages.raw(c, space, path)
            if stored:
                return multistatus([self._page_entry(space, stored[1])])
            folders, _ = self._tree(c, space)
            if path not in folders:
                return 404, '', {}
            entries = [self._entry(f'{self.prefix}/{path}/', True)]
            if depth != '0':
                entries += self._below(c, space, path, depth)
            return multistatus(entries)

    def get(self, space, path, head_only):
        if not path:
            return 404, '', {}
        with self.store.connection() as c:
            stored = pages.raw(c, space, pages.clean_path(path))
        if stored is None:
            return 404, 'No such file', {}
        body, page = stored
        return 200, b'' if head_only else body, {'Content-Type': content_type(path), 'ETag': f'"{page["revision"]}-{page["size"]}"',
                                                 'Last-Modified': http_date(page['updated_at'])}

    def mkcol(self, space, path):
        if path:
            with self.store.connection(True) as c:
                c.execute('INSERT OR IGNORE INTO wiki_folders VALUES (?,?)', (space, pages.clean_path(path)))
        return 201, '', {}

    def put(self, space, path, body):
        if not path:
            raise MemoryError('Choose a file name', 400)
        path = pages.clean_path(path)
        if len(body) > MAX_UPLOAD_BYTES:
            raise MemoryError('File exceeds 1 MB', 413)
        if body[:len(OLD_MEMORY_FILE)] == OLD_MEMORY_FILE and b'\nlineage: "mem_' in body[:300]:
            # A copy of a memory record from the old layout: the memory itself is still in the vault. Accept and
            # drop it, so the plugin then sees it gone remotely and removes the local copy.
            return 204, '', {}
        private = any((path + '/').startswith(p) for p in PRIVATE_PREFIXES)
        with self.store.connection(True) as c:
            existed = pages.raw(c, space, path) is not None
            # Obsidian's own settings change constantly; keeping 20 copies of each would only bloat the vault.
            pages.write(c, space, path, bytes(body), self.author, require_revision=False, keep_history=not private)
        return (204 if existed else 201), '', {}

    def delete(self, space, path, device):
        if not path:
            raise MemoryError('A whole space cannot be deleted from a file client', 403)
        path = pages.clean_path(path)
        with self.store.connection(True) as c:
            doomed = [p['path'] for p in pages.list_pages(c, space) if p['path'] == path or p['path'].startswith(path + '/')]
            folders = c.execute('SELECT path FROM wiki_folders WHERE space=? AND (path=? OR path LIKE ?)',
                                (space, path, path + '/%')).fetchall()
            if not doomed and not folders:
                return 404, '', {}
        # A sync plugin that sees a folder vanish locally deletes remotely too. Slow that down; history keeps every page.
        if len(doomed) > 1:
            self.service.limit('dav-delete:' + device['id'], DELETES_PER_MINUTE)
        with self.store.connection(True) as c:
            for name in doomed:
                pages.delete(c, space, name, self.author, keep_history=not any((name + '/').startswith(p) for p in PRIVATE_PREFIXES))
            c.execute('DELETE FROM wiki_folders WHERE space=? AND (path=? OR path LIKE ?)', (space, path, path + '/%'))
        return 204, '', {}

    # -- scratch area: files above the spaces ------------------------------------------------------------

    def _scratch_rows(self, c):
        c.execute('CREATE TABLE IF NOT EXISTS dav_scratch (path TEXT PRIMARY KEY, body BLOB, modified TEXT)')
        return {r[0]: (r[1] or b'', r[2]) for r in c.execute('SELECT path, body, modified FROM dav_scratch')}

    def _scratch_entries(self, c, prefix, depth):
        """PROPFIND entries below `prefix` (a list of path segments): immediate children, or everything."""
        rows, seen, entries = self._scratch_rows(c), set(), []
        for key in sorted(rows):
            marker = key.endswith('/')
            segments = key.rstrip('/').split('/')
            if segments[:len(prefix)] != prefix or len(segments) <= len(prefix):
                continue
            last = len(prefix) + 1 if depth != 'infinity' else len(segments)
            for cut in range(len(prefix) + 1, min(len(segments), last) + 1):
                path = '/'.join(segments[:cut])
                is_file = cut == len(segments) and not marker
                if (path, is_file) in seen:
                    continue
                seen.add((path, is_file))
                href = f'{self.base}/{path}' + ('' if is_file else '/')
                if is_file:
                    body, modified = rows[key]
                    entries.append(self._entry(href, False, len(body), http_date(modified), f'{len(body)}-{modified}',
                                               content_type(path)))
                else:
                    entries.append(self._entry(href, True))
        return entries

    def scratch_propfind(self, parts, depth):
        path = '/'.join(parts)
        with self.store.connection(True) as c:
            rows = self._scratch_rows(c)
            if path in rows:
                body, modified = rows[path]
                one = [self._entry(f'{self.base}/{path}', False, len(body), http_date(modified), f'{len(body)}-{modified}',
                                   content_type(path))]
            elif path + '/' in rows or any(k.startswith(path + '/') for k in rows):
                one = [self._entry(f'{self.base}/{path}/', True)]
                if depth != '0':
                    one += self._scratch_entries(c, parts, depth)
            else:
                return 404, '', {}
        return multistatus(one)

    def scratch_get(self, parts, head_only):
        with self.store.connection(True) as c:
            row = self._scratch_rows(c).get('/'.join(parts))
        if row is None:
            return 404, 'No such file', {}
        body, modified = row
        return 200, b'' if head_only else bytes(body), {'Content-Type': content_type(parts[-1]),
                                                        'ETag': f'"{len(body)}-{modified}"', 'Last-Modified': http_date(modified)}

    def scratch_mkcol(self, parts):
        with self.store.connection(True) as c:
            if len(self._scratch_rows(c)) >= SCRATCH_FILES:
                raise MemoryError('Too many files outside the wiki', 413)
            c.execute('INSERT OR IGNORE INTO dav_scratch VALUES (?,?,?)', ('/'.join(parts) + '/', b'', now()))
        return 201, '', {}

    def scratch_put(self, parts, body):
        if not parts:
            raise MemoryError('Choose a file name', 400)
        if len(body) > MAX_SCRATCH_BYTES:
            raise MemoryError('File is too large to keep outside the wiki', 413)
        with self.store.connection(True) as c:
            rows = self._scratch_rows(c)
            path = '/'.join(parts)
            if path not in rows and len(rows) >= SCRATCH_FILES:
                raise MemoryError('Too many files outside the wiki', 413)
            c.execute('INSERT OR REPLACE INTO dav_scratch VALUES (?,?,?)', (path, body, now()))
        return 201 if path not in rows else 204, '', {}

    def scratch_delete(self, parts):
        path = '/'.join(parts)
        with self.store.connection(True) as c:
            doomed = [k for k in self._scratch_rows(c) if k.rstrip('/') == path or k.startswith(path + '/')]
            for key in doomed:
                c.execute('DELETE FROM dav_scratch WHERE path=?', (key,))
        return (204 if doomed else 404), '', {}
