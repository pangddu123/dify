"""Unit tests for EnsembleAggregatorNode behaviors that the schema
(test_entities.py) cannot catch.

Covers:
- Segment.text normalization for NoneSegment/ObjectSegment/ArrayStringSegment (P1.3 review r2)
- extract_variable_selector_to_variable_mapping exposing each input selector (P1.3 review r2)
- End-to-end `_run` with mock VariablePool: happy paths + FAILED paths (P1.4)
"""

import pytest

from core.workflow.nodes.ensemble_aggregator import EnsembleAggregatorNode
from core.workflow.nodes.ensemble_aggregator.exceptions import (
    MissingInputError,
    StrategyConfigError,
    StrategyNotFoundError,
)
from graphon.enums import WorkflowNodeExecutionStatus
from graphon.node_events.node import StreamCompletedEvent
from graphon.runtime.variable_pool import VariablePool


def _make_node(pool: VariablePool, node_data_payload: dict) -> EnsembleAggregatorNode:
    """Build a node bypassing Node.__init__ (which needs full graph_init_params).

    We only exercise `_run` / `_collect_inputs`, which read `_node_data`,
    `_node_id`, and `graph_runtime_state.variable_pool`.
    """
    node = EnsembleAggregatorNode.__new__(EnsembleAggregatorNode)
    node._node_id = "agg_1"

    class _RS:
        pass

    rs = _RS()
    rs.variable_pool = pool
    node.graph_runtime_state = rs
    node._node_data = EnsembleAggregatorNode._node_data_type.model_validate(
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
                "strategy_name": "majority_vote",
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
        mapping = EnsembleAggregatorNode.extract_variable_selector_to_variable_mapping(
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
        mapping = EnsembleAggregatorNode.extract_variable_selector_to_variable_mapping(
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
        mapping = EnsembleAggregatorNode.extract_variable_selector_to_variable_mapping(
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

    def test_majority_vote_succeeds_with_expected_outputs(self):
        pool = self._three_text_pool({"llm_a": "A", "llm_b": "A", "llm_c": "B"})
        node = _make_node(pool, self._node_data("majority_vote"))

        events = list(node._run())

        assert len(events) == 1
        assert isinstance(events[0], StreamCompletedEvent)
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        assert nrr.error == ""

        assert nrr.outputs["text"] == "A"
        metadata = nrr.outputs["metadata"]
        assert metadata["strategy"] == "majority_vote"
        assert metadata["votes"] == {"A": 2, "B": 1}
        assert metadata["winner_votes"] == 2
        assert metadata["tie_break_applied"] is False
        assert metadata["contributions"] == {
            "gpt4": "A",
            "claude": "A",
            "llama": "B",
        }

        assert nrr.inputs == {"source_count": 3, "strategy": "majority_vote"}

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
    """FAILED event path: node catches EnsembleAggregatorNodeError descendants
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
        node = _make_node(pool, self._two_inputs_payload("majority_vote"))

        events = list(node._run())

        assert len(events) == 1
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "MissingInputError"
        assert "llm_b" in nrr.error or "'b'" in nrr.error
        # inputs metadata still populated for observability of the failed run.
        assert nrr.inputs == {"source_count": 2, "strategy": "majority_vote"}
        # Outputs not set on failure.
        assert nrr.outputs == {}

    def test_invalid_strategy_config_becomes_failed_event(self):
        pool = VariablePool()
        pool.add(["llm_a", "text"], "x")
        pool.add(["llm_b", "text"], "y")
        # `bogus` is rejected by MajorityVoteStrategy's extra="forbid" config.
        node = _make_node(
            pool,
            self._two_inputs_payload(
                "majority_vote", {"bogus": 42}
            ),
        )

        events = list(node._run())

        assert len(events) == 1
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "StrategyConfigError"
        assert "majority_vote" in nrr.error

    def test_strategy_not_found_defense_in_depth(self):
        """Pydantic Literal normally rejects unknown strategy_name at schema
        time, but `get_strategy` also raises at runtime — defense in depth.

        Bypass Pydantic by assigning directly (validate_assignment is off on
        BaseNodeData) to simulate a drift between the Literal and the registry.
        """
        pool = VariablePool()
        pool.add(["llm_a", "text"], "x")
        pool.add(["llm_b", "text"], "y")
        node = _make_node(pool, self._two_inputs_payload("majority_vote"))
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
