"""Unit tests for ResponseAggregatorNode behaviors that the schema
(test_entities.py) cannot catch.

Covers:
- Segment.text normalization for NoneSegment/ObjectSegment/ArrayStringSegment (P1.3 review r2)
- extract_variable_selector_to_variable_mapping exposing each input selector (P1.3 review r2)
- End-to-end `_run` with mock VariablePool: happy paths + FAILED paths (P1.4)
"""

import pytest

from core.workflow.nodes.response_aggregator import ResponseAggregatorNode
from core.workflow.nodes.response_aggregator.exceptions import (
    MissingInputError,
    StrategyConfigError,
    StrategyNotFoundError,
    WeightResolutionError,
)
from graphon.enums import WorkflowNodeExecutionStatus
from graphon.runtime.variable_pool import VariablePool


def _make_node(pool: VariablePool, node_data_payload: dict) -> ResponseAggregatorNode:
    """Build a node bypassing Node.__init__ (which needs full graph_init_params).

    We only exercise `_run` / `_collect_inputs`, which read `_node_data`,
    `_node_id`, and `graph_runtime_state.variable_pool`.
    """
    node = ResponseAggregatorNode.__new__(ResponseAggregatorNode)
    node._node_id = "agg_1"

    class _RS:
        pass

    rs = _RS()
    rs.variable_pool = pool
    node.graph_runtime_state = rs
    node._node_data = ResponseAggregatorNode._node_data_type.model_validate(
        node_data_payload
    )
    return node


def _default_node_data(selectors: list[tuple[str, list[str]]]) -> dict:
    return {
        "title": "agg",
        "inputs": [
            {"source_id": sid, "variable_selector": sel} for sid, sel in selectors
        ],
        "strategy_name": "concat",
        "strategy_config": {"include_source_label": True, "separator": " || "},
    }


class TestSegmentTextNormalization:
    """_collect_inputs must use Segment.text (graphon canonical) not
    str(segment.value) — otherwise NoneSegment/ObjectSegment/ArrayStringSegment
    diverge from how other graphon nodes render those variables."""

    def test_none_segment_renders_as_empty_string(self):
        pool = VariablePool()
        pool.add(["llm_a", "text"], "real")
        pool.add(["llm_b", "text"], None)  # → NoneSegment, .text == ""
        node = _make_node(
            pool,
            _default_node_data(
                [("a", ["llm_a", "text"]), ("b", ["llm_b", "text"])]
            ),
        )

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.outputs["text"] == "[a]\nreal || [b]\n"

    def test_object_segment_renders_as_json_not_python_repr(self):
        pool = VariablePool()
        pool.add(["llm_a", "text"], "hello")
        pool.add(["llm_b", "text"], {"city": "Paris", "score": 0.9})
        node = _make_node(
            pool,
            _default_node_data(
                [("a", ["llm_a", "text"]), ("b", ["llm_b", "text"])]
            ),
        )

        events = list(node._run())
        text = events[0].node_run_result.outputs["text"]
        assert "[b]" in text
        # JSON form uses double quotes; Python str(dict) uses single quotes.
        assert '"city": "Paris"' in text
        assert "'city': 'Paris'" not in text

    def test_array_string_segment_renders_as_json_not_python_repr(self):
        pool = VariablePool()
        pool.add(["llm_a", "text"], "alpha")
        pool.add(["llm_b", "text"], ["one", "two", "three"])
        node = _make_node(
            pool,
            _default_node_data(
                [("a", ["llm_a", "text"]), ("b", ["llm_b", "text"])]
            ),
        )

        events = list(node._run())
        text = events[0].node_run_result.outputs["text"]
        # JSON: ["one", "two", "three"] (double quotes). Python str: ['one', ...].
        assert '["one", "two", "three"]' in text
        assert "['one', 'two', 'three']" not in text

    def test_empty_array_renders_as_empty_string(self):
        # ArraySegment.text specializes empty arrays to "" rather than "[]".
        pool = VariablePool()
        pool.add(["llm_a", "text"], "kept")
        pool.add(["llm_b", "text"], [])
        node = _make_node(
            pool,
            _default_node_data(
                [("a", ["llm_a", "text"]), ("b", ["llm_b", "text"])]
            ),
        )

        events = list(node._run())
        text = events[0].node_run_result.outputs["text"]
        assert text == "[a]\nkept || [b]\n"


