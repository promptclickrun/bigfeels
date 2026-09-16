"""Adapter for installed Mnemosyne in an isolated subprocess.

The benchmark ingest path deliberately buffers writes and flushes them through
BeamMemory.remember_batch, the installed package's public high-throughput API.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base_adapter import (
    BaseMemoryAdapter,
    CapabilityProfile,
    NormalizedRecord,
    OpResult,
    RetrievalResult,
    StorageStats,
)

_MNEMO_PYTHON = "/Users/gordieai/.local/share/gordie-docs/.venv/bin/python"
_WORKER_SCRIPT = str(Path(__file__).resolve().parent / "mnemosyne_worker.py")
_DEFAULT_BATCH_SIZE = 512


@dataclass
class MnemosyneHandle:
    temp_dir: str
    db_path: Path
    proc: subprocess.Popen
    version: str
    session_id: str
    batch_size: int = _DEFAULT_BATCH_SIZE
    pending: List[Dict[str, Any]] = field(default_factory=list)
    last_flush: Dict[str, Any] = field(default_factory=dict)
    last_error: Dict[str, Any] = field(default_factory=dict)
    step_ids: Dict[str, str] = field(default_factory=dict)


class MnemosyneAdapter(BaseMemoryAdapter):
    name = "mnemosyne"
    version = "3.15.1"

    def capabilities(self) -> CapabilityProfile:
        return CapabilityProfile(
            store=True,
            retrieve=True,
            evidence=False,
            time=False,
            correct=True,
            forget=True,
            scope=False,
            extract=False,
            restart=True,
            queue=False,
            cost=False,
        )

    def _spawn_worker(self, db_path: Path, session_id: str, step_ids: Optional[Dict[str, str]] = None) -> tuple[subprocess.Popen, str]:
        # Minimal clean environment preventing live directory usage.
        env = dict(os.environ)
        env["MNEMOSYNE_DATA_DIR"] = str(db_path.parent)
        env["MNEMOSYNE_DISABLE_AUTO_SYNC"] = "1"
        proc = subprocess.Popen(
            [_MNEMO_PYTHON, _WORKER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({
            "cmd": "init", "db_path": str(db_path), "session_id": session_id,
            "step_ids": step_ids or {},
        }) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            detail = proc.stderr.read(2000).strip() if proc.stderr else ""
            raise RuntimeError(f"Mnemosyne worker EOF during init: {detail}")
        try:
            res = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Mnemosyne worker sent invalid init response: {exc}") from exc
        if res.get("status") != "ok":
            raise RuntimeError(f"Mnemosyne worker init failed: {res}")
        return proc, res.get("version", "3.15.1")

    def _send_cmd(self, proc: subprocess.Popen, cmd_dict: Dict[str, Any]) -> Dict[str, Any]:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(cmd_dict) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            detail = proc.stderr.read(2000).strip() if proc.poll() is not None and proc.stderr else ""
            raise RuntimeError(json.dumps({
                "kind": "worker_eof",
                "command": cmd_dict.get("cmd"),
                "returncode": proc.poll(),
                "stderr": detail,
            }, sort_keys=True))
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(json.dumps({
                "kind": "worker_protocol_error",
                "command": cmd_dict.get("cmd"),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "response_prefix": line[:200],
            }, sort_keys=True)) from exc

    def create_isolated_run(self, run_id: str, seed: int, spaces: List[str], principals: List[str]) -> MnemosyneHandle:
        temp_dir = tempfile.mkdtemp(prefix=f"mnemo-eval-{run_id}-", dir="/tmp")
        db_path = Path(temp_dir) / "mnemosyne.sqlite"
        session_id = f"eval-{run_id}-{seed}"
        proc, version = self._spawn_worker(db_path, session_id)
        return MnemosyneHandle(temp_dir=temp_dir, db_path=db_path, proc=proc, version=version,
                               session_id=session_id)

    @staticmethod
    def _error_result(t0: float, diagnostics: Dict[str, Any]) -> OpResult:
        return OpResult(status="error", latency_ms=(time.perf_counter() - t0) * 1000, safe_diagnostics=diagnostics)

    def _flush(self, handle: MnemosyneHandle) -> OpResult:
        if not handle.pending:
            return OpResult(status="ok", safe_diagnostics={"flushed": 0})
        t0 = time.perf_counter()
        items = handle.pending
        try:
            res = self._send_cmd(handle.proc, {"cmd": "remember_batch", "items": items})
        except Exception as exc:
            diagnostics = {"kind": "flush_transport_error", "error_type": type(exc).__name__, "error": str(exc), "batch_size": len(items)}
            handle.last_error = diagnostics
            return self._error_result(t0, diagnostics)
        if res.get("status") != "ok":
            diagnostics = dict(res.get("diagnostics") or {"error": res.get("error")})
            diagnostics.update({"kind": "batch_ingestion_error", "batch_size": len(items)})
            handle.last_error = diagnostics
            return self._error_result(t0, diagnostics)
        # IDs are returned in exact input order. The worker also records them
        # against each item's step before replying, preserving correction tests.
        ids = [str(x) for x in res.get("ids", [])]
        for item, memory_id in zip(items, ids):
            handle.step_ids[str(item.get("step", 0))] = memory_id
        result = OpResult(
            status="ok",
            record_ids=ids,
            latency_ms=res.get("latency_ms", (time.perf_counter() - t0) * 1000),
            safe_diagnostics={"flushed": len(items), "batch_size": len(items)},
        )
        handle.pending = []
        handle.last_flush = result.safe_diagnostics
        return result

    def flush_pending(self, handle: MnemosyneHandle) -> OpResult:
        """Commit buffered ingest and expose failures to benchmark callers."""
        return self._flush(handle)

    def apply_op(self, handle: MnemosyneHandle, operation: Dict[str, Any]) -> OpResult:
        t0 = time.perf_counter()
        op = operation.get("op")
        step = operation.get("step", 0)

        if op == "remember":
            item = {
                "content": operation.get("content", ""),
                "source": operation.get("source", "benchmark"),
                "importance": operation.get("importance", 0.5),
                "metadata": operation.get("metadata") or {},
                "veracity": "imported",
                "step": step,
            }
            handle.pending.append(item)
            if len(handle.pending) >= handle.batch_size:
                return self._flush(handle)
            return OpResult(status="ok", latency_ms=(time.perf_counter() - t0) * 1000,
                            safe_diagnostics={"queued": 1, "pending": len(handle.pending)})

        # Every non-ingest operation observes the durable state, not a stale
        # client buffer. This is also the ordering barrier for corrections.
        flushed = self._flush(handle)
        if flushed.status != "ok":
            return flushed

        try:
            if op == "correct":
                res = self._send_cmd(handle.proc, {
                    "cmd": "update", "step": step,
                    "target_step": operation.get("target_step", 1),
                    "content": operation.get("content", ""),
                })
            elif op == "forget":
                res = self._send_cmd(handle.proc, {
                    "cmd": "forget", "step": step,
                    "target_step": operation.get("target_step", 1),
                })
            else:
                return OpResult(status="unsupported", safe_diagnostics={"reason": f"op {op} unsupported in mnemosyne adapter"})
        except Exception as exc:
            return self._error_result(t0, {"kind": "worker_transport_error", "command": op, "error_type": type(exc).__name__, "error": str(exc)})
        if res.get("status") == "ok":
            if op == "correct" and res.get("id"):
                handle.step_ids[str(step)] = str(res["id"])
            return OpResult(status="ok", record_ids=[str(res.get("id"))] if res.get("id") else [], latency_ms=res.get("latency_ms", (time.perf_counter() - t0) * 1000))
        return self._error_result(t0, res.get("diagnostics") or {"error": res.get("error"), "command": op})

    def retrieve(self, handle: MnemosyneHandle, actor: str, query: str, as_of: Optional[str] = None, budget: int = 3200) -> RetrievalResult:
        t0 = time.perf_counter()
        flushed = self._flush(handle)
        if flushed.status != "ok":
            return RetrievalResult(status="error", latency_ms=flushed.latency_ms, safe_diagnostics=flushed.safe_diagnostics)
        try:
            res = self._send_cmd(handle.proc, {"cmd": "recall", "query": query, "budget": budget, "top_k": 5})
        except Exception as exc:
            return RetrievalResult(status="error", latency_ms=(time.perf_counter() - t0) * 1000,
                                   safe_diagnostics={"kind": "worker_transport_error", "command": "recall", "error_type": type(exc).__name__, "error": str(exc)})
        if res.get("status") != "ok":
            return RetrievalResult(status="error", latency_ms=(time.perf_counter() - t0) * 1000,
                                   safe_diagnostics=res.get("diagnostics") or {"error": res.get("error")})
        records = [NormalizedRecord(record_id=r.get("id", ""), content=r.get("content", ""),
                                    metadata={"importance": r.get("importance"), "source": r.get("source")})
                   for r in res.get("records", [])]
        return RetrievalResult(status="ok", records=records, tokens_used=res.get("tokens_used", 0),
                               latency_ms=res.get("latency_ms", (time.perf_counter() - t0) * 1000))

    def restart(self, handle: MnemosyneHandle) -> MnemosyneHandle:
        flushed = self._flush(handle)
        if flushed.status != "ok":
            raise RuntimeError(json.dumps(flushed.safe_diagnostics, sort_keys=True))
        try:
            self._send_cmd(handle.proc, {"cmd": "exit"})
            handle.proc.terminate()
            handle.proc.wait(timeout=2)
        except Exception:
            pass
        new_proc, version = self._spawn_worker(handle.db_path, handle.session_id, handle.step_ids)
        return MnemosyneHandle(temp_dir=handle.temp_dir, db_path=handle.db_path, proc=new_proc,
                               version=version, session_id=handle.session_id, batch_size=handle.batch_size,
                               step_ids=dict(handle.step_ids))

    def get_storage_stats(self, handle: MnemosyneHandle) -> StorageStats:
        flushed = self._flush(handle)
        if flushed.status != "ok":
            return StorageStats(safe_diagnostics=flushed.safe_diagnostics)
        p = handle.db_path
        disk_bytes = p.stat().st_size if p.exists() else 0
        wal_p = Path(str(p) + "-wal")
        wal_bytes = wal_p.stat().st_size if wal_p.exists() else 0
        return StorageStats(disk_bytes=disk_bytes, wal_bytes=wal_bytes)

    def teardown(self, handle: MnemosyneHandle) -> None:
        # Flush before exit so teardown cannot silently discard queued ingest.
        self._flush(handle)
        try:
            self._send_cmd(handle.proc, {"cmd": "exit"})
            handle.proc.terminate()
            handle.proc.wait(timeout=2)
        except Exception:
            try:
                handle.proc.kill()
            except Exception:
                pass
        time.sleep(0.05)
        shutil.rmtree(handle.temp_dir, ignore_errors=True)
