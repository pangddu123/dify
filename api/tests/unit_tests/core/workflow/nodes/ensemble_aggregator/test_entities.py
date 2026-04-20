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
