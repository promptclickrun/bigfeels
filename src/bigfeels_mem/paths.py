"""Shared local storage location across host adapters."""
import os
from pathlib import Path
import sys


def default_data_dir():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'bigfeels-mem'
    if os.name == 'nt' and os.environ.get('APPDATA'):
        return Path(os.environ['APPDATA']) / 'bigfeels-mem'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'bigfeels-mem'
