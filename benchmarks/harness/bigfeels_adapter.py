"""Direct adapter for bigfeels Store using public local Store interface."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys
# Ensure src is on path for bigfeels_mem import
_SRC_DIR = str(Path(__file__).resolve().parent.parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from bigfeels_mem import __version__ as BIGFEELS_VERSION  # noqa: E402
from bigfeels_mem.store import Store  # noqa: E402
from .base_adapter import (  # noqa: E402
    BaseMemoryAdapter,
    CapabilityProfile,
    NormalizedRecord,
    OpResult,
    RetrievalResult,
    StorageStats,
)


@dataclass
class BigfeelsHandle:
    temp_dir: str
    db_path: Path
    store: Store
    tokens: Dict[str, str]  # principal -> token
    step_ids: Dict[int, str]  # step_num -> memory_id


class BigfeelsAdapter(BaseMemoryAdapter):
    name = "bigfeels"
    version = BIGFEELS_VERSION

    def capabilities(self) -> CapabilityProfile:
        return CapabilityProfile(
            store=True,
            retrieve=True,
            evidence=True,
            time=True,
            correct=True,
            forget=True,
            scope=True,
            extract=True,
            restart=True,
            queue=True,
            cost=True,
        )

    def create_isolated_run(
        self, run_id: str, seed: int, spaces: List[str], principals: List[str]
    ) -> BigfeelsHandle:
        temp_dir = tempfile.mkdtemp(prefix=f"bigfeels-eval-{run_id}-", dir="/tmp")
        db_path = Path(temp_dir) / "bigfeels.sqlite"
        store = Store(db_path)

        tokens: Dict[str, str] = {}
        for sp in spaces:
            store.create_space(sp)

        # Pair principals with appropriate space permissions
        for p in principals:
            # Grant spaces based on role convention or all requested spaces
            if "restricted" in p:
                allowed = [s for s in spaces if "public" in s or "cleared" in s]
            else:
                allowed = list(spaces)
            if not allowed:
                allowed = ["workspace:default"]
            tokens[p] = store.pair(p, allowed)

        return BigfeelsHandle(
            temp_dir=temp_dir,
            db_path=db_path,
            store=store,
            tokens=tokens,
            step_ids={},
        )

    def _get_auth(self, handle: BigfeelsHandle, actor: str) -> Any:
        tok = handle.tokens.get(actor)
        if not tok:
            # If actor not paired yet, pair with default permissions
            tok = handle.store.pair(actor, ["workspace:default"])
            handle.tokens[actor] = tok
        return handle.store.authenticate(tok)

    def apply_op(self, handle: BigfeelsHandle, operation: Dict[str, Any]) -> OpResult:
        t0 = time.perf_counter()
        op = operation.get("op")
        actor = operation.get("actor", "agent-a")
        space = operation.get("space", "workspace:default")
        auth = self._get_auth(handle, actor)
        step = operation.get("step", 0)

        try:
            if op == "remember":
                content = operation.get("content", "")
                meta = operation.get("metadata", {})
                params: Dict[str, Any] = {
                    "space": space,
                    "content": content,
                }
                if "key" in meta:
                    params["key"] = meta["key"]
                if "valid_from" in meta:
                    params["valid_from"] = meta["valid_from"]
                if "valid_until" in meta:
                    params["valid_until"] = meta["valid_until"]
                if "basis" in meta:
                    params["basis"] = meta["basis"]
                if "kind" in meta:
                    params["kind"] = meta["kind"]
                if "outcome" in meta:
                    params["outcome"] = meta["outcome"]

                res = handle.store.dispatch(auth, "remember", params)
                mem_id = res["id"]
                handle.step_ids[step] = mem_id
                return OpResult(
                    status="ok",
                    record_ids=[mem_id],
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )

            elif op == "correct":
                target_step = operation.get("target_step", 1)
                target_id = handle.step_ids.get(target_step)
                if not target_id:
                    return OpResult(
                        status="error",
                        safe_diagnostics={"error": f"target step {target_step} not found"},
                    )
                content = operation.get("content", "")
                meta = operation.get("metadata", {})
                params = {
                    "id": target_id,
                    "revision": meta.get("revision", 1),
                    "content": content,
                }
                if "valid_from" in meta:
                    params["valid_from"] = meta["valid_from"]
                res = handle.store.dispatch(auth, "correct", params)
                new_id = res["id"]
                handle.step_ids[step] = new_id
                return OpResult(
                    status="ok",
                    record_ids=[new_id],
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )

            elif op == "forget":
                target_step = operation.get("target_step", 1)
                target_id = handle.step_ids.get(target_step)
                if not target_id:
                    return OpResult(
                        status="error",
                        safe_diagnostics={"error": f"target step {target_step} not found"},
                    )
                res = handle.store.dispatch(auth, "forget", {"id": target_id})
                return OpResult(
                    status="ok",
                    record_ids=[target_id],
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    safe_diagnostics={"result": res},
                )

            else:
                return OpResult(
                    status="unsupported",
                    safe_diagnostics={"reason": f"op {op} unsupported in bigfeels adapter"},
                )

        except Exception as e:
            return OpResult(
                status="error",
                latency_ms=(time.perf_counter() - t0) * 1000,
                safe_diagnostics={"exception": str(e)},
            )

    def retrieve(
        self,
        handle: BigfeelsHandle,
        actor: str,
        query: str,
        as_of: Optional[str] = None,
        budget: int = 3200,
    ) -> RetrievalResult:
        t0 = time.perf_counter()
        auth = self._get_auth(handle, actor)
        params: Dict[str, Any] = {"query": query, "budget": budget}
        if as_of:
            params["as_of"] = as_of

        try:
            res = handle.store.dispatch(auth, "context", params)
            recs: List[NormalizedRecord] = []
            for m in res.get("memories", []):
                recs.append(
                    NormalizedRecord(
                        record_id=m["id"],
                        content=m["content"],
                        evidence_ids=m.get("evidence_ids", []),
                        status=m.get("status", "active"),
                        valid_from=m.get("valid_from"),
                        valid_until=m.get("valid_until"),
                        space=m.get("space", "default"),
                    )
                )
            return RetrievalResult(
                status="ok",
                records=recs,
                tokens_used=res.get("tokens", 0),
                latency_ms=(time.perf_counter() - t0) * 1000,
                safe_diagnostics=res.get("trace", {}),
            )
        except Exception as e:
            return RetrievalResult(
                status="error",
                latency_ms=(time.perf_counter() - t0) * 1000,
                safe_diagnostics={"exception": str(e)},
            )

    def restart(self, handle: BigfeelsHandle) -> BigfeelsHandle:
        # Re-instantiate Store over the exact same SQLite database
        new_store = Store(handle.db_path)
        return BigfeelsHandle(
            temp_dir=handle.temp_dir,
            db_path=handle.db_path,
            store=new_store,
            tokens=dict(handle.tokens),
            step_ids=dict(handle.step_ids),
        )

    def get_storage_stats(self, handle: BigfeelsHandle) -> StorageStats:
        p = handle.db_path
        disk_bytes = p.stat().st_size if p.exists() else 0
        wal_p = Path(str(p) + "-wal")
        wal_bytes = wal_p.stat().st_size if wal_p.exists() else 0
        return StorageStats(disk_bytes=disk_bytes, wal_bytes=wal_bytes)

    def teardown(self, handle: BigfeelsHandle) -> None:
        try:
            shutil.rmtree(handle.temp_dir, ignore_errors=True)
        except Exception:
            pass
