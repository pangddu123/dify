"""Aggregator SPI — EXTENSIBILITY_SPEC §6.

v0.1 had a single ``aggregate(signals: object, config: dict) -> object``
that gave third-party authors no idea what shape ``signals`` was. v0.2
splits this into a generic ``Aggregator[ConfigT, SignalT, ResultT]`` plus
two typed bases (``ResponseAggregator`` / ``TokenAggregator``) and an
``AggregationContext`` that carries weights / capabilities / trace
handle alongside the signals.

A custom new scope (``SemanticAggregator``-style) just subclasses the
generic base and pairs with a runner that declares the same ``scope``
string. The framework matches them by string at startup; UI dropdowns
filter by that string.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, TypedDict, TypeVar

from pydantic import BaseModel, ConfigDict

from .backend import BackendInfo, TokenCandidate
from .capability import Capability
from .trace import TraceCollector

ConfigT = TypeVar("ConfigT", bound=BaseModel)
SignalT = TypeVar("SignalT")
ResultT = TypeVar("ResultT")


class AggregationContext(BaseModel):
    """Read-only run context handed to ``aggregate``.

    Frozen so an aggregator that tries to mutate it fails fast. The
    ``trace`` handle is the same ``TraceCollector`` the runner uses, so
    aggregators that want to record their reasoning go through the
    ordinary ``record_token_step`` / ``record_summary`` channels (subject
    to ``DiagnosticsConfig`` gating).

    ``arbitrary_types_allowed`` is set so the ``trace`` field can hold
    a non-pydantic ``TraceCollector`` instance.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    backends: list[BackendInfo]
    weights: dict[str, float]
    capabilities: dict[str, frozenset[Capability]]
    runner_name: str
    runner_config: dict
    trace: TraceCollector
    elapsed_ms_so_far: int
    step_index: int | None = None


class Aggregator[ConfigT: BaseModel, SignalT, ResultT](ABC):
    """Generic three-parameter aggregator base.

    ``ConfigT`` = aggregator config schema (pydantic);
    ``SignalT`` = what the runner hands in (response list, token signals, …);
    ``ResultT`` = what the runner gets back to drive its ``yield``.

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
        context: AggregationContext,
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
    Aggregator[ConfigT, list[ResponseSignal], ResponseAggregationResult]
):
    """Typed base for response-scope aggregators (P1 majority_vote / concat fit here)."""

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


class TokenAggregator(Aggregator[ConfigT, TokenSignals, TokenPick]):
    """Typed base for PN.py-style token-scope aggregators."""

    scope: ClassVar[str] = "token"
