"""Keep the three public agent setup prompts identical in README.md and site/index.html.

The canonical text is docs/agent-setup-prompts/*.txt. Run with --write to copy it into the
README fenced blocks and the site <code> blocks; run without arguments to verify.
"""
from html import escape, unescape
from pathlib import Path
import re, sys

root = Path(__file__).resolve().parents[1]
NAMES = ('mcp', 'cli', 'hermes')
canonical = {n: (root / 'docs/agent-setup-prompts' / f'{n}.txt').read_text(encoding='utf-8').rstrip('\n') for n in NAMES}
readme_path, site_path = root / 'README.md', root / 'site/index.html'

README_RE = '(<!-- agent-prompt:{n} -->\n```text\n)(.*?)(\n```\n<!-- /agent-prompt:{n} -->)'
SITE_RE = '(<code id="prompt-{n}">)(.*?)(</code>)'
PLACEHOLDERS = ('YOUR_CLOUD_URL', 'EXPECTED_VAULT_ID', 'PASTE_FRESH_INVITE_CODE')


def pattern(template, n):
    return re.compile(template.format(n=n), re.S)


def write():
    readme, site = readme_path.read_text(encoding='utf-8'), site_path.read_text(encoding='utf-8')
    for n in NAMES:
        readme, a = pattern(README_RE, n).subn(lambda m: m[1] + canonical[n] + m[3], readme)
        site, b = pattern(SITE_RE, n).subn(lambda m: m[1] + escape(canonical[n], quote=False) + m[3], site)
        assert a == 1 and b == 1, f'missing block for {n}'
    readme_path.write_text(readme, encoding='utf-8')
    site_path.write_text(site, encoding='utf-8')


def check():
    readme, site = readme_path.read_text(encoding='utf-8'), site_path.read_text(encoding='utf-8')
    for n in NAMES:
        text = canonical[n]
        found = pattern(README_RE, n).findall(readme)
        assert len(found) == 1, f'README must hold exactly one {n} block'
        assert found[0][1] == text, f'README {n} prompt differs from docs/agent-setup-prompts/{n}.txt'
        found = pattern(SITE_RE, n).findall(site)
        assert len(found) == 1, f'site must hold exactly one {n} block'
        assert unescape(found[0][1]) == text, f'site {n} prompt differs from docs/agent-setup-prompts/{n}.txt'
        for token in PLACEHOLDERS:
            assert token in text, f'{n} prompt lost placeholder {token}'
        assert '```' not in text, f'{n} prompt would break the README fence'
        assert '\u2014' not in text, f'{n} prompt contains an em dash'
        # Nothing private: no concrete worker origin, vault ID, invitation or device ID.
        assert not re.search(r'https://[\w.-]+\.workers\.dev|vault_[0-9a-f]{8}|device_[0-9a-f]{6}|[0-9a-f]{32}', text), f'{n} prompt holds a concrete identifier'
        assert not re.search(r'/Users/|/home/|/opt/data|/srv/hermes|Colt|promptclickrun\.github\.io', text, re.I), f'{n} prompt holds a personal or host-specific value'
    for n in ('mcp', 'cli'):
        assert 'hermes' not in canonical[n].lower(), f'{n} prompt must stay host-neutral'
    assert 'Streamable HTTP' in canonical['mcp'] and '/v1/devices/join' in canonical['mcp'] and '/mcp' in canonical['mcp']
    assert 'Authorization: Bearer' in canonical['mcp'] and 'legacy SSE' in canonical['mcp']
    assert 'BIGFEELS_PAIRING_SECRET' in canonical['cli'] and '--remote' in canonical['cli'] and '--secret-env' in canonical['cli']
    assert 'memory.provider = bigfeels' in canonical['hermes'] and 'memory.bigfeels.auto_extract = false' in canonical['hermes']
    # Hermes keeps the plugin out of its environment (python_runtime: external): no bigfeels-mem on PATH.
    assert 'hermes bigfeels --data-dir DIRECTORY cloud join' in canonical['hermes'] and 'bigfeels-mem --data-dir' not in canonical['hermes']
    print('agent prompts: README and site match docs/agent-setup-prompts for', ', '.join(NAMES))


if __name__ == '__main__':
    write() if sys.argv[1:] == ['--write'] else check()
