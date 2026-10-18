"""Bundle the canonical engine for Python Workers without maintaining a fork."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent.parent
out = root / 'cloud' / 'src' / 'bigfeels_mem'
if out.exists():
    shutil.rmtree(out)
out.mkdir()
for name in ('__init__', 'store', 'schema', 'retrieval', 'privacy', 'sync', 'client', 'mcp', 'cloud_service', 'cloud_oauth', 'cloud_rerank'):
    source = root / 'src' / 'bigfeels_mem' / (name + '.py')
    if source.exists():
        shutil.copy2(source, out / source.name)
