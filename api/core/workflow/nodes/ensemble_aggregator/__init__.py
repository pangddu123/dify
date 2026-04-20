"""Ensemble aggregator workflow node package."""

ENSEMBLE_AGGREGATOR_NODE_TYPE = "ensemble-aggregator"

from .node import EnsembleAggregatorNode  # noqa: E402  (must follow NODE_TYPE constant)

__all__ = ["ENSEMBLE_AGGREGATOR_NODE_TYPE", "EnsembleAggregatorNode"]
