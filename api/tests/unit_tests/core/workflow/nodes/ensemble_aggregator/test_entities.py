import pytest
from pydantic import ValidationError

from core.workflow.nodes.ensemble_aggregator import ENSEMBLE_AGGREGATOR_NODE_TYPE
from core.workflow.nodes.ensemble_aggregator.entities import (
    AggregationInputRef,
    EnsembleAggregatorNodeData,
)


class TestAggregationInputRef:
    def test_valid_two_segment_selector(self):
        ref = AggregationInputRef(source_id="gpt4", variable_selector=["node_a", "text"])
        assert ref.source_id == "gpt4"
        assert ref.variable_selector == ["node_a", "text"]

    def test_valid_path_segments_allowed(self):
        ref = AggregationInputRef(
            source_id="gpt4",
            variable_selector=["node_a", "text", "0", "content"],
        )
        assert len(ref.variable_selector) == 4

    def test_selector_too_short_rejected(self):
        with pytest.raises(ValidationError):
            AggregationInputRef(source_id="gpt4", variable_selector=["only_one"])

    def test_selector_empty_rejected(self):
        with pytest.raises(ValidationError):
            AggregationInputRef(source_id="gpt4", variable_selector=[])

    def test_blank_selector_segment_rejected(self):
        with pytest.raises(ValidationError):
            AggregationInputRef(source_id="gpt4", variable_selector=["node_a", "  "])

    def test_empty_selector_segment_rejected(self):
        with pytest.raises(ValidationError):
            AggregationInputRef(source_id="gpt4", variable_selector=["node_a", ""])

    def test_blank_source_id_rejected(self):
        with pytest.raises(ValidationError):
            AggregationInputRef(source_id="  ", variable_selector=["node_a", "text"])

    def test_empty_source_id_rejected(self):
        with pytest.raises(ValidationError):
            AggregationInputRef(source_id="", variable_selector=["node_a", "text"])

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            AggregationInputRef.model_validate(
                {
                    "source_id": "gpt4",
                    "variable_selector": ["node_a", "text"],
                    "unknown_field": "x",
                }
            )

    def test_static_weight_defaults_to_one(self):
        ref = AggregationInputRef(source_id="m1", variable_selector=["n", "text"])
        assert ref.weight == 1.0
        assert ref.fallback_weight is None
        assert ref.extra == {}

    def test_static_weight_accepts_float(self):
        ref = AggregationInputRef(
            source_id="m1", variable_selector=["n", "text"], weight=0.7
        )
        assert ref.weight == 0.7

    def test_dynamic_weight_accepts_variable_selector(self):
        ref = AggregationInputRef(
            source_id="m1",
            variable_selector=["n", "text"],
            weight=["weights_node", "m1"],
        )
        assert ref.weight == ["weights_node", "m1"]

    def test_dynamic_weight_too_short_rejected(self):
        with pytest.raises(ValidationError, match="at least 2 segments"):
            AggregationInputRef(
                source_id="m1",
                variable_selector=["n", "text"],
                weight=["only_one"],
            )

    def test_dynamic_weight_blank_segment_rejected(self):
        with pytest.raises(ValidationError, match="must not be blank"):
            AggregationInputRef(
                source_id="m1",
                variable_selector=["n", "text"],
                weight=["weights_node", "  "],
            )

    def test_fallback_weight_accepts_numeric(self):
        ref = AggregationInputRef(
            source_id="m1",
            variable_selector=["n", "text"],
            weight=["w", "m1"],
            fallback_weight=0.5,
        )
        assert ref.fallback_weight == 0.5

    def test_static_weight_bool_rejected(self):
        # ``True`` is an int subclass — would silently coerce to 1.0,
        # masking schema drift. Reject explicitly.
        with pytest.raises(ValidationError, match="bool"):
            AggregationInputRef(
                source_id="m1",
                variable_selector=["n", "text"],
                weight=True,
            )

    def test_static_weight_nan_rejected(self):
        with pytest.raises(ValidationError, match="finite"):
            AggregationInputRef(
                source_id="m1",
                variable_selector=["n", "text"],
                weight=float("nan"),
            )

    def test_static_weight_inf_rejected(self):
        with pytest.raises(ValidationError, match="finite"):
            AggregationInputRef(
                source_id="m1",
                variable_selector=["n", "text"],
                weight=float("inf"),
            )

    def test_fallback_weight_bool_rejected(self):
        with pytest.raises(ValidationError, match="bool"):
            AggregationInputRef(
                source_id="m1",
                variable_selector=["n", "text"],
                weight=["w", "m1"],
                fallback_weight=False,
            )

    def test_fallback_weight_nan_rejected(self):
        with pytest.raises(ValidationError, match="finite"):
            AggregationInputRef(
                source_id="m1",
                variable_selector=["n", "text"],
                weight=["w", "m1"],
                fallback_weight=float("nan"),
            )

    def test_fallback_weight_inf_rejected(self):
        with pytest.raises(ValidationError, match="finite"):
            AggregationInputRef(
                source_id="m1",
                variable_selector=["n", "text"],
                weight=["w", "m1"],
                fallback_weight=float("-inf"),
            )

    def test_extra_accepts_arbitrary_dict(self):
        ref = AggregationInputRef(
            source_id="m1",
            variable_selector=["n", "text"],
            extra={"confidence_tier": "high", "score": 0.95},
        )
        assert ref.extra == {"confidence_tier": "high", "score": 0.95}

    def test_source_id_leading_trailing_whitespace_is_stripped(self):
        # Frontend dedup (default.ts) compares trimmed values — backend
        # must normalize too, otherwise `"model_a"` and `"model_a "` survive
        # as distinct contributions/keys and break majority_vote tie-break.
        ref = AggregationInputRef(
            source_id="  gpt4  ",
            variable_selector=["node_a", "text"],
        )
        assert ref.source_id == "gpt4"


