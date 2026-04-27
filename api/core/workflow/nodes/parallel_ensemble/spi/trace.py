"""Trace + diagnostics SPI — EXTENSIBILITY_SPEC §7.

Diagnostics are a first-class node config — the original user motivation
("easy access to logits / intermediate data") is exactly this surface,
not an extensibility side effect.

``DiagnosticsConfig`` decides what gets recorded; ``TraceCollector`` is
the *only* thing runner / aggregator code touches at runtime, so a
runner author can blindly ``record_*`` without checking flags. The
collector internally drops entries that the config has not opted into.

⚠️ ``storage`` accepts only ``"inline"`` / ``"metadata"`` in v0.2;
``"artifact"`` is reserved for v0.3 (see spec §7.1) and will be
extended to the Literal at that point. Today an "artifact" value is
rejected by ``extra="forbid"``-style pydantic validation rather than
silently falling back.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from .backend import BackendInfo, TokenCandidate


class DiagnosticsConfig(BaseModel):
    """Per-node diagnostics knobs; lands on ``ParallelEnsembleNodeData``.

    Defaults are conservative:
      - heavy fields (model outputs / logits / per-step candidates /
        aggregator reasoning) are off;
      - lightweight ones (timings, per-backend errors) are on;
      - storage is ``metadata`` so the variable pool stays clean and
        token-step traces of 1k entries don't pollute downstream nodes.
    """

    model_config = ConfigDict(extra="forbid")

    include_model_outputs: bool = False
    include_response_timings: bool = True

    include_token_candidates: bool = False
    include_logits: bool = False
    include_aggregator_reasoning: bool = False
    max_trace_tokens: int = Field(default=1000, gt=0)

    include_think_trace: bool = False

    include_per_backend_errors: bool = True

    storage: Literal["inline", "metadata"] = "metadata"


class TokenStepTraceEntry(TypedDict, total=False):
    """One token-step row.

    ``total=False`` because optional fields (``per_model``,
    ``per_model_errors``, ``aggregator_reasoning``) only appear when the
    corresponding ``DiagnosticsConfig`` flag is set. ``step`` /
    ``selected_token`` / ``selected_score`` / ``elapsed_ms`` are always
    present in practice but the TypedDict is loose so a third-party
    runner can extend with ``x_<runner>_*`` keys (spec §7.5).
    """

    step: int
    selected_token: str
    selected_score: float
    elapsed_ms: int
    per_model: dict[str, list[TokenCandidate]]
    per_model_errors: dict[str, str]
    aggregator_reasoning: dict | None


class ResponseTraceEntry(TypedDict, total=False):
    """One per-backend response-level row.

    ``elapsed_ms`` is always populated (lightweight); ``text`` /
    ``error`` are gated on ``include_model_outputs`` /
    ``include_per_backend_errors``.
    """

    source_id: str
    text: str | None
    finish_reason: str
    tokens_count: int
    elapsed_ms: int
    error: str | None


class ThinkTraceEntry(TypedDict):
    """One ``type=think`` model's chain-of-thought slice.

    Only emitted when both the backend reports a ``stop_think`` segment
    AND ``DiagnosticsConfig.include_think_trace`` is set.
    """

    source_id: str
    think_text: str
    elapsed_ms: int


class EnsembleTrace(TypedDict):
    """Final trace blob written to ``outputs.trace`` / ``metadata.ensemble_trace``.

    ``trace_version=1`` is the schema contract — third-party runners
    extending entries with extra keys keep the version unchanged
    (spec §7.5). Bumps require a framework migration.
    """

    trace_version: int
    runner_name: str
    runner_config: dict
    aggregator_name: str
    aggregator_config: dict
    backends: list[BackendInfo]
    diagnostics_config: dict
    response_trace: list[ResponseTraceEntry]
    token_trace: list[TokenStepTraceEntry]
    think_trace: list[ThinkTraceEntry]
    summary: dict


class TraceCollector:
    """Façade that runner / aggregator code talks to instead of raw ``EnsembleTrace``.

    ``record_*`` methods are no-ops when the corresponding
    ``DiagnosticsConfig`` flag is off — runner code never has to check.
    ``record_token_step`` enforces ``max_trace_tokens`` by dropping
    earlier entries (last-N retention) and setting
    ``summary["truncated"] = True`` on finalize so consumers know.

    Not abstract: a single concrete implementation is fine. v0.2 tests
    instantiate it directly; the framework injects it during node run.
    """

    def __init__(self, config: DiagnosticsConfig) -> None:
        self._config = config
        self._response: list[ResponseTraceEntry] = []
        self._token: list[TokenStepTraceEntry] = []
        self._think: list[ThinkTraceEntry] = []
        self._summary: dict[str, object] = {}
        self._truncated_token_steps: int = 0

    @property
    def config(self) -> DiagnosticsConfig:
        """Read-only access for runner code that wants to short-circuit
        expensive trace prep when nothing is enabled."""
        return self._config

    def record_response(self, entry: ResponseTraceEntry) -> None:
        """Persist one response-level entry, redacting fields the config disabled."""
        filtered: ResponseTraceEntry = {
            "source_id": entry.get("source_id", ""),
            "finish_reason": entry.get("finish_reason", ""),
            "tokens_count": entry.get("tokens_count", 0),
            "elapsed_ms": entry.get("elapsed_ms", 0),
        }
        if self._config.include_model_outputs:
            filtered["text"] = entry.get("text")
        if self._config.include_per_backend_errors:
            filtered["error"] = entry.get("error")
        self._response.append(filtered)

    def record_token_step(self, entry: TokenStepTraceEntry) -> None:
        """Persist one token-step entry (last-N capped at ``max_trace_tokens``)."""
        filtered: TokenStepTraceEntry = {
            "step": entry.get("step", 0),
            "selected_token": entry.get("selected_token", ""),
            "selected_score": entry.get("selected_score", 0.0),
            "elapsed_ms": entry.get("elapsed_ms", 0),
        }
        if self._config.include_token_candidates and "per_model" in entry:
            filtered["per_model"] = entry["per_model"]
        if self._config.include_per_backend_errors and "per_model_errors" in entry:
            filtered["per_model_errors"] = entry["per_model_errors"]
        if self._config.include_aggregator_reasoning and "aggregator_reasoning" in entry:
            filtered["aggregator_reasoning"] = entry["aggregator_reasoning"]
        self._token.append(filtered)
        if len(self._token) > self._config.max_trace_tokens:
            # last-N retention: keep the most recent window so the tail
            # of the run (where errors usually surface) survives.
            drop = len(self._token) - self._config.max_trace_tokens
            self._token = self._token[drop:]
            self._truncated_token_steps += drop

    def record_think(self, entry: ThinkTraceEntry) -> None:
        if self._config.include_think_trace:
            self._think.append(entry)

    def record_summary(self, key: str, value: object) -> None:
        self._summary[key] = value

    def finalize(
        self,
        *,
        runner_name: str,
        runner_config: dict,
        aggregator_name: str,
        aggregator_config: dict,
        backends: list[BackendInfo],
    ) -> EnsembleTrace:
        """Compose the final ``EnsembleTrace``; called by the node, not the runner."""
        if self._truncated_token_steps:
            self._summary.setdefault("truncated", True)
            self._summary.setdefault("truncated_token_steps", self._truncated_token_steps)
        return EnsembleTrace(
            trace_version=1,
            runner_name=runner_name,
            runner_config=runner_config,
            aggregator_name=aggregator_name,
            aggregator_config=aggregator_config,
            backends=backends,
            diagnostics_config=self._config.model_dump(),
            response_trace=list(self._response),
            token_trace=list(self._token),
            think_trace=list(self._think),
            summary=dict(self._summary),
        )
