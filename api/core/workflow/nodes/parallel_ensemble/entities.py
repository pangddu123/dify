"""Pydantic schemas for the parallel-ensemble node DSL surface (P3.B.3).

Two layers, on purpose:

* :class:`ParallelEnsembleConfig` is the *business* config block. After
  ADR-v3-16 the node no longer owns prompt rendering or alias selection
  — those moved upstream to the ``token-model-source`` node which yields
  one :class:`~core.workflow.nodes.token_model_source.entities.ModelInvocationSpec`
  per source. This config block carries N :class:`TokenSourceRef` plus
  the runner / aggregator pairing; ``extra="forbid"`` so a DSL author
  cannot smuggle a ``model_url`` / ``api_key`` through this layer.

* :class:`ParallelEnsembleNodeData` is the *graph payload* layer that
  Dify hands the node from the saved workflow JSON. It inherits
  ``BaseNodeData``'s ``extra="allow"`` so legacy / cross-cutting graph
  fields (``selected``, ``params``, ``paramSchemas``, ``datasource_label``,
  …) survive validation — but a ``mode="before"`` validator rejects the
  exact set of sensitive keys the SPI calls out by name, so the
  ``allow`` from BaseNodeData cannot become a smuggling channel.

⚠️ This file does **not** validate runner / aggregator names against the
registry — that's the job of the §9 startup pipeline inside the node's
``_run`` (see ``node.py``). Schema-level validation here only enforces
shape; semantic checks (registry lookup, capability filter, requirements
matching, cross-field rules) belong on the node so they fire after the
factory has injected the registries.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType

from . import PARALLEL_ENSEMBLE_NODE_TYPE
from .spi.trace import DiagnosticsConfig

# Sensitive keys the DSL must never carry on the parallel-ensemble node
# data. URLs and credentials live in ``api/configs/model_net.yaml`` and
# are reachable only via the registry; surfacing them in the DSL would
# turn the node into an SSRF / credential-leak vector. After ADR-v3-16
# the alias list is gone too — backends are picked by the upstream
# ``token-model-source`` node — so this list shrinks to the SSRF /
# credential surface; a typo'd ``model_aliases`` will be caught earlier
# by ``ParallelEnsembleConfig``'s ``extra="forbid"``.
_FORBIDDEN_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"model_url", "api_key", "api_key_env", "url", "endpoint"})


class TokenSourceRef(BaseModel):
    """One source contributing to the joint-vote token loop (ADR-v3-16).

    ``spec_selector`` points at an upstream ``token-model-source`` node's
    ``outputs.spec`` field — the variable pool will hand the runtime a
    :class:`~core.workflow.nodes.token_model_source.entities.ModelInvocationSpec`
    when ``_run`` resolves it. ``source_id`` identifies the source within
    this node so the same model alias can appear twice (the canonical
    "self-consistency at temperature=0.3 vs 1.0" setup) without
    collisions in trace / weights / per-model dicts.

    ``top_k_override`` is the only sampling knob this layer can override:
    PN.py-style joint voting requires every voter to surface the same
    number of candidates per step (``min(per-source top_k)`` would
    otherwise truncate richer voters), so the user can re-pin top_k at
    the *consumer* without editing the upstream source. Other sampling
    knobs (temperature / top_p / stop / seed / max_tokens) ride on the
    spec's ``sampling_params`` exactly as the source produced them.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., min_length=1)
    """Stable per-node identifier; surfaced in trace / weights /
    per-model error dicts. Must be unique within a single
    parallel-ensemble node."""

    spec_selector: list[str] = Field(..., min_length=2)
    """``VariableSelector`` form (``["<node_id>", "<field>"]``) pointing
    at the upstream ``token-model-source`` node's ``outputs.spec``. Two
    segments minimum — same shape Dify uses everywhere else."""

    weight: float | list[str] = 1.0
    """Static float (per-source weight) OR a ``VariableSelector``-shaped
    ``list[str]`` (resolved at runtime against the variable pool, ADR-v3-15).
    Default ``1.0`` keeps unweighted behaviour intact.

    A ``list[str]`` here MUST have ≥ 2 segments — same shape as
    ``spec_selector`` — so the runtime resolver can read it via
    ``variable_pool.get(...)``."""

    top_k_override: int | None = None
    """ADR-v3-6: optional per-source override for the spec's ``top_k``.
    PN.py joint voting requires every voter to surface the same top-k
    count per step; this knob lets the consumer re-pin top_k at the
    aggregation site without editing the upstream source. ``None`` =
    keep the spec's ``sampling_params.top_k``."""

    fallback_weight: float | None = None
    """Numeric fallback when a dynamic ``weight`` selector fails to
    resolve. ``None`` (default) = fail fast: the node raises
    ``WeightResolutionError`` and FAILs. Setting this to a number opts
    into a graceful-degrade mode where the per-source weight collapses
    to ``fallback_weight`` and the trace records a warning (ADR-v3-15)."""

    extra: dict[str, Any] = Field(default_factory=dict)
    """Per-source pass-through metadata, surfaced to strategies via
    ``BackendAggregationContext.source_meta``. Lets a strategy author
    ride extra context (e.g. ``{"confidence_tier": "high"}``) without
    forking the TokenSourceRef schema."""

    @field_validator("source_id")
    @classmethod
    def _source_id_not_blank(cls, v: str) -> str:
        # Mirror ``AggregationInputRef``: trim and reject pure-whitespace
        # ids so ``"m1"`` and ``"m1 "`` cannot slip through as distinct
        # keys in trace / weights downstream.
        stripped = v.strip()
        if not stripped:
            raise ValueError("source_id must not be blank")
        return stripped

    @field_validator("spec_selector")
    @classmethod
    def _spec_selector_segments_not_blank(cls, v: list[str]) -> list[str]:
        for i, seg in enumerate(v):
            if not seg or not seg.strip():
                raise ValueError(
                    f"spec_selector segment [{i}] must not be blank; "
                    "each segment must be a non-empty identifier"
                )
        return v

    @field_validator("weight", mode="before")
    @classmethod
    def _weight_well_formed(cls, v: Any) -> float | list[str]:
        # Same finite + non-bool guard as response_aggregator's
        # AggregationInputRef.weight: bool is an int subclass and would
        # silently coerce to 1.0/0.0; NaN/Inf would corrupt weighted-sum
        # tallying downstream.
        if isinstance(v, bool):
            raise ValueError(
                "weight must be a finite number or a VariableSelector list, "
                "not a bool (bool is an int subclass and would coerce silently)"
            )
        if isinstance(v, (int, float)):
            f = float(v)
            if not math.isfinite(f):
                raise ValueError(f"weight must be finite; got {f}")
            if f <= 0.0:
                raise ValueError(f"weight must be > 0; got {f}")
            return f
        if not isinstance(v, list):
            raise ValueError("weight must be a finite number or a VariableSelector list")
        if len(v) < 2:
            raise ValueError(
                "weight selector must have at least 2 segments (same shape as spec_selector)"
            )
        for i, seg in enumerate(v):
            if not isinstance(seg, str) or not seg or not seg.strip():
                raise ValueError(f"weight selector segment [{i}] must not be blank")
        return v

    @field_validator("fallback_weight", mode="before")
    @classmethod
    def _fallback_weight_finite(cls, v: Any) -> float | None:
        if v is None:
            return None
        if isinstance(v, bool):
            raise ValueError("fallback_weight must be a finite number, not a bool")
        if not isinstance(v, (int, float)):
            raise ValueError(f"fallback_weight must be a finite number, got {type(v).__name__}")
        f = float(v)
        if not math.isfinite(f):
            raise ValueError(f"fallback_weight must be finite; got {f}")
        if f <= 0.0:
            raise ValueError(f"fallback_weight must be > 0; got {f}")
        return f


