"""Ensemble aggregator workflow node package."""

ENSEMBLE_AGGREGATOR_NODE_TYPE = "ensemble-aggregator"

from .node import EnsembleAggregatorNode

__all__ = ["ENSEMBLE_AGGREGATOR_NODE_TYPE", "EnsembleAggregatorNode"]
