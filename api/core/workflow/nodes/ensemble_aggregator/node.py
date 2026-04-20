import logging
from collections.abc import Generator, Mapping, Sequence
from typing import Any, ClassVar

from graphon.enums import NodeType, WorkflowNodeExecutionStatus
from graphon.node_events.base import NodeEventBase, NodeRunResult
from graphon.node_events.node import StreamCompletedEvent
from graphon.nodes.base.node import Node

from . import ENSEMBLE_AGGREGATOR_NODE_TYPE
from .entities import EnsembleAggregatorNodeData
from .exceptions import (
    EnsembleAggregatorNodeError,
    MissingInputError,
)
from .strategies import AggregationInput, get_strategy

logger = logging.getLogger(__name__)


class EnsembleAggregatorNode(Node[EnsembleAggregatorNodeData]):
    node_type: ClassVar[NodeType] = ENSEMBLE_AGGREGATOR_NODE_TYPE

    @classmethod
    def version(cls) -> str:
        return "1"

    def _run(self) -> Generator[NodeEventBase, None, None]:
        node_data = self.node_data
        strategy_name = node_data.strategy_name
        declared_source_count = len(node_data.inputs)

        try:
            aggregation_inputs = self._collect_inputs()
            strategy = get_strategy(strategy_name)
            result = strategy.aggregate(aggregation_inputs, node_data.strategy_config)
        except EnsembleAggregatorNodeError as e:
            logger.warning(
                "EnsembleAggregatorNode %s failed: %s", self._node_id, e, exc_info=True
            )
            yield StreamCompletedEvent(
                node_run_result=NodeRunResult(
                    status=WorkflowNodeExecutionStatus.FAILED,
                    inputs={
                        "source_count": declared_source_count,
                        "strategy": strategy_name,
                    },
                    error=str(e),
                    error_type=type(e).__name__,
                ),
            )
            return

        yield StreamCompletedEvent(
            node_run_result=NodeRunResult(
                status=WorkflowNodeExecutionStatus.SUCCEEDED,
                inputs={
                    "source_count": len(aggregation_inputs),
                    "strategy": strategy_name,
                },
                outputs={
                    "text": result["text"],
                    "metadata": result["metadata"],
                },
            ),
        )

    def _collect_inputs(self) -> list[AggregationInput]:
        variable_pool = self.graph_runtime_state.variable_pool
        collected: list[AggregationInput] = []
        for ref in self.node_data.inputs:
            segment = variable_pool.get(ref.variable_selector)
            if segment is None:
                raise MissingInputError(
                    source_id=ref.source_id,
                    variable_selector=list(ref.variable_selector),
                )
            # Use Segment.text (graphon canonical text rendering) rather than
            # str(segment.value): the former normalizes NoneSegment -> "",
            # ObjectSegment/ArrayStringSegment -> JSON, empty arrays -> "",
            # keeping this node aligned with how graphon's other nodes render
            # variables.
            collected.append({"source_id": ref.source_id, "text": segment.text})
        return collected

    @classmethod
    def _extract_variable_selector_to_variable_mapping(
        cls,
        *,
        graph_config: Mapping[str, Any],
        node_id: str,
        node_data: EnsembleAggregatorNodeData,
    ) -> Mapping[str, Sequence[str]]:
        # Expose each input's upstream selector to the draft-variable preload
        # path (workflow_entry / workflow_app_runner). source_id is unique per
        # node (enforced in entities.py), so {node_id}.inputs.{source_id} is a
        # stable unique key — same shape as knowledge_retrieval_node.py:314.
        return {
            f"{node_id}.inputs.{ref.source_id}": list(ref.variable_selector)
            for ref in node_data.inputs
        }
