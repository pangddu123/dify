"""Token-level joint runner — equivalent to PN.py ``MultiModelHandler.generate_response``.

Drives the per-token consensus loop: every step, every backend produces
its top-k candidates concurrently; a token-scope aggregator reduces
those into a single winning token; the same token is appended to every
backend's running prompt so the next step keeps them in lock-step.

Three deliberate diversions from PN.py
--------------------------------------

1. **Aggregator is pluggable**. PN.py inlines the score aggregation
   into ``calculate_scores``; here the aggregator is an SPI-pluggable
   ``TokenAggregator`` (``sum_score`` / ``max_score`` ship in the box,
   third parties can register more). Picks land in a structured
   ``TokenPick`` so the trace records the rationale.

2. **Concurrency goes through a caller-supplied executor**. PN.py
   constructs its own ``ThreadPoolExecutor``; here the node owns the
   pool (one shared per ``GraphEngine`` run) and hands it in. Reuse
   keeps thread count bounded across many parallel-ensemble nodes in
   the same workflow, and it is the only sane way to surface
   ``PARALLEL_ENSEMBLE_MAX_WORKERS`` knobs (see TASKS.md P2.9 R10).

3. **Aggregator config is bound at construction**. The frozen SPI
   ``run(...)`` signature has no aggregator-config slot, but
   ``TokenAggregator.aggregate(signals, ctx, config)`` requires one.
   The node — the only layer that owns both runner-side and aggregator-
   side config dicts — instantiates ``TokenStepRunner`` with the
   already-validated aggregator config. Tests do the same.

Trace + diagnostics
-------------------

The runner always calls ``trace.record_token_step`` and
``trace.record_summary``; the ``TraceCollector`` decides what is
actually persisted based on ``DiagnosticsConfig``. This means the
runner code never has to branch on diagnostics flags — a property the
SPI promises to extension authors.

KV-cache reuse (PN.py ``clear_slot_kv_cache``) is intentionally not
implemented. EXTENSIBILITY_SPEC §1.2 lists it as a non-goal for v0.2;
the ``KV_CACHE_REUSE`` capability slot exists so a fork can declare it
without breaking the SPI.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ..registry.runner_registry import register_runner
from ..spi.aggregator import (
    Aggregator,
    BackendAggregationContext,
    TokenAggregator,
    TokenPick,
    TokenSignals,
)
from ..spi.backend import BackendInfo, ModelBackend, TokenCandidate, TokenStepParams
from ..spi.capability import Capability
from ..spi.requirements import Requirement, ValidationIssue
from ..spi.runner import DoneEvent, EnsembleRunner, RunnerEvent, SourceInput, TokenEvent
from ..spi.trace import TokenStepTraceEntry, TraceCollector
from .think_phase import ThinkPhaseRunner

if TYPE_CHECKING:
    from ..registry.model_registry import ModelRegistry

logger = logging.getLogger(__name__)


_END_TOKEN_SENTINEL = "<end>"
"""Canonical cross-backend end marker, mirrored from
``backends/llama_cpp.py``. Duplicated rather than imported so the runner
package does not pick up a hard dependency on a specific backend
module — third-party backends are expected to surface this sentinel
through the SPI in the same way (see EXTENSIBILITY_SPEC §3.2).