class TestEnsembleAggregatorNodeData:
    @staticmethod
    def _valid_inputs():
        return [
            {"source_id": "gpt4", "variable_selector": ["node_a", "text"]},
            {"source_id": "claude", "variable_selector": ["node_b", "text"]},
        ]

    def test_defaults_applied(self):
        data = EnsembleAggregatorNodeData(inputs=self._valid_inputs())
        assert data.type == ENSEMBLE_AGGREGATOR_NODE_TYPE
        assert data.strategy_name == "majority_vote"
        assert data.strategy_config == {}

    def test_inputs_too_few_rejected(self):
        with pytest.raises(ValidationError):
            EnsembleAggregatorNodeData(
                inputs=[{"source_id": "gpt4", "variable_selector": ["node_a", "text"]}]
            )

    def test_duplicate_source_id_rejected(self):
        with pytest.raises(ValidationError) as exc:
            EnsembleAggregatorNodeData(
                inputs=[
                    {"source_id": "gpt4", "variable_selector": ["node_a", "text"]},
                    {"source_id": "gpt4", "variable_selector": ["node_b", "text"]},
                ]
            )
        assert "Duplicate source_id" in str(exc.value)

    def test_duplicate_source_id_rejected_after_trim(self):
        # Regression for the frontend/backend divergence: `"gpt4"` and
        # `"gpt4 "` must collide at the uniqueness guard because the
        # field validator strips before the model-level check runs.
        with pytest.raises(ValidationError) as exc:
            EnsembleAggregatorNodeData(
                inputs=[
                    {"source_id": "gpt4", "variable_selector": ["node_a", "text"]},
                    {"source_id": "gpt4 ", "variable_selector": ["node_b", "text"]},
                ]
            )
        assert "Duplicate source_id" in str(exc.value)

    def test_concat_strategy_accepted(self):
        data = EnsembleAggregatorNodeData(
            inputs=self._valid_inputs(),
            strategy_name="concat",
            strategy_config={"separator": "\n\n"},
        )
        assert data.strategy_name == "concat"
        assert data.strategy_config["separator"] == "\n\n"

    def test_unknown_strategy_name_rejected(self):
        with pytest.raises(ValidationError):
            EnsembleAggregatorNodeData(
                inputs=self._valid_inputs(),
                strategy_name="unknown_strategy",  # type: ignore[arg-type]
            )

    def test_weighted_majority_vote_strategy_accepted(self):
        # v3 added strategy literal — guard against accidental removal.
        data = EnsembleAggregatorNodeData(
            inputs=self._valid_inputs(),
            strategy_name="weighted_majority_vote",
        )
        assert data.strategy_name == "weighted_majority_vote"