class TestExtractVariableSelectorMapping:
    """_extract_variable_selector_to_variable_mapping must expose every
    inputs[*].variable_selector so single-step debug + draft-variable preload
    (workflow_entry / workflow_app_runner) can load upstream vars before _run."""

    def _build_config(self, node_id: str, inputs_payload: list[dict]) -> dict:
        return {
            "id": node_id,
            "data": {
                "title": "agg",
                "inputs": inputs_payload,
                "strategy_name": "concat",
                "strategy_config": {},
            },
        }

    def test_mapping_exposes_each_input_selector(self):
        config = self._build_config(
            "agg_node_1",
            [
                {"source_id": "a", "variable_selector": ["llm_a", "text"]},
                {"source_id": "b", "variable_selector": ["llm_b", "text"]},
                {"source_id": "c", "variable_selector": ["llm_c", "text"]},
            ],
        )
        mapping = ResponseAggregatorNode.extract_variable_selector_to_variable_mapping(
            graph_config={}, config=config
        )

        assert dict(mapping) == {
            "agg_node_1.inputs.a": ["llm_a", "text"],
            "agg_node_1.inputs.b": ["llm_b", "text"],
            "agg_node_1.inputs.c": ["llm_c", "text"],
        }

    def test_mapping_is_never_empty_for_valid_node(self):
        # Regression guard against the default Node base implementation that
        # returns {} and silently breaks the preload pipeline.
        config = self._build_config(
            "n1",
            [
                {"source_id": "x", "variable_selector": ["up1", "text"]},
                {"source_id": "y", "variable_selector": ["up2", "text"]},
            ],
        )
        mapping = ResponseAggregatorNode.extract_variable_selector_to_variable_mapping(
            graph_config={}, config=config
        )
        assert len(mapping) == 2

    def test_mapping_preserves_multi_segment_selectors(self):
        # Selectors of length >= 3 (e.g. nested ObjectSegment paths) must be
        # preserved verbatim — not truncated or mangled.
        config = self._build_config(
            "n1",
            [
                {
                    "source_id": "deep",
                    "variable_selector": ["llm_a", "structured_output", "city"],
                },
                {"source_id": "shallow", "variable_selector": ["llm_b", "text"]},
            ],
        )
        mapping = ResponseAggregatorNode.extract_variable_selector_to_variable_mapping(
            graph_config={}, config=config
        )
        assert mapping["n1.inputs.deep"] == [
            "llm_a",
            "structured_output",
            "city",
        ]
        assert mapping["n1.inputs.shallow"] == ["llm_b", "text"]


class TestRunHappyPath:
    """End-to-end `_run()` with a real VariablePool feeding 3 upstream texts.

    Asserts event sequence (exactly 1 StreamCompletedEvent), status=SUCCEEDED,
    outputs/inputs shape, and strategy metadata payload.
    """

    @staticmethod
    def _three_text_pool(texts: dict[str, str]) -> VariablePool:
        pool = VariablePool()
        for node_id, text in texts.items():
            pool.add([node_id, "text"], text)
        return pool

    @staticmethod
    def _node_data(strategy_name: str, strategy_config: dict | None = None) -> dict:
        return {
            "title": "agg",
            "inputs": [
                {"source_id": "gpt4", "variable_selector": ["llm_a", "text"]},
                {"source_id": "claude", "variable_selector": ["llm_b", "text"]},
                {"source_id": "llama", "variable_selector": ["llm_c", "text"]},
            ],
            "strategy_name": strategy_name,
            "strategy_config": strategy_config or {},
        }

    def test_concat_default_separator(self):
        pool = self._three_text_pool(
            {"llm_a": "first", "llm_b": "second", "llm_c": "third"}
        )
        node = _make_node(pool, self._node_data("concat"))

        events = list(node._run())

        assert len(events) == 1
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        assert nrr.outputs["text"] == "first\n\n---\n\nsecond\n\n---\n\nthird"

        metadata = nrr.outputs["metadata"]
        assert metadata["strategy"] == "concat"
        assert metadata["separator"] == "\n\n---\n\n"
        assert metadata["include_source_label"] is False
        assert metadata["contributions"] == {
            "gpt4": "first",
            "claude": "second",
            "llama": "third",
        }

    def test_concat_with_source_label_and_custom_separator(self):
        pool = self._three_text_pool(
            {"llm_a": "hello", "llm_b": "world", "llm_c": "!"}
        )
        node = _make_node(
            pool,
            self._node_data(
                "concat",
                {"include_source_label": True, "separator": " | "},
            ),
        )

        events = list(node._run())

        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        assert (
            nrr.outputs["text"]
            == "[gpt4]\nhello | [claude]\nworld | [llama]\n!"
        )
        assert nrr.outputs["metadata"]["include_source_label"] is True


