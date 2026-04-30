"""Shared fixtures for P2.5 aggregator tests.

Builds an :class:`AggregationContext` with arbitrary defaults so each
test can pass a single ``ctx()`` call instead of repeating the boilerplate
for every signal shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.workflow.nodes.parallel_ensemble.spi import (
    AggregationContext,
    DiagnosticsConfig,
    TraceCollector,
)


@pytest.fixture
def make_ctx():
    """Return a factory that builds a minimal ``AggregationContext``.

    Defaults are wide-open: empty backends list, equal weights set by
    the caller, ``runner_name`` is generic. Tests override only the
    fields that matter to them.
    """

    def _build(
        weights: dict[str, float] | None = None,
        runner_name: str = "test_runner",
        runner_config: dict | None = None,
        step_index: int | None = 0,
    ) -> AggregationContext:
        diagnostics = DiagnosticsConfig()
        trace = TraceCollector(diagnostics)
        # SourceAggregationContext fields (sources/source_meta/strategy_config)
        # added in v3 P3.A.1 — derive ``sources`` from the weights dict so
        # existing test callers don't need to pass it.
        resolved_weights = weights or {}
        return AggregationContext(
            sources=list(resolved_weights.keys()),
            source_meta={},
            strategy_config={},
            backends=[],
            weights=resolved_weights,
            capabilities={},
            runner_name=runner_name,
            runner_config=runner_config or {},
            trace=trace,
            elapsed_ms_so_far=0,
            step_index=step_index,
        )

    return _build


@pytest.fixture
def cand():
    """Build a TokenCandidate dict with required fields filled."""

    def _build(token: str, prob: float, logit: float | None = None) -> dict[str, Any]:
        return {"token": token, "prob": prob, "logit": logit}

    return _build
