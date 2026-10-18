"""Deterministic Obsidian projection. Only files owned by our manifest are changed."""
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .store import Principal

MANIFEST = '.bigfeels-manifest.json'


def _hash(content):
    return hashlib.sha256(content.encode()).hexdigest()


def export_wiki(store, vault, spaces=('owner',), include_evidence=False):
    destination = Path(vault).expanduser() / 'Bigfeels'
    if destination.is_symlink():
        raise ValueError('Wiki destination cannot be a symlink')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = destination / MANIFEST
    if manifest.is_symlink():
        raise ValueError('Wiki manifest cannot be a symlink')
    old = json.loads(manifest.read_text()) if manifest.exists() else {}
    if not isinstance(old, dict) or any(Path(k).name != k or not k.endswith('.md') for k in old):
        raise ValueError('Invalid wiki manifest')
    bundle = store.dispatch(Principal('wiki', tuple(spaces)), 'export', {})
    groups = defaultdict(list)
    for memory in bundle['memories']:
        groups[(memory['space'], memory['kind'], memory['key'] or 'Unkeyed')].append(memory)
    supports = defaultdict(list)
    for edge in bundle['supports']:
        supports[edge['memory_id']].append(edge['evidence_id'])
    files, index = {}, ['# Bigfeels memory\n', 'Generated from memory. Edit the source through Bigfeels; wiki edits do not sync back.\n']
    for key, memories in sorted(groups.items()):
        title = ' / '.join(key)
        filename = 'topic-' + _hash(json.dumps(key))[:24] + '.md'
        metadata = {'space': key[0], 'kind': key[1], 'key': key[2], 'memory_ids': sorted(m['id'] for m in memories)}
        lines = ['---\n' + '\n'.join(k + ': ' + json.dumps(v, ensure_ascii=False) for k, v in metadata.items()) + '\n---\n', '# ' + title + '\n']
        for m in sorted(memories, key=lambda r: (r['valid_from'], r['id'])):
            lines.extend(['## ' + m['id'] + '\n', m['content'] + '\n',
                          f"Status: {m['status']}; revision: {m['revision']}; valid: {m['valid_from']} to {m['valid_until'] or 'open'}.\n",
                          'Evidence: ' + ', '.join('[[evidence-' + _hash(e)[:24] + '|' + e + ']]' for e in sorted(supports[m['id']])) + '\n'])
        files[filename] = '\n'.join(lines)
        index.append('- [[' + filename[:-3] + '|' + title.replace('|', '/') + ']]')
    for e in bundle['evidence']:
        content = e['content'] if include_evidence and e['content'] is not None else 'Source text omitted or expired.'
        files['evidence-' + _hash(e['id'])[:24] + '.md'] = (
            '---\nid: ' + json.dumps(e['id']) + '\nspace: ' + json.dumps(e['space']) +
            '\nexpires_at: ' + json.dumps(e['expires_at']) + '\n---\n\n# ' + e['id'] + '\n\n' + content + '\n')
    files['Index.md'] = '\n'.join(index) + '\n'
    # Check the entire write/delete set before touching any existing file.
    for name in set(old) | set(files):
        path = destination / name
        if path.is_symlink() or (path.exists() and (name not in old or _hash(path.read_text()) != old[name])):
            raise ValueError('Wiki contains a manually changed or unrelated file: ' + name)
    lock = destination / '.bigfeels-export.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        def write(path, content):
            fd, temp = tempfile.mkstemp(dir=destination, prefix='.bigfeels-')
            try:
                with os.fdopen(fd, 'w') as output:
                    output.write(content)
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        for name, content in files.items():
            if old.get(name) != _hash(content) or not (destination / name).exists():
                write(destination / name, content)
        for name in old.keys() - files.keys():
            (destination / name).unlink(missing_ok=True)
        write(manifest, json.dumps({k: _hash(v) for k, v in files.items()}, sort_keys=True))
    finally:
        lock.unlink()
    return {'status': 'exported', 'directory': str(destination), 'pages': len(files),
            'plaintext_copy': True, 'includes_evidence_text': include_evidence}
