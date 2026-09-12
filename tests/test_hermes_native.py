from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import threading
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
UPSTREAM = ROOT.parent / "upstream" / "hermes-agent-official"
if UPSTREAM.is_dir() and str(UPSTREAM) not in sys.path:
    sys.path.insert(0, str(UPSTREAM))
if not UPSTREAM.is_dir() and "agent.memory_provider" not in sys.modules:
    agent_module = types.ModuleType("agent")
    agent_module.__path__ = []  # type: ignore[attr-defined]
    memory_provider_module = types.ModuleType("agent.memory_provider")
    memory_provider_module.MemoryProvider = type("MemoryProvider", (), {})
    skill_commands_module = types.ModuleType("agent.skill_commands")
    skill_commands_module.extract_user_instruction_from_skill_message = lambda value: value
    sys.modules.update(
        {
            "agent": agent_module,
            "agent.memory_provider": memory_provider_module,
            "agent.skill_commands": skill_commands_module,
        }
    )


class HermesNativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_default_provider_is_local_and_requires_no_credentials(self) -> None:
        plugin = importlib.import_module("adapters.hermes")
        local = importlib.import_module("bigfeels_mem.local")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(local, "default_data_dir", return_value=Path(self.temp.name) / "default"),
            patch.object(plugin, "_HostExtractor", return_value=None),
        ):
            provider = plugin.BigfeelsMemoryProvider()
            provider.initialize(
                "session-native",
                hermes_home=self.temp.name,
                platform="cli",
                agent_context="primary",
            )
            self.assertTrue(provider.is_available())
            self.assertEqual(provider.get_config_schema(), [])
            self.assertEqual(provider._config.spaces, ("owner",))
            self.assertIsNotNone(provider._local)
            self.assertIsNone(provider._client)
            provider.shutdown()

    def test_native_capture_and_tools_use_local_client_without_http(self) -> None:
        plugin = importlib.import_module("adapters.hermes")
        provider = plugin.BigfeelsMemoryProvider(
            {
                "data_dir": self.temp.name,
                "project_spaces": ["project:app"],
                "write_space": "project:app",
                "auto_extract": False,
            }
        )
        provider.initialize(
            "session-native",
            hermes_home=self.temp.name,
            platform="cli",
            agent_context="primary",
        )
        self.addCleanup(provider.shutdown)
        provider.on_turn_start(3, "Remember that the release uses Hermes.")
        provider.sync_turn(
            "Remember that the release uses Hermes.",
            "I will keep that in mind.",
            session_id="session-native",
        )
        status = json.loads(provider.handle_tool_call("bigfeels_status", {}))
        self.assertEqual(status["spaces"], ["owner", "project:app"])
        self.assertEqual(provider._client, None)

    def test_native_capture_preserves_hermes_identity_roles_and_echo_lineage(self) -> None:
        plugin = importlib.import_module("adapters.hermes")
        provider = plugin.BigfeelsMemoryProvider(
            {"data_dir": self.temp.name, "auto_extract": False}
        )
        provider.initialize(
            "session-native",
            hermes_home=self.temp.name,
            platform="cli",
            agent_context="primary",
        )
        try:
            provider.on_turn_start(7, "What did we decide?")
            provider.sync_turn(
                "What did we decide?",
                "Use the bounded lifecycle.",
                session_id="session-native",
                messages=[
                    {"role": "user", "content": "What did we decide?"},
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {"id": "memory-call", "function": {"name": "bigfeels_search"}},
                            {"id": "call-1", "function": {"name": "shell"}},
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call-1",
                        "content": "The check completed.",
                    },
                ],
            )
            evidence = provider._local.call("export", {})["evidence"]
            self.assertEqual(
                [(item["source_event_id"], item["speaker"], item["space"]) for item in evidence],
                [
                    ("hermes:session-native:turn-7:user", "user", "owner"),
                    ("hermes:session-native:turn-7:tool:call-1", "tool", "owner"),
                    ("hermes:session-native:turn-7:assistant", "assistant", "owner"),
                ],
            )

            memory = json.loads(
                provider.handle_tool_call(
                    "bigfeels_remember", {"content": "Exact recalled statement."}
                )
            )
            provider.on_turn_start(8, "Repeat the exact recalled statement.")
            self.assertIn(
                "Exact recalled statement.",
                provider.prefetch("Exact recalled statement"),
            )
            provider.sync_turn(
                "Repeat the exact recalled statement.",
                "Exact recalled statement.",
                session_id="session-native",
            )
            self.assertEqual(
                len(provider._local.call("export", {})["evidence"]),
                len(evidence) + 2,
            )
            self.assertEqual(memory["content"], "Exact recalled statement.")
        finally:
            provider.shutdown()

    def test_native_extraction_uses_the_host_auxiliary_client(self) -> None:
        plugin = importlib.import_module("adapters.hermes")
        calls: list[dict[str, object]] = []
        called = threading.Event()
        auxiliary = types.ModuleType("agent.auxiliary_client")

        def call_llm(**kwargs: object) -> object:
            calls.append(kwargs)
            called.set()
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content=json.dumps(
                                {
                                    "memories": [
                                        {
                                            "content": "The release uses Hermes.",
                                            "quote": "The release uses Hermes.",
                                            "kind": "fact",
                                            "basis": "direct",
                                            "outcome": "unspecified",
                                        }
                                    ]
                                }
                            )
                        )
                    )
                ]
            )

        auxiliary.call_llm = call_llm  # type: ignore[attr-defined]
        agent = types.ModuleType("agent")
        agent.__path__ = []  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"agent": agent, "agent.auxiliary_client": auxiliary}):
            provider = plugin.BigfeelsMemoryProvider(
                {"data_dir": self.temp.name, "auto_extract": True}
            )
            try:
                provider.initialize(
                    "session-native",
                    hermes_home=self.temp.name,
                    platform="cli",
                    agent_context="primary",
                )
                provider.on_turn_start(1, "The release uses Hermes.")
                provider.sync_turn(
                    "The release uses Hermes.",
                    "",
                    session_id="session-native",
                )
                self.assertTrue(called.wait(2))
            finally:
                provider.shutdown()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["task"], "bigfeels_memory")
        self.assertIn("messages", calls[0])
        self.assertNotIn("api_key", calls[0])
        self.assertNotIn("token", calls[0])
        local_cls = plugin._import_core('bigfeels_mem.local').LocalClient
        local = local_cls(self.temp.name, auto_process=False)
        try:
            self.assertEqual(local.call('context', {'query':'release Hermes'})['memories'][0]['content'],
                             'The release uses Hermes.')
        finally:
            local.close()

    def test_non_primary_initialization_does_not_open_local_storage(self) -> None:
        plugin = importlib.import_module("adapters.hermes")
        provider = plugin.BigfeelsMemoryProvider({"data_dir": self.temp.name})
        provider.initialize(
            "session-subagent",
            hermes_home=self.temp.name,
            platform="cli",
            agent_context="subagent",
        )
        self.addCleanup(provider.shutdown)
        self.assertFalse(Path(self.temp.name, "memory.sqlite").exists())
        self.assertEqual(provider.prefetch("private"), "")

    def test_repo_root_is_installable_by_the_native_hermes_loader(self) -> None:
        manifest = ROOT / "plugin.yaml"
        entry = ROOT / "__init__.py"
        self.assertTrue(manifest.exists())
        self.assertTrue(entry.exists())
        copied = Path(self.temp.name) / "bigfeels"
        shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc"))
        self.assertTrue((copied / "plugin.yaml").exists())
        self.assertTrue((copied / "__init__.py").exists())

        try:
            from hermes_cli.plugins import PluginManager, PluginManifest
        except ImportError:
            self.skipTest("Hermes checkout is not available")
        manifest_obj = PluginManifest(
            name="bigfeels",
            version="",
            description="",
            author="",
            requires_env=[],
            provides_tools=[],
            provides_hooks=[],
            source="user",
            path=str(copied),
            kind="exclusive",
            key="bigfeels",
        )
        manager = PluginManager()
        module = manager._load_directory_module(manifest_obj)
        self.assertTrue(callable(module.register))

        try:
            from plugins.memory import _load_provider_from_dir, _is_memory_provider_dir
        except ImportError:
            self.skipTest("Hermes memory loader is not available")
        self.assertTrue(_is_memory_provider_dir(copied), 'Hermes must discover the installed repository')
        provider = _load_provider_from_dir(copied)
        self.assertIsNotNone(provider)
        self.assertEqual(provider.name, "bigfeels")

        script = f"""
import sys
import types
from pathlib import Path
from hermes_cli.plugins import PluginManager, PluginManifest

repo = Path({str(copied)!r})
sys.path[:] = [p for p in sys.path if p not in {{{str(ROOT)!r}, {str(SRC)!r}}}]
old_package = types.ModuleType('bigfeels_mem')
old_package.__path__ = ['/nonexistent/old-bigfeels']
sys.modules['bigfeels_mem'] = old_package
manifest = PluginManifest(
    name='bigfeels', version='', description='', author='', requires_env=[],
    provides_tools=[], provides_hooks=[], source='user', path=str(repo),
    kind='exclusive', key='bigfeels',
)
module = PluginManager()._load_directory_module(manifest)
class Context:
    def __init__(self):
        self.provider = None
    def register_memory_provider(self, provider):
        self.provider = provider
context = Context()
module.register(context)
assert context.provider is not None
assert context.provider.is_available()
assert '_bigfeels_mem_hermes.local' in sys.modules
assert sys.modules['bigfeels_mem'] is old_package
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(UPSTREAM)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
