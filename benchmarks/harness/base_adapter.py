"""Abstract contract and data models for memory evaluation adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CapabilityProfile:
    store: bool = True
    retrieve: bool = True
    evidence: bool = False
    time: bool = False
    correct: bool = False
    forget: bool = False
    scope: bool = False
    extract: bool = False
    restart: bool = False
    queue: bool = False
    cost: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "store": self.store,
            "retrieve": self.retrieve,
            "evidence": self.evidence,
            "time": self.time,
            "correct": self.correct,
            "forget": self.forget,
            "scope": self.scope,
            "extract": self.extract,
            "restart": self.restart,
            "queue": self.queue,
            "cost": self.cost,
        }


@dataclass
class NormalizedRecord:
    record_id: str
    content: str
    evidence_ids: List[str] = field(default_factory=list)
    status: str = "active"
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    space: str = "default"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OpResult:
    status: str  # "ok", "unsupported", "error"
    record_ids: List[str] = field(default_factory=list)
    latency_ms: float = 0.0
    provider_calls: int = 0
    safe_diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    status: str  # "ok", "unsupported", "error"
    records: List[NormalizedRecord] = field(default_factory=list)
    tokens_used: int = 0
    latency_ms: float = 0.0
    provider_calls: int = 0
    safe_diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StorageStats:
    disk_bytes: int = 0
    record_count: int = 0
    wal_bytes: int = 0
    safe_diagnostics: Dict[str, Any] = field(default_factory=dict)


class BaseMemoryAdapter(ABC):
    """Host-neutral abstract adapter interface for memory benchmarks."""

    name: str = "base"
    version: str = "0.0.0"

    @abstractmethod
    def capabilities(self) -> CapabilityProfile:
        """Return declared capabilities."""
        pass

    @abstractmethod
    def create_isolated_run(
        self, run_id: str, seed: int, spaces: List[str], principals: List[str]
    ) -> Any:
        """Initialize isolated environment and return handle."""
        pass

    @abstractmethod
    def apply_op(self, handle: Any, operation: Dict[str, Any]) -> OpResult:
        """Apply a case operation (remember, correct, forget, etc.)."""
        pass

    @abstractmethod
    def retrieve(
        self,
        handle: Any,
        actor: str,
        query: str,
        as_of: Optional[str] = None,
        budget: int = 3200,
    ) -> RetrievalResult:
        """Query memory within budget."""
        pass

    @abstractmethod
    def restart(self, handle: Any) -> Any:
        """Restart client/process over same isolated durable storage."""
        pass

    @abstractmethod
    def get_storage_stats(self, handle: Any) -> StorageStats:
        """Report storage footprint."""
        pass

    @abstractmethod
    def teardown(self, handle: Any) -> None:
        """Clean up isolated state completely."""
        pass
