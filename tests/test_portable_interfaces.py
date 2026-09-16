"""Host-neutral CLI/MCP contracts; no host runtime or network required."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PortableInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.env = dict(os.environ)
        self.env.pop("BIGFEELS_MEM_TOKEN", None)

    def cli(self, *arguments):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "bigfeels_mem.cli",
                "--data-dir",
                str(self.data),
                *arguments,
            ],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def json_cli(self, *arguments):
        result = self.cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_cli_exposes_complete_explicit_memory_lifecycle(self):
        saved = self.json_cli(
            "remember",
            "Portable CLI memory.",
            "--space",
            "owner",
            "--kind",
            "decision",
        )
        found = self.json_cli("search", "Portable CLI", "--space", "owner")
        self.assertEqual([item["id"] for item in found["memories"]], [saved["id"]])
        self.assertEqual(self.json_cli("inspect", saved["id"])["content"], saved["content"])

        corrected = self.json_cli(
            "correct",
            saved["id"],
            "--revision",
            str(saved["revision"]),
            "Portable CLI memory, corrected.",
        )
        self.assertNotEqual(corrected["id"], saved["id"])
        current = self.json_cli("context", "corrected Portable CLI", "--space", "owner")
        self.assertEqual([item["id"] for item in current["memories"]], [corrected["id"]])

        preview = self.json_cli("forget-preview", corrected["id"])
        self.assertEqual(preview["memories"][0]["id"], corrected["id"])
        deleted = self.json_cli(
            "forget", corrected["id"], "--plan-token", preview["plan_token"]
        )
        self.assertGreaterEqual(deleted["memories"], 1)
        missing = self.cli("inspect", corrected["id"])
        self.assertNotEqual(missing.returncode, 0)

    def test_mcp_lists_safe_mutation_preview_and_processing_controls(self):
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "generic-client", "version": "1"},
                    "capabilities": {},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        # Start a real stdio process with an explicit request stream.
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "bigfeels_mem.cli",
                "--data-dir",
                str(self.data),
                "mcp",
            ],
            cwd=ROOT,
            env=self.env,
            input="".join(json.dumps(request) + "\n" for request in requests),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        names = {tool["name"] for tool in responses[1]["result"]["tools"]}
        self.assertIn("memory_forget_preview", names)
        self.assertIn("memory_process", names)

    def test_default_test_commands_include_root_node_regressions(self):
        package = json.loads((ROOT / "package.json").read_text())
        package_command = package["scripts"]["test"]
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for command in (package_command, workflow):
            self.assertTrue(
                "tests/openclaw_scheduler.test.mjs" in command
                or "tests/*.test.mjs" in command,
                "Default verification omitted the root OpenClaw scheduler regressions",
            )


if __name__ == "__main__":
    unittest.main()