class TestRunFailurePaths:
    """FAILED event path: node catches ResponseAggregatorNodeError descendants
    and emits exactly one StreamCompletedEvent with status=FAILED + error_type
    carrying the exception class name."""

    @staticmethod
    def _two_inputs_payload(strategy_name: str, strategy_config: dict | None = None) -> dict:
        return {
            "title": "agg",
            "inputs": [
                {"source_id": "a", "variable_selector": ["llm_a", "text"]},
                {"source_id": "b", "variable_selector": ["llm_b", "text"]},
            ],
            "strategy_name": strategy_name,
            "strategy_config": strategy_config or {},
        }

    def test_missing_upstream_input_becomes_failed_event(self):
        pool = VariablePool()
        pool.add(["llm_a", "text"], "only-a-present")
        # llm_b deliberately not added.
        node = _make_node(pool, self._two_inputs_payload("concat"))

        events = list(node._run())

        assert len(events) == 1
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "MissingInputError"
        assert "llm_b" in nrr.error or "'b'" in nrr.error
        # inputs metadata still populated for observability of the failed run.
        assert nrr.inputs == {"source_count": 2, "strategy": "concat"}
        # Outputs not set on failure.
        assert nrr.outputs == {}

    def test_invalid_strategy_config_becomes_failed_event(self):
        pool = VariablePool()
        pool.add(["llm_a", "text"], "x")
        pool.add(["llm_b", "text"], "y")
        # `bogus` is rejected by ConcatStrategy's extra="forbid" config.
        node = _make_node(
            pool,
            self._two_inputs_payload(
                "concat", {"bogus": 42}
            ),
        )

        events = list(node._run())

        assert len(events) == 1
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "StrategyConfigError"
        assert "concat" in nrr.error

    def test_strategy_not_found_defense_in_depth(self):
        """Pydantic Literal normally rejects unknown strategy_name at schema
        time, but `get_strategy` also raises at runtime — defense in depth.

        Bypass Pydantic by assigning directly (validate_assignment is off on
        BaseNodeData) to simulate a drift between the Literal and the registry.
        """
        pool = VariablePool()
        pool.add(["llm_a", "text"], "x")
        pool.add(["llm_b", "text"], "y")
        node = _make_node(pool, self._two_inputs_payload("concat"))
        # Simulate registry/schema drift.
        node._node_data.strategy_name = "never_registered"  # type: ignore[assignment]

        events = list(node._run())

        assert len(events) == 1
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "StrategyNotFoundError"
        assert "never_registered" in nrr.error
        assert nrr.inputs == {"source_count": 2, "strategy": "never_registered"}

    def test_exceptions_are_importable_and_distinct(self):
        # Sanity: import path + hierarchy.
        assert issubclass(MissingInputError, Exception)
        assert issubclass(StrategyConfigError, Exception)
        assert issubclass(StrategyNotFoundError, Exception)
        assert issubclass(WeightResolutionError, Exception)


