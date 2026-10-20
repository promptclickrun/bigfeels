"""Copy the canonical engine into cloud/ so the Worker deploys from this folder alone.

Deploy to Cloudflare treats `cloud/` as the root of a new repository, so the engine copy under
`cloud/src/bigfeels_mem/` is committed. Run this after changing the engine; `--check` (used in CI)
fails when the copy is stale.
"""
from pathlib import Path
import filecmp
import shutil
import sys

root = Path(__file__).resolve().parent.parent
out = root / 'cloud' / 'src' / 'bigfeels_mem'
MODULES = ('__init__', 'store', 'schema', 'retrieval', 'privacy', 'sync', 'client', 'mcp',
           'cloud_service', 'cloud_oauth', 'cloud_rerank')
sources = [root / 'src' / 'bigfeels_mem' / (name + '.py') for name in MODULES]
sources = [path for path in sources if path.exists()]

if '--check' in sys.argv:
    stale = [p.name for p in sources if not (out / p.name).exists() or not filecmp.cmp(p, out / p.name, shallow=False)]
    stale += [p.name for p in out.glob('*.py') if p.name not in {s.name for s in sources}] if out.exists() else []
    if stale:
        sys.exit('cloud/src/bigfeels_mem is out of date (' + ', '.join(sorted(set(stale))) + '); run python3 cloud/build.py')
    print('engine copy is current')
else:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    for source in sources:
        shutil.copy2(source, out / source.name)
