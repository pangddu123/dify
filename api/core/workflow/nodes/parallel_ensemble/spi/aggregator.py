"""Aggregator SPI — EXTENSIBILITY_SPEC §6 + DEVELOPMENT_PLAN_v3 §3 (ADR-v3-8).

v3 splits the aggregation context into two layers:

* ``SourceAggregationContext`` — the source view shared by response and
  token modes (``sources`` / ``weights`` / ``source_meta`` /
  ``strategy_config``). ``ResponseAggregator`` only ever sees this; a
  third-party response strategy cannot reach into runner / backend
  internals it has no business with.
* ``BackendAggregationContext(SourceAggregationContext)`` — adds the
  PN.py-loop fields (``backends`` / ``capabilities`` / ``runner_name`` /
  ``runner_config`` / ``trace`` / ``elapsed_ms_so_far`` /
  ``step_index``). Only ``TokenAggregator`` receives this layer.

P3.B.0 dropped the ``AggregationContext`` back-compat alias alongside
the deletion of the response-mode runner / aggregators (ADR-v3-9). Token
aggregators import ``BackendAggregationContext`` directly; response
strategies live under ``ensemble_aggregator`` and only ever see
``SourceAggregationContext``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, TypedDict, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .backend import BackendInfo, TokenCandidate
from .capability import Capability
from .trace import TraceCollector

ConfigT = TypeVar("ConfigT", bound=BaseModel)
SignalT = TypeVar("SignalT")
ResultT = TypeVar("ResultT")


class SourceAggregationContext(BaseModel):
    """Source-only view of the aggregation context.

    Visible to every aggregator (response + token). Carries just enough
    to write a strategy that reasons about *which sources voted what
    weight* — no backend / runner internals leak in.

    ``frozen=True`` so an aggregator that tries to mutate context fails
    fast; ``arbitrary_types_allowed`` is unused at this layer but kept
    for forward-compat with subclasses that do hold non-pydantic fields
    (``BackendAggregationContext.trace``).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    sources: list[str]
    """Ordered ``source_id`` list — token aggregators get backend aliases,
    response aggregators get whatever the upstream node calls each input."""

    weights: dict[str, float]
    """``source_id`` → effective weight. ensemble_aggregator resolves
    static / variable-bound weights into this dict before calling
    ``aggregate``; the token-mode ``TokenStepRunner`` resolves them from
    ``ModelBackend.weight`` (i.e. the underlying spec ``weight`` field)."""

    source_meta: dict[str, dict] = Field(default_factory=dict)
    """Per-source pass-through dict (``AggregationInputRef.extra`` for
    ensemble_aggregator). Aggregators that want a "tag this source as
    high-confidence" channel read here."""

    strategy_config: dict = Field(default_factory=dict)
    """Raw strategy config dict — same payload that ``aggregate`` gets
    in typed form via the ``config`` parameter. Exposed on the context
    so reasoning helpers can introspect without a second parse."""


class BackendAggregationContext(SourceAggregationContext):
    """Token-mode context — extends the source view with backend / runner state.

    Only ``TokenAggregator`` instances see this. ``backends`` /
    ``capabilities`` / ``runner_name`` / ``runner_config`` are the
    PN.py-loop fields a token strategy genuinely needs (e.g., gating a
    candidate by the contributing backend's capability set);
    ``trace`` / ``elapsed_ms_so_far`` / ``step_index`` give per-step
    diagnostics access subject to ``DiagnosticsConfig`` gating.
    """

    backends: list[BackendInfo]
    capabilities: dict[str, frozenset[Capability]]
    runner_name: str
    runner_config: dict
    trace: TraceCollector
    elapsed_ms_so_far: int
    step_index: int | None = None


class Aggregator[
    ConfigT: BaseModel,
    SignalT,
    ResultT,
    ContextT: SourceAggregationContext,
](ABC):
    """Generic four-parameter aggregator base.

    ``ConfigT`` = aggregator config schema (pydantic);
    ``SignalT`` = what the runner hands in (response list, token signals, …);
    ``ResultT`` = what the runner gets back to drive its ``yield``;
    ``ContextT`` = visible aggregation context — narrow on the typed bases
    so a response strategy never sees backend / runner internals it has
    no business with.

    Pairs with a runner via the ``scope`` ClassVar. Two built-in scopes
    (``"response"``, ``"token"``) get typed bases below; third-party
    scopes pick a free string.
    """

    name: ClassVar[str]
    """Registry key, set by ``@register_aggregator``."""

    scope: ClassVar[str]
    """Scope tag matching ``EnsembleRunner.aggregator_scope``."""

    config_class: ClassVar[type[BaseModel]]
    """Pydantic schema for the aggregator's slice of node config."""

    i18n_key_prefix: ClassVar[str]
    """Same i18n contract as the runner's prefix; see ``runner.py``."""

    ui_schema: ClassVar[dict]
    """Per-field UI controls; same allowlist as the runner's ``ui_schema``."""

    @classmethod
    def config_schema_json(cls) -> dict:
        """Pydantic JSON schema export for frontend fallback validation."""
        return cls.config_class.model_json_schema()

    @abstractmethod
    def aggregate(
        self,
        signals: SignalT,
        context: ContextT,
        config: ConfigT,
    ) -> ResultT: ...


class ResponseSignal(TypedDict):
    """One backend's complete response — input row to ``ResponseAggregator``.

    ``error`` is filled if the runner kept going after a single backend
    failed (token-estimate / judge runners may tolerate a missing
    contestant); ``None`` for the happy path.
    """

    source_id: str
    text: str
    finish_reason: str
    elapsed_ms: int
    error: str | None


class ResponseAggregationResult(TypedDict):
    """Output of a response-scope aggregation."""

    text: str
    metadata: dict


class ResponseAggregator(
    Aggregator[
        ConfigT,
        list[ResponseSignal],
        ResponseAggregationResult,
        SourceAggregationContext,
    ]
):
    """Typed base for response-scope aggregators.

    Sees only the ``SourceAggregationContext`` — backend / runner
    internals are explicitly hidden so a third-party "weighted majority"
    strategy never grows a dependency on PN.py-loop fields it doesn't
    need (ADR-v3-8).
    """

    scope: ClassVar[str] = "response"


class TokenSignals(TypedDict):
    """Per-backend top-k candidates at one token step.

    ``per_model`` is the happy path; ``per_model_errors`` carries
    aliases that produced no usable candidates that step (network blip,
    parser failure). The aggregator decides whether to skip them or
    fall back; v0.1 dropped this distinction.
    """

    per_model: dict[str, list[TokenCandidate]]
    per_model_errors: dict[str, str]


class TokenPick(TypedDict):
    """Aggregator's pick for one token step.

    ``reasoning`` is freeform structured data the aggregator wants in
    the trace (e.g. ``{"per_token_score": {...}}``); only persisted
    when ``DiagnosticsConfig.include_aggregator_reasoning`` is on.
    """

    token: str
    score: float
    reasoning: dict


class TokenAggregator(
    Aggregator[
        ConfigT,
        TokenSignals,
        TokenPick,
        BackendAggregationContext,
    ]
):
    """Typed base for PN.py-style token-scope aggregators."""

    scope: ClassVar[str] = "token"
