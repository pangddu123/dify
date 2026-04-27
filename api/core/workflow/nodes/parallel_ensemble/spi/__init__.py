"""Three-axis SPI (capability / backend / runner / aggregator / trace).

Frozen by P2.1.5 — see `docs/ModelNet/EXTENSIBILITY_SPEC.md` §2-7. Once
this package's public surface lands, downstream tasks (P2.2-P2.10) treat
it as contract: extensions go through subclassing or new registry entries,
not breaking edits to these ABCs / TypedDicts.

Re-exports the public symbols third-party authors are expected to import.
Internal helpers stay on submodules.
"""

from __future__ import annotations

from .aggregator import (
    AggregationContext,
    Aggregator,
    ResponseAggregationResult,
    ResponseAggregator,
    ResponseSignal,
    TokenAggregator,
    TokenPick,
    TokenSignals,
)
from .backend import (
    BackendInfo,
    BaseSpec,
    ChatMessage,
    GenerationParams,
    GenerationResult,
    ModelBackend,
    StreamChunk,
    TokenCandidate,
)
from .capability import Capability
from .requirements import Requirement, ValidationIssue
from .runner import (
    UI_CONTROL_ALLOWLIST,
    DoneEvent,
    EnsembleRunner,
    FullResponseEvent,
    RunnerEvent,
    TokenEvent,
)
from .trace import (
    DiagnosticsConfig,
    EnsembleTrace,
    ResponseTraceEntry,
    ThinkTraceEntry,
    TokenStepTraceEntry,
    TraceCollector,
)

__all__ = [
    "UI_CONTROL_ALLOWLIST",
    # aggregator
    "AggregationContext",
    "Aggregator",
    # backend
    "BackendInfo",
    "BaseSpec",
    # capability
    "Capability",
    "ChatMessage",
    # trace
    "DiagnosticsConfig",
    # runner
    "DoneEvent",
    "EnsembleRunner",
    "EnsembleTrace",
    "FullResponseEvent",
    "GenerationParams",
    "GenerationResult",
    "ModelBackend",
    # requirements
    "Requirement",
    "ResponseAggregationResult",
    "ResponseAggregator",
    "ResponseSignal",
    "ResponseTraceEntry",
    "RunnerEvent",
    "StreamChunk",
    "ThinkTraceEntry",
    "TokenAggregator",
    "TokenCandidate",
    "TokenEvent",
    "TokenPick",
    "TokenSignals",
    "TokenStepTraceEntry",
    "TraceCollector",
    "ValidationIssue",
]
