"""Token-scope aggregators (PN.py-style ensemble at decode time).

Importing this package executes the ``@register_aggregator`` side
effects so ``sum_score`` and ``max_score`` appear in
``AggregatorRegistry.by_scope("token")``.
"""

from __future__ import annotations

from .max_score import MaxScoreAggregator, MaxScoreConfig
from .sum_score import SumScoreAggregator, SumScoreConfig

__all__ = [
    "MaxScoreAggregator",
    "MaxScoreConfig",
    "SumScoreAggregator",
    "SumScoreConfig",
]
