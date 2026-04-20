from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType

from . import ENSEMBLE_AGGREGATOR_NODE_TYPE


class AggregationInputRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., min_length=1)
    variable_selector: list[str] = Field(..., min_length=2)

    @field_validator("source_id")
    @classmethod
    def _source_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_id must not be blank")
        return v

    @field_validator("variable_selector")
    @classmethod
    def _selector_segments_not_blank(cls, v: list[str]) -> list[str]:
        for i, seg in enumerate(v):
            if not seg or not seg.strip():
                raise ValueError(
                    f"variable_selector segment [{i}] must not be blank; "
                    "each segment must be a non-empty identifier"
                )
        return v


class EnsembleAggregatorNodeData(BaseNodeData):
    type: NodeType = ENSEMBLE_AGGREGATOR_NODE_TYPE

    inputs: list[AggregationInputRef] = Field(..., min_length=2)
    strategy_name: Literal["majority_vote", "concat"] = "majority_vote"
    strategy_config: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_source_id_unique(self) -> "EnsembleAggregatorNodeData":
        seen: set[str] = set()
        for ref in self.inputs:
            if ref.source_id in seen:
                raise ValueError(
                    f"Duplicate source_id '{ref.source_id}' in inputs; "
                    "source_id must be unique within a single ensemble-aggregator node"
                )
            seen.add(ref.source_id)
        return self
