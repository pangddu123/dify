"""``ParallelEnsembleNode`` — graphon node binding for the v3 SPI (P3.B.3).

Translates between graphon's event protocol and the SPI surface that
``runners/`` and ``aggregators/`` already implement: the node owns the
five "framework" responsibilities (variable pool I/O, source spec
resolution, §9 startup validation, trace storage, ``StreamCompleted``
emission); the runner owns the algorithm; the aggregator owns the
signal reduction. None of those layers reach into graphon directly —
keeping the SPI testable without spinning up a graph runtime is the
explicit goal of EXTENSIBILITY_SPEC §1.1 ("runner / aggregator stay
graphon-decoupled").

ADR-v3-16 reshape
-----------------

The node no longer owns the question variable or the alias list:

* Each ``token_source`` carries a ``spec_selector`` pointing at the
  ``outputs.spec`` field of an upstream ``token-model-source`` node. At
  run time the variable pool yields one
  :class:`~core.workflow.nodes.token_model_source.entities.ModelInvocationSpec`
  per source — the pre-rendered prompt + chosen ``model_alias`` + the
  source's ``sampling_params`` all live in that spec, so prompt
  templating moves out of the node and into the per-source node where
  the user authored it.
* Backends are keyed by ``source_id`` (not ``model_alias``) so the same
  model can appear twice — the canonical "self-consistency at
  temperature=0.3 vs 1.0" setup — without colliding in trace / weights /
  per-model dicts.
* Per-source ``TokenStepParams`` are constructed once per run from
  ``spec.sampling_params`` ⊕ ``TokenSourceRef.top_k_override`` (the
  override wins on ``top_k``), then handed to the runner via
  :class:`~core.workflow.nodes.parallel_ensemble.spi.runner.SourceInput`.

Selector / event quirks worth pinning here so a future maintainer does
not have to re-derive them from graphon source:

* ``selector`` for streaming chunks must be ``[self._node_id, "text"]``.
  ``self.id`` and ``self._node_id`` carry the same value at runtime
  (both are the graph node id), but graphon's ``_dispatch`` mostly
  uses ``_node_id`` and we follow suit to keep dispatch records uniform —
  see ``graphon/nodes/base/node.py`` ``_dispatch.register(StreamChunkEvent)``.
* ``StreamCompletedEvent``'s argument is ``node_run_result=`` — a bare
  positional or ``run_result=`` would be rejected by pydantic on a
  different validation path. v1 of this design got that wrong; the
  keyword form is the contract.
* Token streaming closes with a ``StreamChunkEvent(chunk="", is_final=True)``
  *before* the ``StreamCompletedEvent``. Without that closing chunk the
  Answer node downstream never flushes its accumulator (matches the
  agent node's pattern).

Trace storage deviation (vs EXTENSIBILITY_SPEC §7.4)
----------------------------------------------------

The spec text reads ``metadata["ensemble_trace"] = trace`` for the
``storage="metadata"`` path, but graphon's ``NodeRunResult.metadata``
is typed as ``Mapping[WorkflowNodeExecutionMetadataKey, Any]`` and
rejects unknown string keys at pydantic validation time
(``pydantic_core.ValidationError: enum``). We can't extend that enum
from this package, so the trace lands in ``process_data["ensemble_trace"]``
instead — that field is ``Mapping[str, Any]``, is persisted into
``node_execution.process_data`` (services/workflow_service.py:1430),
and shows up in run-history viewers exactly the way the spec
intent describes (queryable for debugging, **not** in the variable
pool, ``outputs.text`` clean). EXTENSIBILITY_SPEC §7.4 will be
updated to point at ``process_data`` in the next doc pass.

§9 validation pipeline ordering
-------------------------------

The validation pipeline in ``_validate_at_startup`` runs in the exact
order EXTENSIBILITY_SPEC §9 specifies, and with intent:

1. **Scope alignment first**. A wrong-scope aggregator turns every
   later check into a category error; reject with a structured
   message before any backend gets instantiated.
2. **Schema validation** of ``runner_config`` / ``aggregator_config``
   second. The pydantic ``ValidationError`` carries the field-level
   detail the panel needs; we let it propagate untouched.
3. **Capability filter** (coarse, per-source). Cheap, no IO. Issues
   short-circuit the next pass.
4. **Requirements** (precision, per-source × per-requirement). Calls
   the backend class's ``validate_requirements`` with the source's
   *effective* ``TokenStepParams`` (so per-source ``top_k_override``
   actually drives the cap rejection), still no instances created.
5. **Cross-field** ``validate_selection``. Last because it can use the
   already-validated runner config + the registry to make
   alias-relative claims (``judge_alias`` must be selected, ≥ 2
   contestants, etc).

All five steps fold into a single ``StructuredValidationError``
when any of them surfaces an ``error``-severity issue, so the panel
shows every offence on the first pass instead of one per save.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Generator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import ValidationError

from graphon.enums import NodeType, WorkflowNodeExecutionStatus
from graphon.node_events.base import NodeEventBase, NodeRunResult
from graphon.node_events.node import StreamChunkEvent, StreamCompletedEvent
from graphon.nodes.base.node import Node

from . import PARALLEL_ENSEMBLE_NODE_TYPE
from .entities import ParallelEnsembleConfig, ParallelEnsembleNodeData, TokenSourceRef
from .exceptions import (
    InvalidSpecError,
    MissingSpecError,
    StructuredValidationError,
    WeightResolutionError,
)
from .spi.aggregator import Aggregator
from .spi.backend import BaseSpec, ModelBackend, TokenStepParams
from .spi.requirements import Requirement, ValidationIssue
from .spi.runner import EnsembleRunner, SourceInput
from .spi.trace import EnsembleTrace, TraceCollector

if TYPE_CHECKING:
    from .registry.aggregator_registry import AggregatorRegistry
    from .registry.backend_registry import BackendRegistry
    from .registry.model_registry import ModelRegistry
    from .registry.runner_registry import RunnerRegistry

logger = logging.getLogger(__name__)


_REQUIRED_SPEC_KEYS: frozenset[str] = frozenset({"model_alias", "prompt", "sampling_params"})
"""Keys :class:`ModelInvocationSpec` carries that the parallel-ensemble
node actually reads. ``extra`` is optional — sources that do not need a
pass-through dict skip it. We validate the wire shape here (instead of
re-parsing through the source-side pydantic model) because the spec
crosses node boundaries via ``VariablePool`` serialization, which
flattens it to a dict regardless of the producing node's schema."""


