"""Response-level runner — the SPI-shaped successor to P1 ``EnsembleAggregatorNode``.

P1 fans out via a separate workflow node graph (parallel-branches into
an ``ensemble-aggregator`` collector); the parallel-ensemble node owns
the fan-out itself, so this runner is the in-node analogue: every
selected backend produces a full response concurrently, then a
response-scope aggregator (``majority_vote`` / ``concat`` from P2.5)
reduces the responses to a single answer.

Two deliberate diversions from P1
---------------------------------

1. **One node, not two**. P1 needed ``IterationStartNode`` →
   per-branch LLM nodes → ``ensemble-aggregator``; here the runner
   submits ``backend.generate`` directly to a shared executor and feeds
   the resulting ``ResponseSignal`` list straight to the aggregator. The
   metadata shape (``strategy`` / ``votes`` / ``contributions`` ...) is
   preserved 1:1 because the aggregators are exact migrations of the P1
   strategies.

2. **Errors don't kill the run**. P1's parallel branches were
   independent failure domains; with one node the runner has to make
   the same call explicitly. A failing backend lands in
   ``ResponseSignal.error`` and the aggregator filters it out (matching
   P1 ``majority_vote`` behaviour where errored sources contribute no
   vote). All-errored degrades to ``text=""`` rather than raising — the
   trace ``error_count`` summary still surfaces the failure.

Why ``optional_capabilities = {STREAMING}``
-------------------------------------------

The runner uses ``backend.generate`` (non-streaming), so streaming is
purely informational: the §9 capability filter never blocks a backend
without ``STREAMING``. It surfaces in the panel as "this backend can
stream" so users picking a fully-streaming ensemble see the marker, but
it has no operational effect inside this runner. A future
``StreamingResponseLevelRunner`` could hard-require it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict

from ..registry.runner_registry import register_runner
from ..spi.aggregator import (
    Aggregator,
    BackendAggregationContext,
    ResponseAggregator,
    ResponseSignal,
)
from ..spi.backend import GenerationParams, GenerationResult, ModelBackend
from ..spi.capability import Capability
from ..spi.requirements import Requirement, ValidationIssue
from ..spi.runner import DoneEvent, EnsembleRunner, RunnerEvent
from ..spi.trace import ResponseTraceEntry, TraceCollector

if TYPE_CHECKING:
    from ..registry.model_registry import ModelRegistry

logger = logging.getLogger(__name__)


class ResponseLevelConfig(BaseModel):
    """Empty config — response_level has no tunables today.

    ``extra="forbid"`` so a yaml typo like ``response_level: {top_k: 5}``
    is rejected at startup. Knobs that *might* land here later
    (per-backend timeout override, max-tokens cap, system prompt
    template) are deliberately deferred until a concrete user request
    pins down the exact shape — over-config'd v0.2 surfaces are hard to
    walk back without breaking saved DSLs.
    """

    model_config = ConfigDict(extra="forbid")


@register_runner("response_level")
class ResponseLevelRunner(EnsembleRunner[ResponseLevelConfig]):
    """Concurrent ``backend.generate`` → response-scope aggregation."""

    config_class: ClassVar[type[BaseModel]] = ResponseLevelConfig
    aggregator_scope: ClassVar[str] = "response"
    required_capabilities: ClassVar[frozenset[Capability]] = frozenset()
    optional_capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.STREAMING})

    i18n_key_prefix: ClassVar[str] = "parallelEnsemble.runners.responseLevel"
    ui_schema: ClassVar[dict] = {}

    def __init__(
        self,
        executor: ThreadPoolExecutor,
        aggregator_config: BaseModel,
    ) -> None:
        """Mirrors :class:`TokenStepRunner.__init__`: the node owns the
        shared executor and binds the already-validated aggregator
        config because the SPI ``run(...)`` signature has no slot for
        aggregator-side config (it's a runner / aggregator pairing
        concern, not a node-level routing concern).
        """
        self._executor = executor
        self._aggregator_config = aggregator_config

    # ── Validation hooks ──────────────────────────────────────────────

    @classmethod
    def requirements(cls, config: ResponseLevelConfig) -> list[Requirement]:
        """No config-derived requirements: ``backend.generate`` is the
        SPI floor every backend already implements, so nothing to demand."""
        return []

    @classmethod
    def validate_selection(
        cls,
        config: ResponseLevelConfig,
        model_aliases: list[str],
        registry: ModelRegistry,
    ) -> list[ValidationIssue]:
        """Gate: response-level voting needs ≥ 2 contestants.

        A single-model "ensemble" reduces to the model itself, which is
        better expressed with the bare LLM node — keeping this guard
        loud prevents the node from silently degrading into a one-shot
        passthrough.
        """
        if len(model_aliases) >= 2:
            return []
        return [
            {
                "severity": "error",
                "requirement": {
                    "kind": "min_top_k",
                    "value": 0,
                    "rationale": "response_level needs ≥ 2 models to aggregate",
                },
                "message": "response_level requires at least 2 model aliases",
                "i18n_key": "parallelEnsemble.errors.tooFewModels",
            }
        ]

    # ── Run loop ──────────────────────────────────────────────────────

    def run(
        self,
        question: str,
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: ResponseLevelConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        """Fan out → record per-backend response → aggregate → DoneEvent."""
        if not isinstance(aggregator, ResponseAggregator):
            # Defensive: §9 scope filter should already have rejected
            # the wrong scope; if it didn't, fail loud rather than feed
            # token signals into a response aggregator (or vice versa).
            raise TypeError(f"response_level runner requires a ResponseAggregator, got {type(aggregator).__name__}")

        run_start = time.perf_counter()

        signals, error_count = self._generate_concurrent(question, backends, trace)

        weights = {alias: backend.weight for alias, backend in backends.items()}
        capabilities = {alias: backend.instance_capabilities for alias, backend in backends.items()}
        ctx = BackendAggregationContext(
            sources=list(backends.keys()),
            weights=weights,
            source_meta={},
            strategy_config=self._aggregator_config.model_dump(),
            backends=[],
            capabilities=capabilities,
            runner_name=type(self).name,
            runner_config=config.model_dump(),
            trace=trace,
            elapsed_ms_so_far=int((time.perf_counter() - run_start) * 1000),
            step_index=None,
        )
        result = aggregator.aggregate(signals, ctx, self._aggregator_config)

        total_elapsed_ms = int((time.perf_counter() - run_start) * 1000)
        trace.record_summary("backend_count", len(backends))
        trace.record_summary("error_count", error_count)
        trace.record_summary("total_elapsed_ms", total_elapsed_ms)

        # Hand back the aggregator's metadata verbatim — preserves the
        # P1 ``strategy`` / ``votes`` / ``contributions`` keys the
        # ensemble-aggregator node already exposed, so DSLs migrating
        # from P1 don't need selector rewrites. Runner-level timing /
        # error counts ride on the trace summary instead of polluting
        # the user-visible metadata.
        yield DoneEvent(kind="done", text=result["text"], metadata=result["metadata"])

    # ── Helpers ───────────────────────────────────────────────────────

    def _generate_concurrent(
        self,
        question: str,
        backends: dict[str, ModelBackend],
        trace: TraceCollector,
    ) -> tuple[list[ResponseSignal], int]:
        """Submit every ``backend.generate`` to the shared executor.

        Returns the aggregator's input list plus the count of failed
        backends; ``record_response`` is invoked once per alias (success
        or error path) so downstream tracing is symmetric.

        Failure semantics: a backend that raises does *not* abort the
        run. Its alias appears in the returned signals with
        ``error=str(exc)`` and ``text=""``; aggregators (``majority_vote``
        / ``concat``) filter those out. P1's parallel-branch model gave
        this for free; the in-node runner has to reproduce it.

        Two correctness invariants worth flagging
        -----------------------------------------

        1. ``elapsed_ms`` is measured **after** the future completes,
           via ``as_completed``. A naive insertion-order iteration that
           computed elapsed_ms before ``future.result()`` would record
           ~0 for the first future and inherit its wait time on the
           rest — a fast backend submitted second behind a 1500 ms
           backend would show 1500 ms instead of its own true duration.
           ``as_completed`` gives each future a tight upper bound
           (= time until result was observable to this thread).

        2. Each ``submit`` gets a **fresh** ``GenerationParams`` dict.
           The SPI does not forbid backends from mutating their input
           (a future ``OpenAICompatBackend`` might
           ``params.setdefault("max_tokens", 1024)`` for ergonomics);
           sharing one ``{}`` across concurrent ``generate`` calls
           would let that mutation leak across backends. Cheap to
           defend against, expensive to debug if it ever fires.

        Final signal / trace order is the original ``backends``-dict
        insertion order, regardless of which future finished first, so
        ``ConcatAggregator`` output stays deterministic.
        """
        per_alias_starts: dict[str, float] = {}
        futures: dict[Future[GenerationResult], str] = {}
        for alias, backend in backends.items():
            per_alias_starts[alias] = time.perf_counter()
            params: GenerationParams = {}
            future = self._executor.submit(backend.generate, question, params)
            futures[future] = alias

        successes: dict[str, tuple[GenerationResult, int]] = {}
        errors: dict[str, tuple[str, int]] = {}
        for future in as_completed(futures):
            alias = futures[future]
            try:
                gen = future.result()
                elapsed_ms = int((time.perf_counter() - per_alias_starts[alias]) * 1000)
                successes[alias] = (gen, elapsed_ms)
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - per_alias_starts[alias]) * 1000)
                logger.warning("response_level generate failed for %s: %s", alias, exc)
                errors[alias] = (f"{type(exc).__name__}: {exc}", elapsed_ms)

        signals: list[ResponseSignal] = []
        for alias in backends:
            if alias in errors:
                error_message, elapsed_ms = errors[alias]
                signals.append(
                    ResponseSignal(
                        source_id=alias,
                        text="",
                        finish_reason="error",
                        elapsed_ms=elapsed_ms,
                        error=error_message,
                    )
                )
                trace.record_response(
                    ResponseTraceEntry(
                        source_id=alias,
                        text=None,
                        finish_reason="error",
                        tokens_count=0,
                        elapsed_ms=elapsed_ms,
                        error=error_message,
                    )
                )
                continue

            gen, elapsed_ms = successes[alias]
            text = gen.get("text", "")
            finish_reason = gen.get("finish_reason", "stop")
            # Backend metadata may carry a token count (llama.cpp returns
            # ``tokens_predicted``); read defensively so backends that
            # omit it don't crash trace assembly. Treat any non-int as 0
            # rather than coercing — a string here would be a backend bug
            # and silent ``int("foo")`` would mask it.
            metadata = gen.get("metadata", {}) or {}
            raw_tokens = metadata.get("tokens_count", metadata.get("tokens_predicted", 0))
            tokens_count = raw_tokens if isinstance(raw_tokens, int) else 0

            signals.append(
                ResponseSignal(
                    source_id=alias,
                    text=text,
                    finish_reason=finish_reason,
                    elapsed_ms=elapsed_ms,
                    error=None,
                )
            )
            trace.record_response(
                ResponseTraceEntry(
                    source_id=alias,
                    text=text,
                    finish_reason=finish_reason,
                    tokens_count=tokens_count,
                    elapsed_ms=elapsed_ms,
                    error=None,
                )
            )
        return signals, len(errors)