class ParallelEnsembleConfig(BaseModel):
    """Nested business-config block — ``extra="forbid"`` is the SPI's seat-belt.

    Forbidding extras at this layer is what stops a DSL like
    ``runner_config: {top_k: 5, model_url: "http://..."}`` from
    silently accumulating side-channel fields. The forbidden top-level
    keys also live in :data:`_FORBIDDEN_TOP_LEVEL_KEYS` for an explicit
    second line of defence on the outer ``ParallelEnsembleNodeData``,
    but at this layer the closed schema is enough.
    """

    model_config = ConfigDict(extra="forbid")

    token_sources: list[TokenSourceRef] = Field(..., min_length=1)
    """N upstream sources contributing to the joint-vote loop. Length is
    enforced again by runner-specific ``validate_selection``
    (``token_step`` requires ≥ 2); the schema minimum stays at 1 so a
    judge-style third-party runner that only needs a single contestant +
    a judge stays valid."""

    runner_name: str = Field(min_length=1)
    """Registry key resolved against ``RunnerRegistry`` at run start."""

    runner_config: dict[str, object] = Field(default_factory=dict)
    """Free-form blob; runner's ``config_class`` is the second-level
    schema validator (``model_validate`` lands inside ``_run``). Empty
    dict is a valid default for runners with no tunables."""

    aggregator_name: str = Field(min_length=1)
    """Registry key resolved against ``AggregatorRegistry``; the §9
    pipeline checks the aggregator's ``scope`` matches the runner's
    ``aggregator_scope`` before either is instantiated."""

    aggregator_config: dict[str, object] = Field(default_factory=dict)
    """Same shape contract as ``runner_config``."""

    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
    """Trace knobs; defaults to the SPI-conservative settings (lightweight
    fields on, heavy ones off, ``storage="metadata"``). See SPI §7."""

    @model_validator(mode="after")
    def _check_source_id_unique(self) -> ParallelEnsembleConfig:
        seen: set[str] = set()
        for ref in self.token_sources:
            if ref.source_id in seen:
                raise ValueError(
                    f"Duplicate source_id '{ref.source_id}' in token_sources; "
                    "source_id must be unique within a single parallel-ensemble node"
                )
            seen.add(ref.source_id)
        return self


