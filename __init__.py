"""Repository root entry point for direct Hermes plugin installation.

Hermes installs a repository link as one directory and loads its root
``__init__.py`` through ``PluginManager._load_directory_module``.  Keep the
implementation in ``adapters/hermes`` for package and HTTP compatibility, but
load it under this plugin package so relative imports continue to work when
the repository is copied into a Hermes profile without pip installation.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


_ADAPTER: ModuleType | None = None


def _adapter() -> ModuleType:
    global _ADAPTER
    if _ADAPTER is not None:
        return _ADAPTER
    adapter_dir = Path(__file__).resolve().parent / "adapters" / "hermes"
    init_file = adapter_dir / "__init__.py"
    module_name = f"{__name__}._hermes_adapter"
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_file,
        submodule_search_locations=[str(adapter_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("bigfeels Hermes adapter is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _ADAPTER = module
    return module


def register(ctx):
    ctx.register_memory_provider(_adapter().BigfeelsMemoryProvider())


def __getattr__(name: str):
    return getattr(_adapter(), name)


__all__ = ["register"]