class TestDynamicWeightResolution:
    """Three-branch coverage of v3 weight resolution (ADR-v3-15):

    * happy path (selector resolves to a finite number),
    * fail-fast (no fallback → WeightResolutionError → FAILED),
    * graceful degrade (fallback set → swap in fallback + log warning).
    """

    @staticmethod
    def _two_inputs_pool(*, with_weight_var: bool, weight_value=None) -> VariablePool:
        pool = VariablePool()
        pool.add(["llm_a", "text"], "A")
        pool.add(["llm_b", "text"], "A")
        if with_weight_var:
            pool.add(["weights_node", "m1"], weight_value)
        return pool

    @staticmethod
    def _payload(
        weight_a, fallback_a=None, weight_b=1.0
    ) -> dict:
        ref_a: dict = {
            "source_id": "m1",
            "variable_selector": ["llm_a", "text"],
            "weight": weight_a,
        }
        if fallback_a is not None:
            ref_a["fallback_weight"] = fallback_a
        return {
            "title": "agg",
            "inputs": [
                ref_a,
                {
                    "source_id": "m2",
                    "variable_selector": ["llm_b", "text"],
                    "weight": weight_b,
                },
            ],
            "strategy_name": "concat",
            "strategy_config": {},
        }

    def test_dynamic_weight_resolves_from_pool(self):
        pool = self._two_inputs_pool(with_weight_var=True, weight_value=3.0)
        payload = self._payload(weight_a=["weights_node", "m1"])
        node = _make_node(pool, payload)

        # `_collect_inputs` is the seam that resolves dynamic weights.
        _, weights, _, fallbacks = node._collect_inputs()
        assert weights == {"m1": 3.0, "m2": 1.0}
        assert fallbacks == []

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        # No fallbacks used → process_data stays empty.
        assert nrr.process_data == {}

    def test_dynamic_weight_missing_var_fail_fast(self):
        # Selector present but pool has no value → WeightResolutionError.
        pool = self._two_inputs_pool(with_weight_var=False)
        payload = self._payload(weight_a=["weights_node", "m1"])
        node = _make_node(pool, payload)

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "WeightResolutionError"
        assert "m1" in nrr.error
        # 'inputs' metadata still records strategy + count for observability.
        assert nrr.inputs["strategy"] == "concat"

    def test_dynamic_weight_non_numeric_fail_fast(self):
        # Selector resolves but value is a string — must escalate, not silently coerce.
        pool = self._two_inputs_pool(with_weight_var=True, weight_value="three")
        payload = self._payload(weight_a=["weights_node", "m1"])
        node = _make_node(pool, payload)

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "WeightResolutionError"
        assert "not numeric" in nrr.error or "str" in nrr.error

    def test_dynamic_weight_bool_fail_fast(self):
        # ``True`` is an int subclass in Python — exclude explicitly.
        pool = self._two_inputs_pool(with_weight_var=True, weight_value=True)
        payload = self._payload(weight_a=["weights_node", "m1"])
        node = _make_node(pool, payload)

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "WeightResolutionError"
        assert "bool" in nrr.error or "not numeric" in nrr.error

    def test_dynamic_weight_falls_back_when_fallback_set(self):
        # Same missing-var setup, but ``fallback_weight=0.5`` opts into degrade.
        pool = self._two_inputs_pool(with_weight_var=False)
        payload = self._payload(
            weight_a=["weights_node", "m1"],
            fallback_a=0.5,
            weight_b=2.0,
        )
        node = _make_node(pool, payload)

        # Verify the fallback resolved to the expected numeric.
        _, weights, _, fallbacks = node._collect_inputs()
        assert weights == {"m1": 0.5, "m2": 2.0}
        assert [fb["source_id"] for fb in fallbacks] == ["m1"]

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        # ``inputs`` stays clean — fallback warnings are not "inputs".
        assert "weight_fallbacks" not in nrr.inputs
        assert "weight_fallback_warnings" not in nrr.inputs
        # ADR-v3-15 trace surface: process_data carries the per-source
        # fallback record so the single-step debug panel surfaces it.
        assert nrr.process_data["weight_fallback_warnings"] == [
            {
                "source_id": "m1",
                "selector": ["weights_node", "m1"],
                "reason": "variable not present in pool",
                "fallback_weight": 0.5,
            }
        ]

    def test_static_weight_no_pool_lookup_required(self):
        # No weights_node in the pool — static float must not trigger resolution.
        pool = self._two_inputs_pool(with_weight_var=False)
        payload = self._payload(weight_a=2.0, weight_b=1.0)
        node = _make_node(pool, payload)

        # Static numeric path doesn't touch the pool.
        _, weights, _, _ = node._collect_inputs()
        assert weights == {"m1": 2.0, "m2": 1.0}

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED

    def test_dynamic_weight_none_value_fail_fast(self):
        # Pool has the selector key, but the stored value is None — the
        # pool returns a NoneSegment whose `.value` is None. The resolver
        # must distinguish this from "selector not present" and report
        # the dedicated reason string so debugging stays unambiguous.
        pool = self._two_inputs_pool(with_weight_var=True, weight_value=None)
        payload = self._payload(weight_a=["weights_node", "m1"])
        node = _make_node(pool, payload)

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "WeightResolutionError"
        # Either reason can apply depending on how the pool stores None
        # (some VariablePool impls treat None as "not present"); whichever
        # branch fires, the failure must surface through the same exception.
        assert (
            "None" in nrr.error
            or "not present" in nrr.error
            or "not numeric" in nrr.error
        )

    def test_dynamic_weight_nan_value_fail_fast(self):
        # NaN is technically a float; finiteness guard must reject it.
        pool = self._two_inputs_pool(
            with_weight_var=True, weight_value=float("nan")
        )
        payload = self._payload(weight_a=["weights_node", "m1"])
        node = _make_node(pool, payload)

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "WeightResolutionError"
        assert "not finite" in nrr.error or "nan" in nrr.error.lower()

    def test_dynamic_weight_inf_value_fail_fast(self):
        pool = self._two_inputs_pool(
            with_weight_var=True, weight_value=float("inf")
        )
        payload = self._payload(weight_a=["weights_node", "m1"])
        node = _make_node(pool, payload)

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "WeightResolutionError"
        assert "not finite" in nrr.error or "inf" in nrr.error.lower()

    def test_dynamic_weight_negative_inf_value_fail_fast(self):
        pool = self._two_inputs_pool(
            with_weight_var=True, weight_value=float("-inf")
        )
        payload = self._payload(weight_a=["weights_node", "m1"])
        node = _make_node(pool, payload)

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "WeightResolutionError"
        assert "not finite" in nrr.error or "inf" in nrr.error.lower()

    def test_fallback_recovers_non_numeric_pool_value(self):
        # Different failure reason than the missing-var test above —
        # this one resolves the selector but lands on a string. Fallback
        # path must still capture the original reason in process_data.
        pool = self._two_inputs_pool(with_weight_var=True, weight_value="three")
        payload = self._payload(
            weight_a=["weights_node", "m1"], fallback_a=0.25, weight_b=1.0
        )
        node = _make_node(pool, payload)

        _, weights, _, _ = node._collect_inputs()
        assert weights == {"m1": 0.25, "m2": 1.0}

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        warnings = nrr.process_data["weight_fallback_warnings"]
        assert len(warnings) == 1
        assert warnings[0]["source_id"] == "m1"
        assert warnings[0]["fallback_weight"] == 0.25
        assert "not numeric" in warnings[0]["reason"]

    def test_multiple_fallbacks_recorded_in_declared_order(self):
        # Two failing dynamic weights, both with fallbacks — the
        # process_data list must hold one entry per source in declared
        # order, so single-step debug shows them column-aligned with
        # the inputs panel.
        pool = VariablePool()
        pool.add(["llm_a", "text"], "A")
        pool.add(["llm_b", "text"], "A")
        # Neither weight selector is present → both fall back.
        payload = {
            "title": "agg",
            "inputs": [
                {
                    "source_id": "m1",
                    "variable_selector": ["llm_a", "text"],
                    "weight": ["weights_node", "m1"],
                    "fallback_weight": 0.6,
                },
                {
                    "source_id": "m2",
                    "variable_selector": ["llm_b", "text"],
                    "weight": ["weights_node", "m2"],
                    "fallback_weight": 0.4,
                },
            ],
            "strategy_name": "concat",
            "strategy_config": {},
        }
        node = _make_node(pool, payload)

        _, weights, _, _ = node._collect_inputs()
        assert weights == {"m1": 0.6, "m2": 0.4}

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        warnings = nrr.process_data["weight_fallback_warnings"]
        assert [w["source_id"] for w in warnings] == ["m1", "m2"]
        assert [w["fallback_weight"] for w in warnings] == [0.6, 0.4]


