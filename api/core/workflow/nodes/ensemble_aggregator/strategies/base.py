"""Strategy base — v3 SPI convergence (ADR-v3-9).

The local ``concat`` strategy inherits ``ResponseAggregator`` from
``parallel_ensemble.spi.aggregator``; ``aggregate(signals, context, config)``
takes a ``SourceAggregationContext`` so strategies see weights /
source_meta / strategy_config but never reach into runner / backend
internals (those live on ``BackendAggregationContext``).

We re-use ``ResponseSignal``
(``source_id`` / ``text`` / ``finish_reason`` / ``elapsed_ms`` / ``error``)
so a strategy added to this node could land unchanged in the
parallel_ensemble response path.
"""

from __future__ import annotations

from core.workflow.nodes.parallel_ensemble.spi.aggregator import (
    ResponseAggregationResult,
    ResponseAggregator,
    ResponseSignal,
    SourceAggregationContext,
)

__all__ = [
    "ResponseAggregationResult",
    "ResponseAggregator",
    "ResponseSignal",
    "SourceAggregationContext",
]
