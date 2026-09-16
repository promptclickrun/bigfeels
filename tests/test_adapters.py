from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HERMES_UPSTREAM = ROOT.parent / "upstream" / "hermes-agent-official"
if HERMES_UPSTREAM.is_dir():
    sys.path.insert(0, str(HERMES_UPSTREAM))
else:
    # Standalone package CI has no Hermes checkout. This fixture contains only
    # the abstract surface the external plugin imports; integration against the
    # real ABC is exercised automatically when the reference checkout exists.
    class _MemoryProvider:
        pass

    agent_module = types.ModuleType("agent")
    agent_module.__path__ = []  # type: ignore[attr-defined]
    memory_provider_module = types.ModuleType("agent.memory_provider")
    memory_provider_module.MemoryProvider = _MemoryProvider
    skill_commands_module = types.ModuleType("agent.skill_commands")

    def _extract_user_instruction_from_skill_message(content: object):
        prefix = "[IMPORTANT: The user has invoked the "
        single_marker = "The full skill content is loaded below.]"
        single_instruction = (
            "The user has provided the following instruction alongside the skill invocation: "
        )
        if not isinstance(content, str):
            return None
        if not content.startswith(prefix):
            return content
        if " skill bundle," in content:
            marker = "\nUser instruction: "
            boundary = '\n\n[Loaded as part of the '
            if marker not in content:
                return None
            return content.split(marker, 1)[1].split(boundary, 1)[0].strip() or None
        if single_marker not in content or single_instruction not in content:
            return None
        instruction = content.rsplit(single_instruction, 1)[1]
        return instruction.split("\n\n[Runtime note:", 1)[0].strip() or None

    skill_commands_module.extract_user_instruction_from_skill_message = (
        _extract_user_instruction_from_skill_message
    )
    sys.modules.setdefault("agent", agent_module)
    sys.modules.setdefault("agent.memory_provider", memory_provider_module)
    sys.modules.setdefault("agent.skill_commands", skill_commands_module)
sys.path.insert(0, str(ROOT))


class _MemoryHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    context_by_query: dict[str, dict] = {}
    status_by_path: dict[str, int] = {}
    delay_by_path: dict[str, float] = {}

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
                "body": body,
            }
        )
        delay = type(self).delay_by_path.get(self.path, 0)
        if delay:
            time.sleep(delay)
        status = type(self).status_by_path.get(self.path, 200)
        if self.path == "/v1/context":
            response = type(self).context_by_query.get(
                body.get("query", ""),
                {"memories": [], "tokens": 0, "status": "ok", "trace": {}},
            )
        else:
            response = {"id": "evidence", "status": "accepted"}
        encoded = json.dumps(response).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except BrokenPipeError:
            pass

    def log_message(self, _format: str, *_args: object) -> None:
        pass


@contextmanager
def memory_service():
    _MemoryHandler.requests = []
    _MemoryHandler.context_by_query = {}
    _MemoryHandler.status_by_path = {}
    _MemoryHandler.delay_by_path = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MemoryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class FakeHermesContext:
    def __init__(self) -> None:
        self.provider = None

    def register_memory_provider(self, provider: object) -> None:
        self.provider = provider


