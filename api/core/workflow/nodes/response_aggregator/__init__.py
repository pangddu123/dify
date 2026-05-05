"""Response aggregator workflow node package."""

RESPONSE_AGGREGATOR_NODE_TYPE = "response-aggregator"

from .node import ResponseAggregatorNode

__all__ = ["RESPONSE_AGGREGATOR_NODE_TYPE", "ResponseAggregatorNode"]
