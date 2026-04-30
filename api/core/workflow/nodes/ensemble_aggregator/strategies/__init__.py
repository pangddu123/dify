from .base import (
    ResponseAggregationResult,
    ResponseAggregator,
    ResponseSignal,
    SourceAggregationContext,
)
from .concat import ConcatStrategy
from .majority_vote import MajorityVoteStrategy
from .registry import get_strategy, list_strategies, register
from .weighted_majority_vote import WeightedMajorityVoteStrategy

__all__ = [
    "ConcatStrategy",
    "MajorityVoteStrategy",
    "ResponseAggregationResult",
    "ResponseAggregator",
    "ResponseSignal",
    "SourceAggregationContext",
    "WeightedMajorityVoteStrategy",
    "get_strategy",
    "list_strategies",
    "register",
]
