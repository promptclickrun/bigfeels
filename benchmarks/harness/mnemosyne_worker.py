"""Isolated worker for the installed Mnemosyne BeamMemory API.

Protocol: line-delimited JSON commands over stdin/stdout. Only the explicit
/tmp database supplied by init is opened.
"""
from __future__ import annotations

import importlib.metadata
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

try:
    from mnemosyne.core.beam import BeamMemory
    MNEMO_VERSION = importlib.metadata.version("mnemosyne-memory")
except Exception as exc:  # pragma: no cover - exercised by process startup
    sys.stderr.write(f"Failed to import installed mnemosyne BeamMemory: {exc}\n")
    sys.exit(1)


def reply(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def command_error(cmd: str, exc: BaseException, **extra: Any) -> Dict[str, Any]:
    diagnostics = {
        "kind": "worker_command_error",
        "command": cmd,
        "error_type": type(exc).__name__,
        "error": str(exc),
        **extra,
    }
    return {"status": "error", "error": str(exc), "diagnostics": diagnostics}


def main() -> None:
    db_path: Path | None = None
    beam: BeamMemory | None = None
    step_ids: Dict[str, str] = {}

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as exc:
            reply(command_error("protocol", exc))
            continue

        cmd = req.get("cmd", "")
        try:
            if cmd == "init":
                db_path = Path(req["db_path"]).resolve()
                if not (str(db_path).startswith("/tmp") or str(db_path).startswith("/private/tmp")):
                    raise ValueError(f"db_path must be under /tmp, got {db_path}")
                session_id = str(req.get("session_id") or "eval-session")
                beam = BeamMemory(
                    session_id=session_id,
                    db_path=db_path,
                    author_id="eval-owner",
                    author_type="eval",
                    channel_id="eval-channel",
                )
                step_ids.clear()
                step_ids.update({str(k): str(v) for k, v in (req.get("step_ids") or {}).items()})
                reply({"status": "ok", "version": MNEMO_VERSION, "db_path": str(db_path), "session_id": session_id})

            elif cmd == "remember_batch":
                if beam is None:
                    raise RuntimeError("BeamMemory not initialized")
                items: List[Dict[str, Any]] = list(req.get("items") or [])
                t0 = time.perf_counter()
                # This is the documented benchmark/import path. Explicit
                # trust settings prevent imported text from self-elevating.
                ids = beam.remember_batch(
                    items,
                    veracity="imported",
                    force_veracity=True,
                    trust_tier="IMPORTED",
                    extract_entities=False,
                    extract=False,
                )
                if len(ids) != len(items):
                    raise RuntimeError(f"remember_batch returned {len(ids)} IDs for {len(items)} items")
                for item, memory_id in zip(items, ids):
                    step_ids[str(item.get("step", 0))] = str(memory_id)
                reply({"status": "ok", "ids": [str(x) for x in ids], "batch_size": len(items),
                       "latency_ms": (time.perf_counter() - t0) * 1000})

            elif cmd == "update":
                if beam is None:
                    raise RuntimeError("BeamMemory not initialized")
                target_step = str(req.get("target_step", 1))
                target_id = step_ids.get(target_step)
                if not target_id:
                    reply(command_error(cmd, KeyError(f"target step {target_step} not found"), target_step=target_step))
                    continue
                t0 = time.perf_counter()
                ok = beam.update_working(target_id, content=req.get("content", ""))
                reply({"status": "ok" if ok else "error", "id": target_id,
                       "latency_ms": (time.perf_counter() - t0) * 1000,
                       **({} if ok else {"diagnostics": {"kind": "update_not_found", "target_id": target_id}})})
                if ok:
                    step_ids[str(req.get("step", 0))] = target_id

            elif cmd == "forget":
                if beam is None:
                    raise RuntimeError("BeamMemory not initialized")
                target_step = str(req.get("target_step", 1))
                target_id = step_ids.get(target_step)
                if not target_id:
                    reply(command_error(cmd, KeyError(f"target step {target_step} not found"), target_step=target_step))
                    continue
                t0 = time.perf_counter()
                ok = beam.forget_working(target_id)
                reply({"status": "ok" if ok else "error", "id": target_id,
                       "latency_ms": (time.perf_counter() - t0) * 1000,
                       **({} if ok else {"diagnostics": {"kind": "forget_not_found", "target_id": target_id}})})

            elif cmd == "recall":
                if beam is None:
                    raise RuntimeError("BeamMemory not initialized")
                t0 = time.perf_counter()
                raw_results = beam.recall(query=req.get("query", ""), top_k=req.get("top_k", 5))
                budget = int(req.get("budget", 3200))
                bounded: List[Dict[str, Any]] = []
                used_bytes = 0
                for record in raw_results:
                    cost = len(str(record.get("content", "")).encode("utf-8")) + 64
                    if used_bytes + cost > budget:
                        break
                    bounded.append({"id": record.get("id"), "content": record.get("content"),
                                    "importance": record.get("importance"), "source": record.get("source")})
                    used_bytes += cost
                reply({"status": "ok", "records": bounded, "tokens_used": used_bytes,
                       "latency_ms": (time.perf_counter() - t0) * 1000})

            elif cmd == "stats":
                if beam is None:
                    raise RuntimeError("BeamMemory not initialized")
                row = beam.conn.execute("SELECT COUNT(*) AS n FROM working_memory WHERE session_id = ?", (beam.session_id,)).fetchone()
                reply({"status": "ok", "record_count": int(row["n"] if hasattr(row, "keys") else row[0])})

            elif cmd == "ping":
                reply({"status": "ok", "msg": "pong"})

            elif cmd == "exit":
                reply({"status": "ok", "msg": "bye"})
                break

            else:
                reply(command_error(cmd, ValueError(f"unsupported command: {cmd}")))
        except Exception as exc:
            reply(command_error(cmd, exc, batch_size=len(req.get("items") or []) if cmd == "remember_batch" else None))


if __name__ == "__main__":
    main()
