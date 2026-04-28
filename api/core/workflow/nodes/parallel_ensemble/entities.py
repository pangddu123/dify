"""Pydantic schemas for the parallel-ensemble node DSL surface (P2.8).

Two layers, on purpose:

* :class:`ParallelEnsembleConfig` is the *business* config block. It owns
  ``runner_name`` / ``runner_config`` / ``aggregator_name`` /
  ``aggregator_config`` / ``diagnostics`` / ``question_variable`` /
  ``model_aliases``. ``extra="forbid"`` so a DSL author cannot smuggle a
  ``model_url`` / ``api_key`` through this layer — the SSRF / credential
  boundary documented in EXTENSIBILITY_SPEC §4.4 (T1 / T2).

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

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType

from . import PARALLEL_ENSEMBLE_NODE_TYPE
from .spi.trace import DiagnosticsConfig

# Sensitive keys the DSL must never carry on the parallel-ensemble node
# data. URLs and credentials live in ``api/configs/model_net.yaml`` and
# are reachable only via the registry; surfacing them in the DSL would
# turn the node into an SSRF / credential-leak vector. These names are
# deliberately the *exact* strings TASKS.md P2.8 calls out — the
# rejection is a closed allowlist, not a regex match, so a third-party
# extension can still carry e.g. ``"system_prompt"`` without tripping
# this check.
_FORBIDDEN_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"model_url", "api_key", "api_key_env", "url", "endpoint"})


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

    question_variable: list[str] = Field(min_length=2)
    """Selector pointing at the user-question variable in the variable
    pool, e.g. ``["start", "user_input"]``. Two segments minimum because
    a one-segment selector cannot identify both the source node and the
    field — same shape Dify uses everywhere else."""

    model_aliases: list[str] = Field(min_length=1)
    """Registry aliases to fan out across. Length is enforced again by
    runner-specific ``validate_selection`` (token_step / response_level
    both require ≥ 2); we keep the schema minimum at 1 so ``judge``-
    style runners that only need a single contestant + a judge stay
    valid here."""

    runner_name: str = Field(min_length=1)
    """Registry key resolved against ``RunnerRegistry`` at run start."""

    runner_config: dict[str, object] = Field(default_factory=dict)
    """Free-form blob; runner's ``config_class`` is the second-level
    schema validator (``model_validate`` lands inside ``_run``). Empty
    dict is a valid default for runners with no tunables (e.g.
    ``response_level``)."""

    aggregator_name: str = Field(min_length=1)
    """Registry key resolved against ``AggregatorRegistry``; the §9
    pipeline checks the aggregator's ``scope`` matches the runner's
    ``aggregator_scope`` before either is instantiated."""

    aggregator_config: dict[str, object] = Field(default_factory=dict)
    """Same shape contract as ``runner_config``."""

    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
    """Trace knobs; defaults to the SPI-conservative settings (lightweight
    fields on, heavy ones off, ``storage="metadata"``). See SPI §7."""


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

    # The node type string is the registry key the framework uses to
    # resolve this class via ``Node._registry``; surfacing it as a class
    # attribute (not just an instance field) keeps the registration
    # legible at import time and matches the pattern in other node
    # packages (``ensemble_aggregator``, ``llm`` etc.).
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
                # ``ValueError`` here surfaces as a pydantic
                # ``ValidationError`` to the caller, which is the same
                # error class the ``extra="forbid"`` machinery uses on
                # the nested ``ParallelEnsembleConfig`` — keeping the
                # error surface uniform across both defences.
                raise ValueError(
                    f"parallel-ensemble node data must not carry sensitive fields "
                    f"{offenders}; URLs and credentials live in the model registry "
                    f"yaml, not in the DSL (see EXTENSIBILITY_SPEC §4.4 T1/T2)."
                )
        return data