Post-ADR-v3-16: the chat-template / system-prompt fallback that PN.py
inlined here moved upstream to the ``token-model-source`` node. The
runner now consumes prompts as the source produced them — every
voter's starting prompt is whatever its source's
``ModelInvocationSpec.prompt`` carries, so research configurations
that intentionally feed different priming text per voter (e.g.,
chain-of-thought vs vanilla) round-trip through the SPI without the
runner re-templating them."""


class TokenStepConfig(BaseModel):
    """Pydantic schema for ``token_step`` runner config (DSL slice).

    Post-ADR-v3-16: ``top_k`` no longer lives here. Each source's
    ``ModelInvocationSpec.sampling_params.top_k`` (optionally
    overridden by ``TokenSourceRef.top_k_override``) drives both the
    backend call and the §9 capability cap; surfacing a runner-level
    ``top_k`` would be misleading because nothing reads it.
    """

    model_config = ConfigDict(extra="forbid")

    max_len: int = Field(default=1000, gt=0)
    """Hard ceiling on the number of joint tokens produced before the
    runner force-stops with ``stopped_by="max_len"``. Mirrors PN.py's
    ``args['max_len']``."""

    enable_think: bool = True
    """Whether to run the ``ThinkPhaseRunner`` pre-pass for ``type=think``
    backends. Has no effect when no selected alias declares
    ``type=think`` (validate_selection emits a warning in that case)."""


@register_runner("token_step")
class TokenStepRunner(EnsembleRunner[TokenStepConfig]):
    """PN.py-style joint per-token voting runner."""

    config_class: ClassVar[type[BaseModel]] = TokenStepConfig
    aggregator_scope: ClassVar[str] = "token"
    required_capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS})
    optional_capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.CHAT_TEMPLATE})

    i18n_key_prefix: ClassVar[str] = "parallelEnsemble.runners.tokenStep"
    ui_schema: ClassVar[dict] = {
        "max_len": {"control": "number_input", "min": 1, "step": 1},
        "enable_think": {"control": "switch"},
    }

    def __init__(
        self,
        executor: ThreadPoolExecutor,
        aggregator_config: BaseModel,
    ) -> None:
        """``executor`` is the shared pool the node owns; ``aggregator_config``
        is the already-validated config the paired token aggregator
        consumes. Both come from the node side (P2.8) — see module
        docstring for why config is bound here rather than passed
        through ``run()``.
        """
        self._executor = executor
        self._aggregator_config = aggregator_config

    # ── Validation hooks ──────────────────────────────────────────────

    @classmethod
    def requirements(cls, config: TokenStepConfig) -> list[Requirement]:
        """Two requirements per source: candidate count + probability access.

        ``min_top_k`` is a per-source claim — ADR-v3-16 moved ``top_k``
        out of the runner config and onto each source's effective
        ``TokenStepParams``, so the runner returns the requirement
        *shape* (kind + rationale template) and the node substitutes
        the per-source ``value`` from ``effective_params[sid].top_k``
        before calling ``backend.validate_requirements``. The
        placeholder ``value=1`` is what a source would land on if it
        omitted top_k entirely; the substitution path tested in
        ``test_top_k_override_drives_per_source_requirement`` pins the
        contract.

        ``needs_logprobs`` is a coarser declaration that the runner
        needs usable probability values, not just rank — backends
        that omit ``TOP_PROBS`` fail capability filtering before this
        requirement is even checked, but the explicit declaration is
        useful for tooltips ("why does this backend show as
        unavailable?").
        """
        del config
        return [
            {
                "kind": "min_top_k",
                "value": 1,
                "rationale": "token_step uses each source's effective top_k (ADR-v3-16)",
            },
            {
                "kind": "needs_logprobs",
                "value": True,
                "rationale": "token_step needs candidate probabilities, not just ranks",
            },
        ]

    @classmethod
    def validate_selection(
        cls,
        config: TokenStepConfig,
        model_aliases: list[str],
        registry: ModelRegistry,
    ) -> list[ValidationIssue]:
        """Cross-field rules: ≥ 2 models + think-mode/model coherence."""
        issues: list[ValidationIssue] = []

        if len(model_aliases) < 2:
            issues.append(
                {
                    "severity": "error",
                    "requirement": {
                        "kind": "min_top_k",
                        "value": 0,
                        "rationale": "token_step needs ≥ 2 models to vote",
                    },
                    "message": "token_step requires at least 2 model aliases",
                    "i18n_key": "parallelEnsemble.errors.tooFewModels",
                }
            )

        # ``type`` is a llama.cpp-specific field; only inspect it when present.
        # Keeps the runner backend-agnostic — third-party backends without a
        # ``type`` field are simply treated as non-think.
        think_aliases = [
            alias
            for alias in model_aliases
            if alias in registry and getattr(registry.get(alias), "type", None) == "think"
        ]

        if config.enable_think and not think_aliases:
            issues.append(
                {
                    "severity": "warning",
                    "requirement": {
                        "kind": "needs_chat_template",
                        "value": False,
                        "rationale": "enable_think=True but no think-type models selected",
                    },
                    "message": (
                        "enable_think is on but none of the selected models are "
                        "type=think; the think phase will be a no-op"
                    ),
                    "i18n_key": "parallelEnsemble.errors.thinkNoModels",
                }
            )
        if not config.enable_think and think_aliases:
            issues.append(
                {
                    "severity": "warning",
                    "requirement": {
                        "kind": "needs_chat_template",
                        "value": False,
                        "rationale": "enable_think=False but think-type models selected",
                    },
                    "message": (
                        "enable_think is off but some selected models are type=think; "
                        "their chain-of-thought markers will be voted on as ordinary tokens"
                    ),
                    "i18n_key": "parallelEnsemble.errors.thinkOffWithThinkModels",
                }
            )
        return issues

    # ── Run loop ──────────────────────────────────────────────────────

    def run(
        self,
        sources: dict[str, SourceInput],
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: TokenStepConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        """PN.py main loop: think pre-pass → joint token consensus → DoneEvent."""
        if not isinstance(aggregator, TokenAggregator):
            # Defensive: the §9 scope check should already have rejected
            # this combination; if it didn't, fail loud rather than emit
            # gibberish.
            raise TypeError(f"token_step runner requires a TokenAggregator, got {type(aggregator).__name__}")

        run_start = time.perf_counter()

        # Pre-rendered per-source prompts come from upstream
        # ``token-model-source`` nodes (ADR-v3-16). The runner mutates a
        # local copy so the caller's dict survives the loop intact.
        prompts: dict[str, str] = {sid: src["prompt"] for sid, src in sources.items()}
        params_per_source: dict[str, TokenStepParams] = {sid: src["params"] for sid, src in sources.items()}
        weights: dict[str, float] = {sid: src["weight"] for sid, src in sources.items()}
        # ``source_meta`` stays empty in token mode — backend-private
        # extras already ride on ``params.extra`` per source, and v0.3
        # has no aggregator-vocab per-source field. A future strategy
        # that wants e.g. ``confidence_tier`` per source can populate
        # this from a new ``TokenSourceRef`` channel without touching
        # the runner.
        source_meta: dict[str, dict[str, object]] = {sid: {} for sid in sources}

        if config.enable_think:
            think = ThinkPhaseRunner(self._executor)
            suffixes = think.run(prompts, backends, trace)
            for sid, suffix in suffixes.items():
                if suffix:
                    prompts[sid] = prompts[sid] + suffix

        capabilities = {sid: backend.instance_capabilities for sid, backend in backends.items()}
        # Project each ``ModelBackend`` instance to the public
        # ``BackendInfo`` surface so ``TokenAggregator`` implementations
        # can read backend metadata (id / backend-class name / declared
        # caps) without poking at ``_spec``. ADR-v3-8 promises this on
        # the token-mode context — leaving it as ``[]`` would force
        # third-party token aggregators back to private state.
        # Insertion order mirrors ``sources`` below so a strategy can
        # zip the two without keying lookups.
        backend_infos: list[BackendInfo] = [
            BackendInfo(
                id=backend.id,
                backend=type(backend).name,
                model_name=backend.model_name,
                capabilities=sorted(c.value for c in backend.instance_capabilities),
                metadata={},
            )
            for backend in backends.values()
        ]
        runner_config_dump = config.model_dump()

        accumulated = ""
        step = 0
        stopped_by = "max_len"

        while step < config.max_len:
            step_start = time.perf_counter()

            per_model, per_model_errors = self._step_concurrent(
                backends=backends,
                prompts=prompts,
                params_per_source=params_per_source,
            )

            ctx = BackendAggregationContext(
                sources=list(backends.keys()),
                weights=weights,
                source_meta=source_meta,
                strategy_config=self._aggregator_config.model_dump(),
                backends=backend_infos,
                capabilities=capabilities,
                runner_name=type(self).name,
                runner_config=runner_config_dump,
                trace=trace,
                elapsed_ms_so_far=int((time.perf_counter() - run_start) * 1000),
                step_index=step,
            )
            pick: TokenPick = aggregator.aggregate(
                TokenSignals(per_model=per_model, per_model_errors=per_model_errors),
                ctx,
                self._aggregator_config,
            )

            elapsed_ms = int((time.perf_counter() - step_start) * 1000)
            trace.record_token_step(
                TokenStepTraceEntry(
                    step=step,
                    selected_token=pick["token"],
                    selected_score=pick["score"],
                    elapsed_ms=elapsed_ms,
                    per_model=per_model,
                    per_model_errors=per_model_errors,
                    aggregator_reasoning=pick["reasoning"],
                )
            )

            token = pick["token"]
            if token == _END_TOKEN_SENTINEL:
                stopped_by = "eos"
                break
            if token == "":
                # Aggregator's "all voters empty" sentinel — every backend
                # failed this step; nothing useful to append. Treat it as
                # a soft EOS so the runner doesn't loop forever returning
                # blanks. Surfaced separately from "eos" so trace
                # consumers can tell the two apart.
                stopped_by = "all_voters_empty"
                break

            for sid in prompts:
                prompts[sid] = prompts[sid] + token
            accumulated += token
            yield TokenEvent(kind="token", delta=token)
            step += 1

        # Loop fell through naturally: hit max_len without seeing EOS.
        # The default ``stopped_by`` set before the loop covers this —
        # avoid re-asserting here so an explicit ``break`` (eos /
        # all_voters_empty) keeps its label.

        total_elapsed_ms = int((time.perf_counter() - run_start) * 1000)
        trace.record_summary("stopped_by", stopped_by)
        trace.record_summary("tokens_count", step)
        trace.record_summary("total_elapsed_ms", total_elapsed_ms)

        yield DoneEvent(
            kind="done",
            text=accumulated,
            metadata={
                "stopped_by": stopped_by,
                "tokens_count": step,
                "elapsed_ms": total_elapsed_ms,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────

    def _step_concurrent(
        self,
        backends: dict[str, ModelBackend],
        prompts: dict[str, str],
        params_per_source: dict[str, TokenStepParams],
    ) -> tuple[dict[str, list[TokenCandidate]], dict[str, str]]:
        """Fan out ``step_token`` across backends, partition into success / error.

        Each source carries its own ``TokenStepParams`` (P3.B.3 / ADR-v3-6
        + ADR-v3-14): the upstream ``token-model-source`` node defines
        ``temperature`` / ``top_p`` / ``stop`` / ``seed`` / ``max_tokens``;
        ``TokenSourceRef.top_k_override`` re-pins ``top_k`` at the
        consumer. The runner just dispatches them — the merge already
        happened in the node.

        A failing backend does not abort the step — its source_id lands in
        ``per_model_errors`` and the aggregator decides whether the
        remaining voters can still pick a winner. This matches PN.py's
        behaviour of returning ``[['<end>', 0.01]]`` on HTTP failure
        and re-emitting ``<end>`` on aggregation, but the SPI surfaces
        the error explicitly so the trace can record it.
        """
        futures: dict[Future[list[TokenCandidate]], str] = {}
        for sid, backend in backends.items():
            future = self._executor.submit(backend.step_token, prompts[sid], params_per_source[sid])
            futures[future] = sid

        per_model: dict[str, list[TokenCandidate]] = {}
        per_model_errors: dict[str, str] = {}
        for future in futures:
            sid = futures[future]
            try:
                per_model[sid] = future.result()
            except Exception as exc:
                per_model_errors[sid] = f"{type(exc).__name__}: {exc}"
        return per_model, per_model_errors
