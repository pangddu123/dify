import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType

from . import ENSEMBLE_AGGREGATOR_NODE_TYPE


class AggregationInputRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., min_length=1)
    variable_selector: list[str] = Field(..., min_length=2)
    weight: float | list[str] = 1.0
    """Static float (per-source weight) OR a ``VariableSelector``-shaped
    ``list[str]`` (resolved at runtime against the variable pool, ADR-v3-15).
    Default ``1.0`` keeps v2.4 unweighted majority-vote behaviour intact.

    A ``list[str]`` here MUST have ≥ 2 segments — same shape as
    ``variable_selector`` — so the runtime resolver can read it via
    ``variable_pool.get(...)``."""

    fallback_weight: float | None = None
    """Numeric fallback when a dynamic ``weight`` selector fails to
    resolve (variable not in pool / wrong type). ``None`` (default) =
    fail fast: the node raises ``WeightResolutionError`` and FAILs.
    Setting this to a number opts into a graceful-degrade mode where
    the per-source weight collapses to ``fallback_weight`` and the
    trace records a warning (ADR-v3-15)."""

    extra: dict[str, Any] = Field(default_factory=dict)
    """Per-source pass-through metadata, surfaced to strategies via
    ``SourceAggregationContext.source_meta``. Lets a strategy author
    ride extra context (e.g. ``{"confidence_tier": "high"}``) without
    forking the AggregationInputRef schema."""

    @field_validator("source_id")
    @classmethod
    def _source_id_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("source_id must not be blank")
        # Normalize leading/trailing whitespace: the frontend dedup check
        # (default.ts) compares trimmed values, but the uniqueness guard on
        # this model runs against the raw value — without normalization,
        # `"model_a"` and `"model_a "` would survive as distinct keys in
        # `metadata.contributions` and diverge majority_vote tie-break.
        return stripped

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

    @field_validator("weight", mode="before")
    @classmethod
    def _weight_selector_well_formed(cls, v: Any) -> float | list[str]:
        # Static numeric branch: reject ``bool`` explicitly (it's an
        # ``int`` subclass; silent ``True/False`` → ``1.0/0.0`` would
        # mask schema drift), and reject NaN / ±Inf so a downstream
        # strategy never sees a non-finite weight that would corrupt
        # weighted-sum tallying.
        if isinstance(v, bool):
            raise ValueError(
                "weight must be a finite number or a VariableSelector list, "
                "not a bool (bool is an int subclass and would coerce silently)"
            )
        if isinstance(v, (int, float)):
            f = float(v)
            if not math.isfinite(f):
                raise ValueError(
                    f"weight must be finite; got {f} (NaN / Inf is rejected to "
                    "avoid corrupting weighted-sum tallying)"
                )
            return f
        # Dynamic selector branch: enforce the same shape as
        # ``variable_selector`` so the runtime resolver doesn't have to
        # special-case malformed input.
        if not isinstance(v, list):
            raise ValueError(
                "weight must be a finite number or a VariableSelector list"
            )
        if len(v) < 2:
            raise ValueError(
                "weight selector must have at least 2 segments "
                "(same shape as variable_selector)"
            )
        for i, seg in enumerate(v):
            if not isinstance(seg, str) or not seg or not seg.strip():
                raise ValueError(
                    f"weight selector segment [{i}] must not be blank"
                )
        return v

    @field_validator("fallback_weight", mode="before")
    @classmethod
    def _fallback_weight_finite(cls, v: Any) -> float | None:
        # Same finite + non-bool guard as ``weight``: a graceful-degrade
        # fallback set to NaN / Inf / True would silently corrupt the
        # tallying that the fallback exists to keep correct.
        if v is None:
            return None
        if isinstance(v, bool):
            raise ValueError(
                "fallback_weight must be a finite number, not a bool"
            )
        if not isinstance(v, (int, float)):
            raise ValueError(
                f"fallback_weight must be a finite number, got {type(v).__name__}"
            )
        f = float(v)
        if not math.isfinite(f):
            raise ValueError(
                f"fallback_weight must be finite; got {f}"
            )
        return f


class EnsembleAggregatorNodeData(BaseNodeData):
    type: NodeType = ENSEMBLE_AGGREGATOR_NODE_TYPE

    inputs: list[AggregationInputRef] = Field(..., min_length=2)
    strategy_name: Literal["majority_vote", "concat", "weighted_majority_vote"] = "majority_vote"
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
