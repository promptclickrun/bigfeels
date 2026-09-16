"""Adapters for No Memory, Curated Notes, and Simple Lexical Retrieval."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .base_adapter import (
    BaseMemoryAdapter,
    CapabilityProfile,
    NormalizedRecord,
    OpResult,
    RetrievalResult,
    StorageStats,
)


def _tokenize(text: str) -> Set[str]:
    return set(re.findall(r"\w+", text.lower()))


# -----------------------------------------------------------------------------
# Baseline 0: No Memory
# -----------------------------------------------------------------------------
class NoMemoryAdapter(BaseMemoryAdapter):
    name = "no_memory"
    version = "1.0.0"

    def capabilities(self) -> CapabilityProfile:
        return CapabilityProfile(
            store=False,
            retrieve=False,
            evidence=False,
            time=False,
            correct=False,
            forget=False,
            scope=False,
            extract=False,
            restart=False,
            queue=False,
            cost=False,
        )

    def create_isolated_run(
        self, run_id: str, seed: int, spaces: List[str], principals: List[str]
    ) -> Any:
        return None

    def apply_op(self, handle: Any, operation: Dict[str, Any]) -> OpResult:
        # Silently drops or no-ops store since no memory exists
        return OpResult(status="ok")

    def retrieve(
        self,
        handle: Any,
        actor: str,
        query: str,
        as_of: Optional[str] = None,
        budget: int = 3200,
    ) -> RetrievalResult:
        return RetrievalResult(status="ok", records=[], tokens_used=0, latency_ms=0.01)

    def restart(self, handle: Any) -> Any:
        return None

    def get_storage_stats(self, handle: Any) -> StorageStats:
        return StorageStats(disk_bytes=0, record_count=0, wal_bytes=0)

    def teardown(self, handle: Any) -> None:
        pass


# -----------------------------------------------------------------------------
# Baseline 1: Curated Flat Notes (Chronological System Prompt Notes)
# -----------------------------------------------------------------------------
@dataclass
class CuratedHandle:
    notes: List[NormalizedRecord] = field(default_factory=list)


class CuratedNotesAdapter(BaseMemoryAdapter):
    name = "curated_notes"
    version = "1.0.0"

    def capabilities(self) -> CapabilityProfile:
        return CapabilityProfile(
            store=True,
            retrieve=True,
            evidence=False,
            time=False,
            correct=False,
            forget=False,
            scope=False,
            extract=False,
            restart=True,
            queue=False,
            cost=False,
        )

    def create_isolated_run(
        self, run_id: str, seed: int, spaces: List[str], principals: List[str]
    ) -> CuratedHandle:
        return CuratedHandle()

    def apply_op(self, handle: CuratedHandle, operation: Dict[str, Any]) -> OpResult:
        t0 = time.perf_counter()
        op = operation.get("op")
        if op in ("remember", "observe"):
            content = operation.get("content", "")
            rec = NormalizedRecord(
                record_id=f"note_{len(handle.notes)+1}",
                content=content,
                metadata=operation.get("metadata", {}),
            )
            handle.notes.append(rec)
            return OpResult(
                status="ok",
                record_ids=[rec.record_id],
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
        # Curated notes has no native correction/deletion API
        return OpResult(
            status="unsupported",
            safe_diagnostics={"reason": f"op {op} unsupported in curated notes"},
        )

    def retrieve(
        self,
        handle: CuratedHandle,
        actor: str,
        query: str,
        as_of: Optional[str] = None,
        budget: int = 3200,
    ) -> RetrievalResult:
        t0 = time.perf_counter()
        bounded: List[NormalizedRecord] = []
        used_bytes = 0
        # Dumps all stored notes into context up to budget
        for n in handle.notes:
            cost = len(n.content.encode("utf-8")) + 64
            if used_bytes + cost <= budget:
                bounded.append(n)
                used_bytes += cost
            else:
                break
        return RetrievalResult(
            status="ok",
            records=bounded,
            tokens_used=used_bytes,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    def restart(self, handle: CuratedHandle) -> CuratedHandle:
        # In-memory persistence copy
        return CuratedHandle(notes=list(handle.notes))

    def get_storage_stats(self, handle: CuratedHandle) -> StorageStats:
        total_bytes = sum(len(n.content.encode("utf-8")) for n in handle.notes)
        return StorageStats(disk_bytes=total_bytes, record_count=len(handle.notes))

    def teardown(self, handle: CuratedHandle) -> None:
        handle.notes.clear()


# -----------------------------------------------------------------------------
# Baseline 2: Simple Lexical Retrieval (BM25 / Keyword Overlap)
# -----------------------------------------------------------------------------
@dataclass
class LexicalHandle:
    records: List[NormalizedRecord] = field(default_factory=list)


class SimpleLexicalAdapter(BaseMemoryAdapter):
    name = "simple_lexical"
    version = "1.0.0"

    def capabilities(self) -> CapabilityProfile:
        return CapabilityProfile(
            store=True,
            retrieve=True,
            evidence=False,
            time=False,
            correct=False,
            forget=False,
            scope=False,
            extract=False,
            restart=True,
            queue=False,
            cost=False,
        )

    def create_isolated_run(
        self, run_id: str, seed: int, spaces: List[str], principals: List[str]
    ) -> LexicalHandle:
        return LexicalHandle()

    def apply_op(self, handle: LexicalHandle, operation: Dict[str, Any]) -> OpResult:
        t0 = time.perf_counter()
        op = operation.get("op")
        if op in ("remember", "observe"):
            content = operation.get("content", "")
            rec = NormalizedRecord(
                record_id=f"lex_{len(handle.records)+1}",
                content=content,
                metadata=operation.get("metadata", {}),
            )
            handle.records.append(rec)
            return OpResult(
                status="ok",
                record_ids=[rec.record_id],
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
        return OpResult(
            status="unsupported",
            safe_diagnostics={"reason": f"op {op} unsupported in simple lexical"},
        )

    def retrieve(
        self,
        handle: LexicalHandle,
        actor: str,
        query: str,
        as_of: Optional[str] = None,
        budget: int = 3200,
    ) -> RetrievalResult:
        t0 = time.perf_counter()
        qterms = _tokenize(query)
        scored: List[tuple[int, NormalizedRecord]] = []
        for r in handle.records:
            rterms = _tokenize(r.content)
            overlap = len(qterms & rterms)
            if overlap > 0:
                scored.append((overlap, r))

        scored.sort(key=lambda x: x[0], reverse=True)

        bounded: List[NormalizedRecord] = []
        used_bytes = 0
        for _, r in scored:
            cost = len(r.content.encode("utf-8")) + 64
            if used_bytes + cost <= budget:
                bounded.append(r)
                used_bytes += cost
            else:
                break

        return RetrievalResult(
            status="ok",
            records=bounded,
            tokens_used=used_bytes,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    def restart(self, handle: LexicalHandle) -> LexicalHandle:
        return LexicalHandle(records=list(handle.records))

    def get_storage_stats(self, handle: LexicalHandle) -> StorageStats:
        total_bytes = sum(len(r.content.encode("utf-8")) for r in handle.records)
        return StorageStats(disk_bytes=total_bytes, record_count=len(handle.records))

    def teardown(self, handle: LexicalHandle) -> None:
        handle.records.clear()