class ParallelEnsembleNodeData(BaseNodeData):
    """Top-level graph payload for the parallel-ensemble node.

    Inherits ``BaseNodeData(extra="allow")`` so cross-cutting graph
    extras (``selected`` / ``params`` / ``paramSchemas`` /
    ``datasource_label`` etc.) keep flowing through saved-workflow
    payloads. The :meth:`_reject_sensitive_top_level_fields` validator
    closes the SSRF / credential gap the ``allow`` would otherwise open
    — every key in :data:`_FORBIDDEN_TOP_LEVEL_KEYS` is rejected with a
    structured error before pydantic gets a chance to silently store it
    in ``__pydantic_extra__``.

    Two cooperating defences:

    1. ``ensemble.runner_config`` / ``ensemble.aggregator_config`` are
       sub-models with ``extra="forbid"`` — a DSL that nests a forbidden
       key inside the business config dies at schema validation.
    2. The top-level ``allow`` is permissive on purpose for legacy
       compatibility, but the validator below blocks the named sensitive
       keys at the *outer* layer. Together they cover both the
       business-layer smuggle and the framework-layer smuggle.
    """

    type: NodeType = PARALLEL_ENSEMBLE_NODE_TYPE

    ensemble: ParallelEnsembleConfig
    """Nested business config — keep DSL extras out of this object."""

    NODE_TYPE: ClassVar[str] = PARALLEL_ENSEMBLE_NODE_TYPE

    @model_validator(mode="before")
    @classmethod
    def _reject_sensitive_top_level_fields(cls, data: Any) -> Any:
        """Hard-fail any DSL that carries a known SSRF / credential key.

        Runs in ``mode="before"`` so the rejection wins over the
        permissive ``extra="allow"`` inherited from ``BaseNodeData`` —
        otherwise pydantic would happily stash the key in
        ``__pydantic_extra__`` and the only thing standing between us
        and an SSRF DSL would be downstream code remembering to look
        for the field by name.
        """
        if isinstance(data, dict):
            offenders = sorted(_FORBIDDEN_TOP_LEVEL_KEYS & data.keys())
            if offenders:
                raise ValueError(
                    f"parallel-ensemble node data must not carry sensitive fields "
                    f"{offenders}; URLs and credentials live in the model registry "
                    f"yaml, not in the DSL (see EXTENSIBILITY_SPEC §4.4 T1/T2)."
                )
        return data
