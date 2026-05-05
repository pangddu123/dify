import logging
import math
from collections.abc import Generator, Mapping, Sequence
from typing import Any, ClassVar

from pydantic import ValidationError

from graphon.enums import NodeType, WorkflowNodeExecutionStatus
from graphon.node_events.base import NodeEventBase, NodeRunResult
from graphon.node_events.node import StreamCompletedEvent
from graphon.nodes.base.node import Node

from . import RESPONSE_AGGREGATOR_NODE_TYPE
from .entities import AggregationInputRef, ResponseAggregatorNodeData
from .exceptions import (
    ResponseAggregatorNodeError,
    MissingInputError,
    StrategyConfigError,
    WeightResolutionError,
)
from .strategies import (
    ResponseSignal,
    SourceAggregationContext,
    get_strategy,
)

logger = logging.getLogger(__name__)


class ResponseAggregatorNode(Node[ResponseAggregatorNodeData]):
    node_type: ClassVar[NodeType] = RESPONSE_AGGREGATOR_NODE_TYPE

    @classmethod
    def version(cls) -> str:
        return "1"

    def _run(self) -> Generator[NodeEventBase, None, None]:
        node_data = self.node_data
        strategy_name = node_data.strategy_name
        declared_source_count = len(node_data.inputs)

        try:
            signals, weights, source_meta, weight_fallbacks = self._collect_inputs()
            strategy = get_strategy(strategy_name)
            try:
                parsed_config = strategy.config_class.model_validate(
                    node_data.strategy_config
                )
            except ValidationError as e:
                raise StrategyConfigError(strategy_name, str(e)) from e

            context = SourceAggregationContext(
                sources=[s["source_id"] for s in signals],
                weights=weights,
                source_meta=source_meta,
                strategy_config=dict(node_data.strategy_config),
            )
            result = strategy.aggregate(signals, context, parsed_config)
        except ResponseAggregatorNodeError as e:
            logger.warning(
                "ResponseAggregatorNode %s failed: %s", self._node_id, e, exc_info=True
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

        # ``weight_fallbacks`` lists every source whose dynamic weight
        # selector failed and was rescued by ``fallback_weight``. Lives on
        # ``process_data`` (the graphon trace-equivalent surface for
        # nodes without a TraceCollector) per ADR-v3-15 — empty in the
        # happy path; non-empty entries surface in the single-step debug
        # panel under "process data" so silent degrades stay visible.
        process_data: dict[str, Any] = {}
        if weight_fallbacks:
            process_data["weight_fallback_warnings"] = weight_fallbacks

        yield StreamCompletedEvent(
            node_run_result=NodeRunResult(
                status=WorkflowNodeExecutionStatus.SUCCEEDED,
                inputs={
                    "source_count": len(signals),
                    "strategy": strategy_name,
                },
                process_data=process_data,
                outputs={
                    "text": result["text"],
                    "metadata": result["metadata"],
                },
            ),
        )

    def _collect_inputs(
        self,
    ) -> tuple[
        list[ResponseSignal],
        dict[str, float],
        dict[str, dict],
        list[dict[str, Any]],
    ]:
        """Read upstream texts + resolve per-source weights.

        Returns:
            signals: ``ResponseSignal`` rows the strategy receives.
            weights: ``source_id`` → effective float weight, exposed via
                ``SourceAggregationContext.weights``.
            source_meta: ``source_id`` → ``ref.extra`` pass-through.
            weight_fallbacks: per-source fallback events (surfaced on
                ``process_data`` by ``_run`` so silent degrades are visible
                to single-step debug — empty in the happy path).

        Failure modes (ADR-v3-15 fail-fast):
            * Missing upstream variable → ``MissingInputError``.
            * Dynamic weight selector unresolvable AND no
              ``fallback_weight`` → ``WeightResolutionError``.
            * Dynamic weight selector unresolvable WITH
              ``fallback_weight`` → use fallback, log warning, append
              to ``weight_fallbacks``.
        """
        variable_pool = self.graph_runtime_state.variable_pool
        signals: list[ResponseSignal] = []
        weights: dict[str, float] = {}
        source_meta: dict[str, dict] = {}
        weight_fallbacks: list[dict[str, Any]] = []

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
            signals.append(
                ResponseSignal(
                    source_id=ref.source_id,
                    text=segment.text,
                    finish_reason="stop",
                    elapsed_ms=0,
                    error=None,
                )
            )

            weights[ref.source_id] = self._resolve_weight(
                ref, variable_pool, weight_fallbacks
            )
            source_meta[ref.source_id] = dict(ref.extra)

        return signals, weights, source_meta, weight_fallbacks

    def _resolve_weight(
        self,
        ref: AggregationInputRef,
        variable_pool: Any,
        weight_fallbacks: list[dict[str, Any]],
    ) -> float:
        """Resolve ``ref.weight`` to a float.

        Static numeric branch returns directly. Dynamic
        ``VariableSelector``-shaped list branch reads the pool and
        coerces to float; coercion failure escalates to
        ``WeightResolutionError`` unless ``fallback_weight`` opts into
        the graceful-degrade path (ADR-v3-15).
        """
        weight_value = ref.weight
        if isinstance(weight_value, (int, float)):
            return float(weight_value)

        # Dynamic selector branch.
        selector = list(weight_value)
        try:
            segment = variable_pool.get(selector)
            if segment is None:
                raise WeightResolutionError(
                    input_id=ref.source_id,
                    selector=selector,
                    reason="variable not present in pool",
                )
            value = segment.value
            if value is None:
                raise WeightResolutionError(
                    input_id=ref.source_id,
                    selector=selector,
                    reason="resolved value is None",
                )
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                # ``bool`` is a subclass of ``int`` in Python — exclude
                # explicitly so `True`/`False` aren't silently coerced
                # to 1.0/0.0 (would mask schema drift upstream).
                raise WeightResolutionError(
                    input_id=ref.source_id,
                    selector=selector,
                    reason=f"resolved value is not numeric (got {type(value).__name__})",
                )
            resolved = float(value)
            if not math.isfinite(resolved):
                raise WeightResolutionError(
                    input_id=ref.source_id,
                    selector=selector,
                    reason=f"resolved value is not finite (got {resolved})",
                )
            return resolved
        except WeightResolutionError as exc:
            if ref.fallback_weight is None:
                raise
            # Graceful-degrade path: log + append a per-source record so
            # single-step debug users see the fallback fired without
            # having to grep the logs.
            logger.warning(
                "ResponseAggregatorNode %s: weight selector for source '%s' "
                "failed (%s); falling back to %s",
                self._node_id,
                ref.source_id,
                exc.reason,
                ref.fallback_weight,
            )
            weight_fallbacks.append(
                {
                    "source_id": ref.source_id,
                    "selector": selector,
                    "reason": exc.reason,
                    "fallback_weight": ref.fallback_weight,
                }
            )
            return float(ref.fallback_weight)

    @classmethod
    def _extract_variable_selector_to_variable_mapping(
        cls,
        *,
        graph_config: Mapping[str, Any],
        node_id: str,
        node_data: ResponseAggregatorNodeData,
    ) -> Mapping[str, Sequence[str]]:
        # Expose each input's upstream selector to the draft-variable preload
        # path (workflow_entry / workflow_app_runner). source_id is unique per
        # node (enforced in entities.py), so {node_id}.inputs.{source_id} is a
        # stable unique key — same shape as knowledge_retrieval_node.py:314.
        # Dynamic ``weight`` selectors are also surfaced so the variable
        # is preloaded ahead of resolution at runtime (ADR-v3-15).
        mapping: dict[str, Sequence[str]] = {}
        for ref in node_data.inputs:
            mapping[f"{node_id}.inputs.{ref.source_id}"] = list(ref.variable_selector)
            if isinstance(ref.weight, list):
                mapping[f"{node_id}.inputs.{ref.source_id}.weight"] = list(ref.weight)
        return mapping
