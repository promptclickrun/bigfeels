"""One shared bigfeels store for every Hermes profile on this host.

Profiles keep separate stores by default, which is the private choice. `setup` makes the other choice
explicit: it initializes one data directory and points each profile's `memory.bigfeels.path` at it with
Hermes' own `config set`, so no config file is edited by hand. Without --apply it only prints the commands.
"""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from . import cloud
from .store import Store


def _profiles(home):
    found = ['default']
    root = Path(home) / 'profiles'
    if root.is_dir():
        found += sorted(p.name for p in root.iterdir() if (p / 'config.yaml').is_file())
    return found


def run_setup(data_dir, args):
    home = Path(args.hermes_home or os.environ.get('HERMES_HOME') or Path.home() / '.hermes').expanduser()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A directory paired in cloud-only mode holds no database; creating one would shadow the vault.
    if not cloud.is_remote(data_dir):
        Store(data_dir / 'memory.sqlite')
    profiles = args.profiles or _profiles(home)
    hermes = shutil.which('hermes') or 'hermes'
    commands = []
    for profile in profiles:
        base = [hermes] + ([] if profile == 'default' else ['-p', profile]) + ['config', 'set']
        commands.append(base + ['memory.provider', 'bigfeels'])
        commands.append(base + ['memory.bigfeels.path', str(data_dir)])
    result = {'status': 'ready', 'data_dir': str(data_dir), 'profiles': profiles,
              'commands': [shlex.join(c) for c in commands]}
    if not args.apply:
        result['next'] = 'Review the commands, then run `setup --apply` (or paste them), and restart the gateways.'
        return result
    failed = []
    for command in commands:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if done.returncode:
            failed.append(shlex.join(command))
    result.update(status='applied' if not failed else 'partial', failed=failed,
                  next='Restart the Hermes gateways so every profile opens the shared store.')
    return result
