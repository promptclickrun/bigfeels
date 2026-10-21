"""``hermes bigfeels ...``: the bundled ``bigfeels-mem`` command, through Hermes.

The plugin declares ``python_runtime: external``, so Hermes never installs this
repository into its environment and no ``bigfeels-mem`` script lands on PATH.
For the active memory provider, Hermes imports this file (never the provider)
and calls ``register_cli`` with the ``hermes bigfeels`` subparser. Arguments go
unchanged to ``bigfeels_mem.cli`` from this checkout's ``src`` directory, in a
child process so an older ``bigfeels_mem`` on Hermes' path cannot shadow it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent / "src"


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Run the bundled bigfeels-mem command line, for example "
        "'hermes bigfeels --data-dir DIR cloud status'. "
        "Run 'hermes bigfeels' alone for its full command list."
    )
    # bigfeels-mem's only global option. Declared here because argparse cannot hand a
    # leading unknown option to a REMAINDER positional; everything after the first
    # command word (including --help) passes through untouched.
    subparser.add_argument("--data-dir", help="bigfeels data directory")
    subparser.add_argument("bigfeels_args", nargs=argparse.REMAINDER, metavar="COMMAND ...")


def bigfeels_command(args: argparse.Namespace) -> int:
    argv = ["--data-dir", args.data_dir] if args.data_dir is not None else []
    return run([*argv, *(args.bigfeels_args or ["--help"])])


def run(argv: list[str]) -> int:
    """Run ``bigfeels_mem.cli`` from the bundled source; returns its exit code."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(SOURCE_ROOT), env.get("PYTHONPATH", "")) if part
    )
    return subprocess.call([sys.executable, "-m", "bigfeels_mem.cli", *argv], env=env)