class HermesAdapterTests(unittest.TestCase):
    def make_provider(self, base_url: str, **overrides: object):
        from adapters.hermes import AdapterConfig, BigfeelsMemoryProvider

        values = {
            "base_url": base_url,
            "token": "top-secret-token",
            "owner_space": "owner:gordie",
            "project_spaces": ("project:bigfeels",),
            "write_space": "project:bigfeels",
            "budget": 240,
            "timeout": 0.25,
            "capture_roles": ("user", "assistant", "tool"),
        }
        values.update(overrides)
        provider = BigfeelsMemoryProvider(AdapterConfig(**values))
        provider.initialize("session-A", platform="cli", agent_context="primary")
        return provider

    def test_register_uses_the_native_hermes_memory_provider_lifecycle(self) -> None:
        from agent.memory_provider import MemoryProvider

        env = {
            "BIGFEELS_MEM_URL": "http://127.0.0.1:8765",
            "BIGFEELS_MEM_TOKEN": "secret",
            "BIGFEELS_MEM_OWNER_SPACE": "owner:gordie",
        }
        with patch.dict(os.environ, env, clear=True):
            plugin = importlib.import_module("adapters.hermes")
            context = FakeHermesContext()
            plugin.register(context)

        self.assertIsInstance(context.provider, MemoryProvider)
        self.assertEqual(context.provider.name, "bigfeels")
        self.assertEqual(
            [schema["name"] for schema in context.provider.get_tool_schemas()],
            [
                "bigfeels_search",
                "bigfeels_inspect",
                "bigfeels_remember",
                "bigfeels_correct",
                "bigfeels_forget_preview",
                "bigfeels_forget",
                "bigfeels_status",
            ],
        )

    def test_native_tool_calls_use_the_authenticated_scoped_client(self) -> None:
        with memory_service() as base_url:
            provider = self.make_provider(base_url)
            result = json.loads(provider.handle_tool_call("bigfeels_status", {}))

        self.assertEqual(result, {"id": "evidence", "status": "accepted"})
        self.assertEqual(_MemoryHandler.requests[0]["path"], "/v1/status")
        self.assertEqual(
            _MemoryHandler.requests[0]["authorization"], "Bearer top-secret-token"
        )

    def test_native_deleted_missing_and_denied_ids_return_the_same_safe_not_found_error(self) -> None:
        from adapters.hermes import BigfeelsMemoryProvider

        expected = {
            "error": {
                "code": "not_found",
                "message": "Memory item was not found or is not accessible.",
            }
        }
        with tempfile.TemporaryDirectory() as data_dir:
            private = BigfeelsMemoryProvider(
                {
                    "data_dir": data_dir,
                    "owner_space": "project:private",
                    "write_space": "project:private",
                    "auto_extract": False,
                }
            )
            private.initialize("private-session", platform="cli", agent_context="primary")
            private_memory = json.loads(
                private.handle_tool_call(
                    "bigfeels_remember", {"content": "Synthetic private memory."}
                )
            )
            self.assertEqual(
                json.loads(
                    private.handle_tool_call(
                        "bigfeels_inspect", {"id": private_memory["id"]}
                    )
                )["content"],
                "Synthetic private memory.",
            )
            private.shutdown()

            owner = BigfeelsMemoryProvider(
                {"data_dir": data_dir, "auto_extract": False}
            )
            owner.initialize("owner-session", platform="cli", agent_context="primary")
            saved = json.loads(
                owner.handle_tool_call(
                    "bigfeels_remember", {"content": "Synthetic deleted memory."}
                )
            )
            preview = json.loads(
                owner.handle_tool_call("bigfeels_forget_preview", {"id": saved["id"]})
            )
            deleted = json.loads(
                owner.handle_tool_call(
                    "bigfeels_forget",
                    {"id": saved["id"], "plan_token": preview["plan_token"]},
                )
            )
            self.assertEqual(deleted["status"], "deleted")

            results = [
                json.loads(owner.handle_tool_call("bigfeels_inspect", {"id": item_id}))
                for item_id in (
                    saved["id"],
                    "mem_nonexistent_synthetic_id",
                    private_memory["id"],
                )
            ]
            owner.shutdown()

        self.assertEqual(results, [expected, expected, expected])

    def test_http_missing_and_denied_ids_return_the_same_safe_not_found_error(self) -> None:
        expected = {
            "error": {
                "code": "not_found",
                "message": "Memory item was not found or is not accessible.",
            }
        }
        with memory_service() as base_url:
            provider = self.make_provider(base_url)
            results = []
            for status in (404, 403):
                _MemoryHandler.status_by_path["/v1/inspect"] = status
                results.append(
                    json.loads(
                        provider.handle_tool_call(
                            "bigfeels_inspect", {"id": "mem_synthetic_id"}
                        )
                    )
                )

        self.assertEqual(results, [expected, expected])

    def test_recall_and_capture_preserve_roles_ids_scopes_and_provenance(self) -> None:
        with memory_service() as base_url:
            _MemoryHandler.context_by_query["What did we decide?"] = {
                "memories": [
                    {
                        "id": "memory-1",
                        "content": "Use bounded lifecycle hooks.",
                        "kind": "decision",
                        "basis": "direct",
                        "outcome": "proposed",
                        "evidence_ids": ["evidence-9"],
                        "valid_from": "2026-09-01T00:00:00Z",
                        "valid_until": None,
                        "status": "active",
                        "source_availability": "available",
                        "reason": "hybrid match",
                    }
                ],
                "tokens": 8,
                "status": "ok",
                "trace": {"mode": "hybrid"},
            }
            provider = self.make_provider(base_url)
            provider.on_turn_start(7, "What did we decide?", platform="cli")
            context = provider.prefetch("What did we decide?", session_id="session-A")
            provider.sync_turn(
                "What did we decide?",
                'I quoted “Use bounded lifecycle hooks.” and proposed a test.',
                session_id="session-A",
                messages=[
                    {"role": "user", "content": "What did we decide?"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "memory-call",
                                "function": {"name": "bigfeels_search"},
                            },
                            {"id": "call-1", "function": {"name": "shell"}},
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "memory-call",
                        "content": '{"memories":[{"content":"old memory"}]}',
                    },
                    {
                        "role": "tool",
                        "toolName": "bigfeels_status",
                        "content": '{"status":"ok"}',
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call-1",
                        "content": "The verification was attempted, not completed.",
                    },
                    {"role": "assistant", "content": "final"},
                ],
            )
            # A host retry reaches the service with the same event keys.
            provider.sync_turn(
                "What did we decide?",
                'I quoted “Use bounded lifecycle hooks.” and proposed a test.',
                session_id="session-A",
                messages=[
                    {"role": "user", "content": "What did we decide?"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "memory-call",
                                "function": {"name": "bigfeels_search"},
                            },
                            {"id": "call-1", "function": {"name": "shell"}},
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "memory-call",
                        "content": '{"memories":[{"content":"old memory"}]}',
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call-1",
                        "content": "The verification was attempted, not completed.",
                    },
                ],
            )

        self.assertIn("Use bounded lifecycle hooks.", context)
        self.assertIn("evidence=evidence-9", context)
        self.assertIn("valid_from=2026-09-01T00:00:00Z", context)
        self.assertIn("source_availability=available", context)
        self.assertNotIn("top-secret-token", context)
        context_request = _MemoryHandler.requests[0]
        self.assertEqual(context_request["path"], "/v1/context")
        self.assertEqual(context_request["authorization"], "Bearer top-secret-token")
        self.assertEqual(context_request["content_type"], "application/json")
        self.assertEqual(
            context_request["body"],
            {
                "query": "What did we decide?",
                "spaces": ["owner:gordie", "project:bigfeels"],
                "budget": 240,
            },
        )
        observations = [request["body"] for request in _MemoryHandler.requests[1:]]
        self.assertEqual([item["speaker"] for item in observations], ["user", "tool", "assistant"] * 2)
        self.assertEqual(
            [item["source_event_id"] for item in observations],
            [
                "hermes:session-A:turn-7:user",
                "hermes:session-A:turn-7:tool:call-1",
                "hermes:session-A:turn-7:assistant",
            ]
            * 2,
        )
        self.assertEqual(observations[1]["content"], "The verification was attempted, not completed.")
        self.assertNotIn("outcome", observations[1])
        self.assertNotIn("origin_ids", observations[2])
        self.assertNotIn("outcome", observations[2])
        self.assertEqual(observations[2]["content"], 'I quoted “Use bounded lifecycle hooks.” and proposed a test.')
        self.assertTrue(all(item["space"] == "project:bigfeels" for item in observations))

    def test_exact_recalled_echo_is_marked_with_its_origin(self) -> None:
        with memory_service() as base_url:
            _MemoryHandler.context_by_query["repeat it"] = {
                "memories": [
                    {
                        "id": "memory-echo",
                        "content": "Exact recalled statement.",
                        "evidence_ids": ["evidence-original"],
                        "status": "active",
                    }
                ],
                "tokens": 4,
                "status": "ok",
                "trace": {},
            }
            provider = self.make_provider(base_url)
            provider.on_turn_start(2, "repeat it")
            provider.prefetch("repeat it", session_id="session-A")
            provider.sync_turn("repeat it", "Exact recalled statement.", session_id="session-A")

        assistant = _MemoryHandler.requests[-1]["body"]
        self.assertEqual(assistant["speaker"], "assistant")
        self.assertEqual(assistant["origin_ids"], ["evidence-original"])

    @unittest.skipUnless(
        HERMES_UPSTREAM.is_dir(),
        "the actual Hermes MemoryManager checkout is optional",
    )
    def test_queued_sync_keeps_the_completed_turn_identity(self) -> None:
        from agent.memory_manager import MemoryManager

        with memory_service() as base_url:
            provider = self.make_provider(base_url)
            manager = MemoryManager()
            manager.add_provider(provider)
            queued = []
            manager._submit_background = queued.append

            manager.on_turn_start(1, "first question")
            manager.sync_all(
                "first question",
                "first answer",
                session_id="session-A",
                messages=[
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "first answer"},
                ],
            )
            manager.on_turn_start(2, "second question")
            manager.sync_all(
                "second question",
                "second answer",
                session_id="session-A",
                messages=[
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "first answer"},
                    {"role": "user", "content": "second question"},
                    {"role": "assistant", "content": "second answer"},
                ],
            )

            self.assertEqual(len(queued), 2)
            for callback in queued:
                callback()

        user_events = [
            request["body"]["source_event_id"]
            for request in _MemoryHandler.requests
            if request["body"].get("speaker") == "user"
        ]
        self.assertEqual(
            user_events,
            [
                "hermes:session-A:turn-1:user",
                "hermes:session-A:turn-2:user",
            ],
        )

    @unittest.skipUnless(
        HERMES_UPSTREAM.is_dir(),
        "the actual Hermes skill normalization contract is optional",
    )
    def test_queued_skill_sync_uses_hermes_normalized_turn_identity(self) -> None:
        from agent.memory_manager import MemoryManager
        from agent.skill_commands import (
            _SINGLE_SKILL_INSTRUCTION,
            _SINGLE_SKILL_MARKER,
            _SKILL_INVOCATION_PREFIX,
        )

        def expanded(instruction: str) -> str:
            return (
                f'{_SKILL_INVOCATION_PREFIX}"test-skill" skill. '
                f"{_SINGLE_SKILL_MARKER}\n\n"
                "# Large model-facing skill body\n\n"
                f"{_SINGLE_SKILL_INSTRUCTION}{instruction}"
            )

        first = expanded("first skill instruction")
        second = expanded("second skill instruction")
        with memory_service() as base_url:
            provider = self.make_provider(base_url)
            manager = MemoryManager()
            manager.add_provider(provider)
            queued = []
            manager._submit_background = queued.append

            manager.on_turn_start(1, first)
            manager.sync_all(first, "first answer", session_id="session-A")
            manager.on_turn_start(2, second)
            manager.sync_all(second, "second answer", session_id="session-A")
            for callback in queued:
                callback()

        users = [
            request["body"]
            for request in _MemoryHandler.requests
            if request["body"].get("speaker") == "user"
        ]
        self.assertEqual(
            [(item["source_event_id"], item["content"]) for item in users],
            [
                ("hermes:session-A:turn-1:user", "first skill instruction"),
                ("hermes:session-A:turn-2:user", "second skill instruction"),
            ],
        )

    def test_subagent_background_and_unstable_turns_do_not_pollute_memory(self) -> None:
        with memory_service() as base_url:
            subagent = self.make_provider(base_url)
            subagent.initialize("child-session", platform="cli", agent_context="subagent")
            subagent.on_turn_start(1, "private delegated context")
            self.assertEqual(subagent.prefetch("private delegated context"), "")
            subagent.sync_turn("private delegated context", "delegated answer")

            cron = self.make_provider(base_url)
            cron.initialize("cron-session", platform="cron", agent_context="primary")
            cron.on_turn_start(1, "background task")
            cron.sync_turn("background task", "background result")

            unstable = self.make_provider(base_url)
            unstable.sync_turn("missing stable identity", "must skip")

        self.assertEqual(_MemoryHandler.requests, [])

    def test_recall_timeout_is_fail_open_and_does_not_log_token(self) -> None:
        with memory_service() as base_url:
            _MemoryHandler.delay_by_path["/v1/context"] = 0.15
            provider = self.make_provider(base_url, timeout=0.02)
            provider.on_turn_start(1, "slow query")
            with self.assertLogs("bigfeels_mem.hermes", logging.WARNING) as captured:
                result = provider.prefetch("slow query", session_id="session-A")

        self.assertEqual(result, "")
        joined = "\n".join(captured.output)
        self.assertIn("unavailable", joined)
        self.assertNotIn("top-secret-token", joined)

    def test_plain_http_service_must_be_loopback(self) -> None:
        from adapters.hermes import AdapterConfig

        with self.assertRaisesRegex(ValueError, "loopback"):
            AdapterConfig(
                base_url="http://memory.example.test",
                token="secret",
                owner_space="owner:gordie",
            ).validate()


if __name__ == "__main__":
    unittest.main()