class ParallelEnsembleNode(Node[ParallelEnsembleNodeData]):
    """Graphon binding for the parallel-ensemble SPI.

    Five external dependencies arrive as keyword-only init args (the
    DifyNodeFactory branch landed in P2.9 wires them):

    * ``model_registry`` — yaml-backed alias → spec table.
    * ``runner_registry`` — class-level map of name → ``EnsembleRunner``.
    * ``aggregator_registry`` — class-level map of name → ``Aggregator``.
    * ``backend_registry`` — class-level map of backend → backend class.
    * ``executor`` — shared ``ThreadPoolExecutor`` the runner uses for
      its fan-out. Sharing one pool across many ``parallel-ensemble``
      nodes in the same workflow is what keeps thread count bounded
      under load (TASKS.md R10).

    HTTP traffic to model endpoints flows through the object the
    factory passes as ``http_client``. The default falls back to
    ``core.helper.ssrf_proxy.ssrf_proxy`` so the node remains
    constructible from tests / standalone scripts; production wiring
    overrides this with the same ssrf-proxy instance the HTTP-request
    node already uses.
    """

    node_type: ClassVar[NodeType] = PARALLEL_ENSEMBLE_NODE_TYPE

    @classmethod
    def version(cls) -> str:
        return "1"

    def __init__(
        self,
        id: str,
        config: Any,
        graph_init_params: Any,
        graph_runtime_state: Any,
        *,
        model_registry: ModelRegistry,
        runner_registry: type[RunnerRegistry],
        aggregator_registry: type[AggregatorRegistry],
        backend_registry: type[BackendRegistry],
        executor: ThreadPoolExecutor,
        http_client: object | None = None,
    ) -> None:
        super().__init__(
            id=id,
            config=config,
            graph_init_params=graph_init_params,
            graph_runtime_state=graph_runtime_state,
        )
        self._model_registry = model_registry
        self._runner_registry = runner_registry
        self._aggregator_registry = aggregator_registry
        self._backend_registry = backend_registry
        self._executor = executor
        # Backend instances need an ``HttpClientProtocol`` for SPI
        # compliance; production wires ``ssrf_proxy`` here. The lazy
        # import keeps standalone unit tests / tools that build a
        # ``ParallelEnsembleNode`` instance with an explicit mock from
        # paying the import cost of a Flask-side helper.
        if http_client is None:
            from core.helper.ssrf_proxy import ssrf_proxy

            http_client = ssrf_proxy
        self._http_client = http_client

    # ── Run loop ─────────────────────────────────────────────────────────

    def _run(self) -> Generator[NodeEventBase, None, None]:
        cfg = self.node_data.ensemble

        # Resolve every upstream ``ModelInvocationSpec`` first — a missing
        # spec is a fail-fast condition: the joint loop has no defined
        # behaviour for an absent voter, so we surface it before any
        # backend is instantiated.
        specs = self._collect_specs(cfg.token_sources)

        # Per-source ``TokenStepParams`` carry whatever sampling the
        # upstream source produced (temperature / top_p / stop / seed /
        # max_tokens) plus the ``top_k_override`` this node injects.
        # Built before §9 validation so the requirements pass can use
        # each source's *effective* top_k for the cap rejection.
        effective_params = self._build_effective_params(cfg.token_sources, specs)

        runner_cls = self._runner_registry.get(cfg.runner_name)
        aggregator_cls = self._aggregator_registry.get(cfg.aggregator_name)

        # Step 2 of §9: schema-validate runner / aggregator configs first
        # so a failed extra-forbid catches DSL smuggle (model_url inside
        # runner_config etc.) before we touch the registries any further.
        runner_config = runner_cls.config_class.model_validate(cfg.runner_config)
        aggregator_config = aggregator_cls.config_class.model_validate(cfg.aggregator_config)

        self._validate_at_startup(
            runner_cls=runner_cls,
            aggregator_cls=aggregator_cls,
            runner_config=runner_config,
            cfg=cfg,
            specs=specs,
            effective_params=effective_params,
        )

        backends = self._instantiate_backends(cfg.token_sources, specs)
        weights = self._resolve_weights(cfg.token_sources)
        sources = self._build_source_inputs(cfg.token_sources, specs, effective_params, weights)

        trace = TraceCollector(cfg.diagnostics)
        runner = self._instantiate_runner(runner_cls, aggregator_config)
        aggregator = aggregator_cls()

        run_started = time.perf_counter()
        accumulated = ""

        # Drive the runner — translate ``token`` into graphon
        # ``StreamChunkEvent``s as they arrive; ``done`` provides the
        # canonical final text for runners that produce a single answer
        # without per-token streaming; ``full_response`` is recorded
        # into the trace but does not stream a chunk (judge-style
        # runners surface contestants, not user-facing chunks).
        #
        # ``match`` over the ``kind`` discriminator is the form
        # basedpyright narrows the ``RunnerEvent`` union with — an
        # ``if event["kind"] == ...`` chain leaves ``event["delta"]``
        # ambiguous because the union members do not all carry the
        # same keys.
        for event in runner.run(sources, backends, aggregator, runner_config, trace):
            match event:
                case {"kind": "token", "delta": delta}:
                    accumulated += delta
                    yield StreamChunkEvent(
                        selector=[self._node_id, "text"],
                        chunk=delta,
                        is_final=False,
                    )
                case {"kind": "done", "text": done_text}:
                    # ``token_step`` emits ``done`` after the last
                    # ``token``; a non-streaming third-party runner can
                    # emit ``done`` with full text. Replacing
                    # ``accumulated`` only when no tokens streamed keeps
                    # both contracts working from the same branch — a
                    # token-streaming runner that also sets
                    # ``DoneEvent.text`` does not erase the per-chunk
                    # accumulator.
                    if not accumulated:
                        accumulated = done_text
                case {"kind": "full_response", "source_id": source_id}:
                    # v0.2 has no built-in judge runner; keep the
                    # branch so third-party runners that emit
                    # per-contestant responses don't crash this node.
                    # Trace recording is the runner's job (it has
                    # access to the ``TraceCollector``); the node only
                    # needs to consume the event without mis-streaming
                    # it as a user-facing chunk.
                    logger.debug(
                        "parallel-ensemble node %s saw full_response from %s",
                        self._node_id,
                        source_id,
                    )
                case _:
                    # Defensive: ``RunnerEvent`` is a closed union
                    # today, but a third-party runner may yield an
                    # event whose ``kind`` we don't recognise; logging
                    # is preferable to crashing the workflow on a
                    # well-meaning future event type.
                    logger.warning(
                        "parallel-ensemble node %s ignoring unknown runner event %r",
                        self._node_id,
                        event,
                    )

        # Closing-chunk for the streaming protocol; without it the
        # downstream Answer node never flushes (graphon agent pattern).
        yield StreamChunkEvent(
            selector=[self._node_id, "text"],
            chunk="",
            is_final=True,
        )

        elapsed_ms = int((time.perf_counter() - run_started) * 1000)
        outputs, process_data, status = self._finalize_outputs(
            accumulated=accumulated,
            elapsed_ms=elapsed_ms,
            trace=trace,
            runner_cls=runner_cls,
            aggregator_cls=aggregator_cls,
            runner_config=runner_config,
            aggregator_config=aggregator_config,
            backends=backends,
            specs=specs,
        )

        yield StreamCompletedEvent(
            node_run_result=NodeRunResult(
                status=status,
                inputs={
                    "sources": [ref.source_id for ref in cfg.token_sources],
                    "models": [specs[ref.source_id]["model_alias"] for ref in cfg.token_sources],
                    "runner": cfg.runner_name,
                    "aggregator": cfg.aggregator_name,
                },
                process_data=process_data,
                outputs=outputs,
                error=("" if status == WorkflowNodeExecutionStatus.SUCCEEDED else "all backends failed"),
            )
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _collect_specs(self, refs: list[TokenSourceRef]) -> dict[str, dict[str, Any]]:
        """Resolve every ``spec_selector`` against the variable pool.

        Returns ``{source_id: spec_dict}``. Raises :class:`MissingSpecError`
        if any selector misses (the upstream ``token-model-source`` node
        FAILed) and :class:`InvalidSpecError` if the resolved value does
        not carry the ``ModelInvocationSpec`` shape (``model_alias`` /
        ``prompt`` / ``sampling_params``). Both surface as fail-fast
        because the joint loop has no defined behaviour for an absent
        or malformed voter.
        """
        variable_pool = self.graph_runtime_state.variable_pool
        specs: dict[str, dict[str, Any]] = {}
        for ref in refs:
            segment = variable_pool.get(ref.spec_selector)
            if segment is None:
                raise MissingSpecError(
                    source_id=ref.source_id,
                    spec_selector=list(ref.spec_selector),
                )
            value = segment.value
            if not isinstance(value, dict):
                raise InvalidSpecError(
                    source_id=ref.source_id,
                    reason=f"resolved value is {type(value).__name__}, expected dict (ModelInvocationSpec)",
                )
            missing = _REQUIRED_SPEC_KEYS - value.keys()
            if missing:
                raise InvalidSpecError(
                    source_id=ref.source_id,
                    reason=f"missing required keys {sorted(missing)} (have {sorted(value.keys())})",
                )
            if not isinstance(value["model_alias"], str) or not value["model_alias"].strip():
                raise InvalidSpecError(
                    source_id=ref.source_id,
                    reason="model_alias must be a non-empty string",
                )
            if not isinstance(value["prompt"], str):
                raise InvalidSpecError(
                    source_id=ref.source_id,
                    reason=f"prompt must be a string, got {type(value['prompt']).__name__}",
                )
            if not isinstance(value["sampling_params"], dict):
                raise InvalidSpecError(
                    source_id=ref.source_id,
                    reason=(
                        f"sampling_params must be a dict, got {type(value['sampling_params']).__name__}"
                    ),
                )
            specs[ref.source_id] = value
        return specs

    def _build_effective_params(
        self,
        refs: list[TokenSourceRef],
        specs: dict[str, dict[str, Any]],
    ) -> dict[str, TokenStepParams]:
        """Merge ``spec.sampling_params`` ⊕ ``TokenSourceRef.top_k_override`` per source.

        ``top_k_override`` wins on the ``top_k`` field; every other
        sampling knob (temperature / top_p / stop / seed / max_tokens)
        rides through from the spec exactly as the upstream
        ``token-model-source`` node produced it. The result is a frozen
        :class:`TokenStepParams` per source so a misbehaving backend
        cannot mutate the params dict and bleed sampling state across
        sibling sources sharing the params reference.

        Sampling fields that the spec did not carry fall through to
        :class:`TokenStepParams`'s own defaults — the merge is *additive*,
        not overwrite-with-None.

        Backend-private knobs ride on ``TokenStepParams.extra``: the
        upstream ``token-model-source`` node carries them on
        ``ModelInvocationSpec.extra`` (e.g. ``{"mirostat": 2}``) and
        the parallel-ensemble node's :class:`TokenSourceRef.extra`
        layer can override per-source on top of that. This lets the
        backend (e.g. ``llama_cpp.step_token`` writing
        ``params.extra`` straight into the request body) see the
        composed knobs without us reaching into a sibling
        ``SourceInput`` field — i.e. extras *route through sampling*,
        not through aggregator metadata.
        """
        out: dict[str, TokenStepParams] = {}
        for ref in refs:
            spec = specs[ref.source_id]
            sampling_raw = dict(spec["sampling_params"])
            # ``stop`` arrives as a list (the source's pydantic model
            # serialises it that way) but ``TokenStepParams.stop`` is a
            # tuple — coerce here so a typo'd stop list passed straight
            # through doesn't trip the frozen-tuple invariant later.
            if "stop" in sampling_raw and isinstance(sampling_raw["stop"], list):
                sampling_raw["stop"] = tuple(sampling_raw["stop"])
            if ref.top_k_override is not None:
                sampling_raw["top_k"] = ref.top_k_override
            # Drop ``None`` entries before validation so the spec's
            # "let backend decide" optionals don't override
            # ``TokenStepParams``'s defaults with explicit ``None``
            # (which would fail ``gt=0`` etc. on numeric fields).
            sampling_clean = {k: v for k, v in sampling_raw.items() if v is not None or k == "seed"}
            # Backend-private extras: spec.extra is the producer-vocab
            # default, ref.extra wins on key collision (consumer-vocab
            # gets the last word — same precedence response_aggregator
            # uses for its source-level overrides).
            spec_extra = spec.get("extra")
            spec_extra_dict: dict[str, Any] = dict(spec_extra) if isinstance(spec_extra, dict) else {}
            merged_extra = {**spec_extra_dict, **dict(ref.extra)}
            if merged_extra:
                sampling_clean["extra"] = merged_extra
            try:
                out[ref.source_id] = TokenStepParams.model_validate(sampling_clean)
            except ValidationError as exc:
                # Wrap so the panel sees *which* source's sampling
                # tripped validation; the bare pydantic error doesn't
                # carry source_id and would force the user to grep the
                # graph by alias.
                raise InvalidSpecError(
                    source_id=ref.source_id,
                    reason=f"sampling_params failed validation: {exc}",
                ) from exc
        return out

    def _resolve_weights(self, refs: list[TokenSourceRef]) -> dict[str, float]:
        """Resolve ``TokenSourceRef.weight`` per source.

        Static numeric branch returns directly. Dynamic
        ``VariableSelector``-shaped list branch reads the pool and
        coerces to float; coercion failure escalates to
        :class:`WeightResolutionError` unless ``fallback_weight`` opts
        into the graceful-degrade path (ADR-v3-15).
        """
        variable_pool = self.graph_runtime_state.variable_pool
        weights: dict[str, float] = {}
        for ref in refs:
            if isinstance(ref.weight, (int, float)):
                weights[ref.source_id] = float(ref.weight)
                continue
            selector = list(ref.weight)
            try:
                segment = variable_pool.get(selector)
                if segment is None:
                    raise WeightResolutionError(
                        source_id=ref.source_id,
                        selector=selector,
                        reason="variable not present in pool",
                    )
                value = segment.value
                if value is None:
                    raise WeightResolutionError(
                        source_id=ref.source_id,
                        selector=selector,
                        reason="resolved value is None",
                    )
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise WeightResolutionError(
                        source_id=ref.source_id,
                        selector=selector,
                        reason=f"resolved value is not numeric (got {type(value).__name__})",
                    )
                resolved = float(value)
                if not math.isfinite(resolved):
                    raise WeightResolutionError(
                        source_id=ref.source_id,
                        selector=selector,
                        reason=f"resolved value is not finite (got {resolved})",
                    )
                if resolved <= 0.0:
                    # Mirror the static branch's ``> 0`` guard: a zero
                    # or negative resolved weight would silently zero
                    # out a voter (or, worse, let it cancel siblings in
                    # weighted-sum tallying). Static weight is rejected
                    # at schema time; dynamic weight has to be rejected
                    # here because the value isn't known until run
                    # time.
                    raise WeightResolutionError(
                        source_id=ref.source_id,
                        selector=selector,
                        reason=f"resolved value must be > 0 (got {resolved})",
                    )
                weights[ref.source_id] = resolved
            except WeightResolutionError:
                if ref.fallback_weight is None:
                    raise
                logger.warning(
                    "parallel-ensemble node %s: weight selector for source '%s' failed; "
                    "falling back to %s",
                    self._node_id,
                    ref.source_id,
                    ref.fallback_weight,
                )
                weights[ref.source_id] = float(ref.fallback_weight)
        return weights

    def _build_source_inputs(
        self,
        refs: list[TokenSourceRef],
        specs: dict[str, dict[str, Any]],
        effective_params: dict[str, TokenStepParams],
        weights: dict[str, float],
    ) -> dict[str, SourceInput]:
        """Bundle per-source data into the runner's ``SourceInput`` shape.

        Backend-private extras already ride on ``effective_params[sid].extra``
        (see :meth:`_build_effective_params`); ``SourceInput`` deliberately
        carries no parallel ``extra`` field so the same dict cannot end
        up in two places with different precedence semantics.
        """
        out: dict[str, SourceInput] = {}
        for ref in refs:
            spec = specs[ref.source_id]
            out[ref.source_id] = SourceInput(
                prompt=spec["prompt"],
                params=effective_params[ref.source_id],
                weight=weights[ref.source_id],
            )
        return out

    def _validate_at_startup(
        self,
        *,
        runner_cls: type[EnsembleRunner],
        aggregator_cls: type[Aggregator],
        runner_config: Any,
        cfg: ParallelEnsembleConfig,
        specs: dict[str, dict[str, Any]],
        effective_params: dict[str, TokenStepParams],
    ) -> None:
        """EXTENSIBILITY_SPEC §9 startup validation pipeline.

        Aggregates *every* error-severity issue across capability /
        requirements / cross-field passes into a single
        ``StructuredValidationError`` so the panel can render the full
        offending-config picture in one pass instead of leading the
        user through a fix-and-rerun loop.
        """
        # Step 1: scope alignment between runner and aggregator.
        if aggregator_cls.scope != runner_cls.aggregator_scope:
            raise StructuredValidationError(
                [
                    {
                        "severity": "error",
                        "requirement": {
                            # "kind" is open-typed by Requirement
                            # (TypedDict total=False with a Literal
                            # union); reuse the most-applicable closed
                            # value rather than coining a new one,
                            # which would break the union narrowing
                            # for backends that switch on it.
                            "kind": "needs_chat_template",
                            "value": False,
                            "rationale": (
                                f"runner '{runner_cls.name}' expects scope "
                                f"'{runner_cls.aggregator_scope}'; aggregator "
                                f"'{aggregator_cls.name}' has scope "
                                f"'{aggregator_cls.scope}'"
                            ),
                        },
                        "message": (
                            f"Aggregator '{aggregator_cls.name}' (scope="
                            f"{aggregator_cls.scope}) is not compatible with runner "
                            f"'{runner_cls.name}' (scope={runner_cls.aggregator_scope})"
                        ),
                        "i18n_key": "parallelEnsemble.errors.scopeMismatch",
                    }
                ]
            )

        issues: list[ValidationIssue] = []
        # Track which sources failed capability so the requirements
        # pass can skip them (avoids double-stacking issues for the
        # same source when both checks would surface a problem).
        capability_failed: set[str] = set()

        # Step 3: capability filter (coarse, per-source). The same alias
        # may appear under two source_ids; we still validate per source
        # because the *next* step checks the source's own effective
        # ``TokenStepParams``, which can differ across sources.
        for ref in cfg.token_sources:
            alias = specs[ref.source_id]["model_alias"]
            spec = self._model_registry.get(alias)
            backend_cls = self._backend_registry.get(spec.backend)
            caps = backend_cls.capabilities(spec)
            missing = runner_cls.required_capabilities - caps
            if missing:
                capability_failed.add(ref.source_id)
                issues.append(
                    {
                        "severity": "error",
                        "requirement": {
                            "kind": "needs_chat_template",
                            "value": False,
                            "rationale": (
                                f"source '{ref.source_id}' (alias={alias}, backend={spec.backend}) "
                                f"declares capabilities {sorted(c.value for c in caps)}; runner "
                                f"'{runner_cls.name}' needs "
                                f"{sorted(c.value for c in missing)}"
                            ),
                        },
                        "message": (
                            f"Source '{ref.source_id}' (alias={alias}, backend={spec.backend}) "
                            f"lacks required capabilities for runner '{runner_cls.name}': "
                            f"{sorted(c.value for c in missing)}"
                        ),
                        "i18n_key": "parallelEnsemble.errors.capabilityMissing",
                    }
                )

        # Step 4: requirements per source — use the source's *effective*
        # ``TokenStepParams`` so per-source ``top_k_override`` actually
        # drives the cap rejection. ``min_top_k`` is overridden in the
        # runner-derived requirement list; other requirements pass
        # through untouched (think ``needs_logprobs=True`` — invariant
        # across sources).
        runner_requirements = runner_cls.requirements(runner_config)
        if runner_requirements:
            for ref in cfg.token_sources:
                if ref.source_id in capability_failed:
                    continue
                alias = specs[ref.source_id]["model_alias"]
                spec = self._model_registry.get(alias)
                backend_cls = self._backend_registry.get(spec.backend)
                source_top_k = effective_params[ref.source_id].top_k
                per_source_reqs: list[Requirement] = []
                for req in runner_requirements:
                    if req.get("kind") == "min_top_k":
                        per_source_reqs.append({**req, "value": source_top_k})
                    else:
                        per_source_reqs.append(req)
                issues.extend(backend_cls.validate_requirements(spec, per_source_reqs))

        # Step 5: cross-field ``validate_selection``. ``model_aliases``
        # carries one entry per source (duplicates allowed — same model
        # twice with different sampling is a legitimate setup).
        source_aliases = [specs[ref.source_id]["model_alias"] for ref in cfg.token_sources]
        issues.extend(runner_cls.validate_selection(runner_config, source_aliases, self._model_registry))

        errors = [issue for issue in issues if issue["severity"] == "error"]
        if errors:
            raise StructuredValidationError(errors)

    def _instantiate_backends(
        self,
        refs: list[TokenSourceRef],
        specs: dict[str, dict[str, Any]],
    ) -> dict[str, ModelBackend]:
        """``source_id`` → fresh-per-run backend mapping.

        Keyed by ``source_id`` (not ``model_alias``) so the same alias
        contributing two sources lands as two distinct backend
        instances — required for the "same model, different sampling"
        self-consistency configuration that motivates ADR-v3-6.
        """
        backends: dict[str, ModelBackend] = {}
        for ref in refs:
            alias = specs[ref.source_id]["model_alias"]
            spec: BaseSpec = self._model_registry.get(alias)
            backend_cls = self._backend_registry.get(spec.backend)
            backends[ref.source_id] = backend_cls(spec, http=self._http_client)
        return backends

    def _instantiate_runner(
        self,
        runner_cls: type[EnsembleRunner],
        aggregator_config: Any,
    ) -> EnsembleRunner:
        """Construct a runner instance.

        The built-in ``token_step`` runner takes
        ``(executor, aggregator_config)``; both come from the node
        because the SPI ``run(...)`` signature has no slot for either
        — see ``token_step`` module docstring for the rationale. A
        third-party runner signs up to the v0.2 SPI by accepting the
        same positional pair, even if it ignores one of the args.
        """
        # The base ``EnsembleRunner`` ABC declares no constructor
        # args — the v0.2 SPI freeze is on public methods, not on
        # construction shape. Built-in / third-party runners are
        # contracted to accept ``(executor, aggregator_config)``
        # positionally; basedpyright cannot see that promise from the
        # abstract declaration alone, hence the targeted ignore.
        return runner_cls(self._executor, aggregator_config)  # pyright: ignore[reportCallIssue]

    def _finalize_outputs(
        self,
        *,
        accumulated: str,
        elapsed_ms: int,
        trace: TraceCollector,
        runner_cls: type[EnsembleRunner],
        aggregator_cls: type[Aggregator],
        runner_config: Any,
        aggregator_config: Any,
        backends: dict[str, ModelBackend],
        specs: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], WorkflowNodeExecutionStatus]:
        """Compose ``outputs`` + ``process_data`` per the storage policy.

        ``outputs.text`` is always the final answer string (downstream
        LLM / End / Answer nodes consume it without rewriting selectors);
        ``tokens_count`` reflects the number of joint tokens the runner
        produced (``token_step``); ``elapsed_ms`` is wall-clock for the
        whole run.

        Trace placement:

        * ``inline`` → ``outputs.trace``. Survives into the variable pool
          so a downstream node can reference it.
        * ``metadata`` → ``process_data["ensemble_trace"]``. Persisted
          for run-history viewers; **not** in the variable pool (keeps
          ``outputs.text`` clean).

        Status: SUCCEEDED unless every selected backend errored — that
        case is detected via the trace summary the runner already
        records, so the node does not need a runner-specific branch.
        """
        # Project each backend instance into the trace's BackendInfo
        # shape — ``id`` carries the source_id so duplicated aliases
        # stay distinguishable; ``backend`` is the registered backend
        # class name (not the alias) so debug viewers can tell
        # llama_cpp from openai_compat at a glance; ``capabilities``
        # surfaces the declared SPI caps so the trace records *what
        # this backend could do* on this run (not just what it was
        # asked to do).
        from .spi.backend import BackendInfo

        backends_info: list[BackendInfo] = [
            BackendInfo(
                id=source_id,
                backend=type(backend).name,
                model_name=backend.model_name,
                capabilities=sorted(c.value for c in backend.instance_capabilities),
                metadata={"model_alias": specs[source_id]["model_alias"]},
            )
            for source_id, backend in backends.items()
        ]
        trace_data = trace.finalize(
            runner_name=runner_cls.name,
            runner_config=runner_config.model_dump(),
            aggregator_name=aggregator_cls.name,
            aggregator_config=aggregator_config.model_dump(),
            backends=backends_info,
        )

        summary = trace_data.get("summary", {})
        tokens_count_raw = summary.get("tokens_count", 0)
        tokens_count = tokens_count_raw if isinstance(tokens_count_raw, int) else 0

        outputs: dict[str, Any] = {
            "text": accumulated,
            "tokens_count": tokens_count,
            "elapsed_ms": elapsed_ms,
        }
        process_data: dict[str, Any] = {}

        cfg_diagnostics = self.node_data.ensemble.diagnostics
        if cfg_diagnostics.storage == "inline":
            outputs["trace"] = trace_data
        else:
            # storage == "metadata" — see module docstring on why
            # ``process_data`` is the landing zone instead of graphon's
            # strict enum-keyed metadata field.
            process_data["ensemble_trace"] = trace_data

        status = self._derive_status(trace_data, backends_count=len(backends))
        return outputs, process_data, status

    @staticmethod
    def _derive_status(
        trace_data: EnsembleTrace,
        *,
        backends_count: int,
    ) -> WorkflowNodeExecutionStatus:
        """SUCCEEDED unless the trace summary says every backend errored.

        ``TokenStepRunner`` records ``stopped_by="all_voters_empty"``
        when the aggregator gave up because every step's voters were
        empty. A third-party runner can opt into the FAILED branch by
        recording ``error_count`` / ``backend_count`` (``error_count
        >= backend_count`` means every contestant raised) — useful for
        judge-style runners that fan out to multiple contestants and
        want to surface a hard failure on the unanimous-error case.
        Re-raising the underlying exception so graphon's base ``run()``
        wraps it as ``NodeRunFailedEvent`` is the simpler alternative.
        """
        summary = trace_data.get("summary", {})
        error_count = summary.get("error_count")
        backend_count = summary.get("backend_count", backends_count)
        if (
            isinstance(error_count, int)
            and isinstance(backend_count, int)
            and backend_count > 0
            and error_count >= backend_count
        ):
            return WorkflowNodeExecutionStatus.FAILED
        if summary.get("stopped_by") == "all_voters_empty":
            return WorkflowNodeExecutionStatus.FAILED
        return WorkflowNodeExecutionStatus.SUCCEEDED

    @classmethod
    def _extract_variable_selector_to_variable_mapping(
        cls,
        *,
        graph_config: Mapping[str, Any],
        node_id: str,
        node_data: ParallelEnsembleNodeData,
    ) -> Mapping[str, Sequence[str]]:
        """Expose every ``TokenSourceRef.spec_selector`` (and dynamic
        ``weight`` selector) to the draft-variable preload path.

        The framework walks this mapping ahead of ``_run`` to materialise
        upstream values; without it the variable pool would be empty when
        ``_collect_specs`` looks up the spec selectors. ``source_id`` is
        unique per node (entities-layer invariant), so
        ``{node_id}.token_sources.{source_id}`` is a stable unique key —
        same shape as ``response_aggregator``'s mapping for symmetry.
        """
        del graph_config
        mapping: dict[str, Sequence[str]] = {}
        for ref in node_data.ensemble.token_sources:
            mapping[f"{node_id}.token_sources.{ref.source_id}"] = list(ref.spec_selector)
            if isinstance(ref.weight, list):
                mapping[f"{node_id}.token_sources.{ref.source_id}.weight"] = list(ref.weight)
        return mapping