class TestExtraSourceMeta:
    """``AggregationInputRef.extra`` flows through to ``SourceAggregationContext.source_meta``."""

    def test_extra_surfaces_in_source_meta_via_collect_inputs(self):
        # Verify via _collect_inputs directly: source_meta carries
        # per-source extra dict with the ref's payload.
        pool = VariablePool()
        pool.add(["llm_a", "text"], "x")
        pool.add(["llm_b", "text"], "y")
        node = _make_node(
            pool,
            {
                "title": "agg",
                "inputs": [
                    {
                        "source_id": "a",
                        "variable_selector": ["llm_a", "text"],
                        "extra": {"tier": "high"},
                    },
                    {
                        "source_id": "b",
                        "variable_selector": ["llm_b", "text"],
                    },
                ],
                "strategy_name": "concat",
                "strategy_config": {},
            },
        )
        signals, weights, source_meta, fallbacks = node._collect_inputs()
        assert source_meta == {"a": {"tier": "high"}, "b": {}}
        assert weights == {"a": 1.0, "b": 1.0}
        assert fallbacks == []
        assert [s["source_id"] for s in signals] == ["a", "b"]


class TestExtractMappingExposesDynamicWeight:
    def test_dynamic_weight_selector_surfaces_in_mapping(self):
        config = {
            "id": "agg_node",
            "data": {
                "title": "agg",
                "inputs": [
                    {
                        "source_id": "a",
                        "variable_selector": ["llm_a", "text"],
                        "weight": ["weights", "a"],
                    },
                    {
                        "source_id": "b",
                        "variable_selector": ["llm_b", "text"],
                        # Static weight → no extra mapping entry.
                    },
                ],
                "strategy_name": "concat",
                "strategy_config": {},
            },
        }
        mapping = (
            ResponseAggregatorNode.extract_variable_selector_to_variable_mapping(
                graph_config={}, config=config
            )
        )
        assert dict(mapping) == {
            "agg_node.inputs.a": ["llm_a", "text"],
            "agg_node.inputs.a.weight": ["weights", "a"],
            "agg_node.inputs.b": ["llm_b", "text"],
        }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
