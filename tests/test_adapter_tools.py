from __future__ import annotations

import json
import re
import tempfile
import unittest
import uuid
from pathlib import Path

# Installs the minimal host ABC only when the optional Hermes checkout is absent.
import test_adapters  # noqa: F401

from adapters.hermes.tools import handle_tool, tool_schemas
from bigfeels_mem.store import Principal, Store


class HermesAdapterToolTests(unittest.TestCase):
    def test_schemas_expose_only_the_seven_bounded_memory_operations(self) -> None:
        schemas = tool_schemas()

        self.assertEqual(
            [schema["name"] for schema in schemas],
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
        self.assertTrue(
            all(set(schema) == {"name", "description", "parameters"} for schema in schemas)
        )
        by_name = {schema["name"]: schema["parameters"] for schema in schemas}
        self.assertEqual(by_name["bigfeels_search"]["required"], ["query"])
        self.assertIn("include_inactive", by_name["bigfeels_search"]["properties"])
        self.assertEqual(by_name["bigfeels_remember"]["required"], ["content"])
        self.assertEqual(
            by_name["bigfeels_remember"]["properties"]["kind"]["enum"],
            ["fact", "preference", "decision", "episode", "procedure", "task"],
        )
        self.assertEqual(
            by_name["bigfeels_correct"]["required"],
            ["id", "revision", "content"],
        )
        self.assertEqual(
            by_name["bigfeels_forget"]["required"], ["id", "plan_token"]
        )
        self.assertEqual(by_name["bigfeels_status"]["properties"], {})
        self.assertTrue(
            all(schema["parameters"]["additionalProperties"] is False for schema in schemas)
        )

    def test_remember_description_documents_attested_rules_and_not_verified(self) -> None:
        remember = next(
            schema for schema in tool_schemas() if schema["name"] == "bigfeels_remember"
        )
        description = remember["description"]

        # Agents that followed "use verified outcome" were rejected by the store (#12).
        self.assertNotRegex(description, re.compile(r"\buse verified\b", re.IGNORECASE))
        self.assertIn("leave outcome unset", description)
        for requirement in ("attested", "basis observed", "evidence_ids", "tool observation"):
            self.assertIn(requirement, description)
        self.assertRegex(description, r"legacy value verified .*stored as attested")

        outcomes = remember["parameters"]["properties"]["outcome"]["enum"]
        self.assertEqual(
            outcomes, ["unspecified", "proposed", "attempted", "attested", "failed"]
        )

    def test_legacy_verified_outcome_still_reaches_the_store_as_attested(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "memory.sqlite")
            principal = Principal("adapter-tools", ("owner",))

            def post(operation: str, payload: dict[str, object]) -> dict[str, object]:
                return store.dispatch(principal, operation, payload)

            evidence = store.dispatch(
                principal,
                "observe",
                {
                    "space": "owner",
                    "source": "synthetic-adapter",
                    "source_event_id": "1",
                    "session_id": "adapter",
                    "speaker": "tool",
                    "content": "deploy: exit 0",
                },
            )

            def remember(content: str, **extra: object) -> dict[str, object]:
                return json.loads(
                    handle_tool(
                        "bigfeels_remember",
                        {"content": content, **extra},
                        post,
                        ("owner",),
                        "owner",
                    )
                )

            legacy = remember(
                "The deploy succeeded.",
                basis="observed",
                outcome="verified",
                evidence_ids=[evidence["id"]],
            )
            attested = remember(
                "The staging deploy succeeded.",
                basis="observed",
                outcome="attested",
                evidence_ids=[evidence["id"]],
            )
            missing_evidence = remember(
                "The prod deploy succeeded.", basis="observed", outcome="attested"
            )
            plain = remember("Deploys run from the release branch.")

        self.assertEqual(legacy["outcome"], "attested")
        self.assertEqual(attested["outcome"], "attested")
        self.assertIn("error", missing_evidence)
        self.assertEqual(plain["outcome"], "unspecified")

    def test_search_and_remember_apply_configured_scope_defaults_without_mutating_args(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def post(operation: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((operation, payload))
            return {"operation": operation, "payload": payload}

        search_args = {"query": "release gate", "budget": 900}
        remember_args = {"content": "Ship after the gate", "kind": "procedure"}
        search_result = json.loads(
            handle_tool(
                "bigfeels_search",
                search_args,
                post,
                ("owner:gordie", "project:bigfeels"),
                "project:bigfeels",
            )
        )
        remember_result = json.loads(
            handle_tool(
                "bigfeels_remember",
                remember_args,
                post,
                ("owner:gordie", "project:bigfeels"),
                "project:bigfeels",
            )
        )

        self.assertEqual(search_args, {"query": "release gate", "budget": 900})
        self.assertEqual(remember_args, {"content": "Ship after the gate", "kind": "procedure"})
        self.assertEqual(
            calls,
            [
                (
                    "search",
                    {
                        "query": "release gate",
                        "budget": 900,
                        "spaces": ["owner:gordie", "project:bigfeels"],
                    },
                ),
                (
                    "remember",
                    {
                        "content": "Ship after the gate",
                        "kind": "procedure",
                        "space": "project:bigfeels",
                    },
                ),
            ],
        )
        self.assertEqual(search_result["operation"], "search")
        self.assertEqual(remember_result["operation"], "remember")

    def test_requested_spaces_must_stay_within_the_adapter_configuration(self) -> None:
        calls: list[object] = []

        def post(operation: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((operation, payload))
            return {"unexpected": True}

        cases = [
            ("bigfeels_search", {"query": "secret", "spaces": ["other:private"]}),
            ("bigfeels_remember", {"content": "secret", "space": "other:private"}),
        ]
        for name, args in cases:
            with self.subTest(name=name):
                result = json.loads(
                    handle_tool(
                        name,
                        args,
                        post,
                        ("owner:gordie", "project:bigfeels"),
                        "owner:gordie",
                    )
                )
                self.assertEqual(result, {"error": {"message": "Requested memory space is not allowed."}})
        self.assertEqual(calls, [])

    def test_all_tools_route_to_the_matching_service_operation(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def post(operation: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((operation, payload))
            return {"ok": operation}

        cases = [
            ("bigfeels_inspect", {"id": "memory-1"}, "inspect"),
            (
                "bigfeels_correct",
                {"id": "memory-1", "revision": 2, "content": "Updated"},
                "correct",
            ),
            ("bigfeels_forget_preview", {"id": "memory-1"}, "forget_preview"),
            (
                "bigfeels_forget",
                {"id": "memory-1", "plan_token": "current-plan"},
                "forget",
            ),
            ("bigfeels_status", {}, "status"),
        ]
        for name, args, operation in cases:
            with self.subTest(name=name):
                result = json.loads(
                    handle_tool(name, args, post, ("owner:gordie",), "owner:gordie")
                )
                self.assertEqual(result, {"ok": operation})
        self.assertEqual([operation for operation, _ in calls], [item[2] for item in cases])

        tokenless = json.loads(
            handle_tool(
                "bigfeels_forget", {"id": "memory-1"}, post,
                ("owner:gordie",), "owner:gordie",
            )
        )
        self.assertEqual(tokenless, {"error": {"message": "Invalid memory tool arguments."}})

    def test_invalid_calls_and_service_failures_do_not_echo_arguments_or_credentials(self) -> None:
        # Generate a synthetic marker; never ship a credential-shaped literal.
        secret = uuid.uuid4().hex

        def failing_post(_operation: str, _payload: dict[str, object]) -> dict[str, object]:
            raise RuntimeError(f"request failed with {secret}")

        failed = handle_tool(
            "bigfeels_inspect",
            {"id": secret},
            failing_post,
            ("owner:gordie",),
            "owner:gordie",
        )
        unknown = handle_tool(
            "bigfeels_export",
            {"token": secret},
            failing_post,
            ("owner:gordie",),
            "owner:gordie",
        )

        self.assertEqual(json.loads(failed), {"error": {"message": "Memory service request failed."}})
        self.assertEqual(json.loads(unknown), {"error": {"message": "Unknown bigfeels memory tool."}})
        self.assertNotIn(secret, failed + unknown)


if __name__ == "__main__":
    unittest.main()
