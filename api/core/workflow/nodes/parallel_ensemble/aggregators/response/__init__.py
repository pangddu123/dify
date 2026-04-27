"""Response-scope aggregators (v0.2 smooth migration of P1 strategies).

Importing this package executes the ``@register_aggregator`` side
effects for ``majority_vote`` + ``concat`` so they appear in
``AggregatorRegistry.by_scope("response")``.
"""

from __future__ import annotations

from .concat import ConcatAggregator, ConcatConfig
from .majority_vote import MajorityVoteAggregator, MajorityVoteConfig

__all__ = [
    "ConcatAggregator",
    "ConcatConfig",
    "MajorityVoteAggregator",
    "MajorityVoteConfig",
]
