from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, ClassVar, Literal, Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    from ..aggregator import AggregatedBatch
    from ..config import BackendConfigs

@dataclass(slots=True)
class BackendDescriptor:
    name: str
    version: str
    supports_tags: bool
    max_batch_size: int
    qos: Literal["critical", "best_effort"]

@dataclass(slots=True)
class FlushResult:
    backend: str
    batch_id: str
    success: bool
    retryable: bool
    latency_ms: float
    error: str | None = None

class Backend(Protocol):
    """
    Protocol that all backends must implement.
    """
    name: ClassVar[str]
    supports_tags: ClassVar[bool]
    required: ClassVar[bool]

    async def setup(self, config: Any, *, loop: asyncio.AbstractEventLoop) -> None:
        """
        Initialize the backend.
        config: The specific configuration object for this backend (e.g. LoggerConfig).
        """
        ...

    async def flush(self, batch: AggregatedBatch) -> FlushResult:
        """
        Flush a batch of metrics to the backend.
        """
        ...

    async def shutdown(self) -> None:
        """
        Clean up resources.
        """
        ...

    def describe(self) -> BackendDescriptor:
        """
        Return metadata about the backend.
        """
        ...

class BackendLoadError(RuntimeError):
    """Raised when a backend cannot be loaded."""
    pass
