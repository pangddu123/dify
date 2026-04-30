"""``ParallelEnsembleNode`` — graphon node binding for the v0.2 SPI (P2.8).

Translates between graphon's event protocol and the SPI surface that
``runners/`` and ``aggregators/`` already implement: the node owns the
five "framework" responsibilities (variable pool I/O, alias→backend
resolution, §9 startup validation, trace storage, ``StreamCompleted``
emission); the runner owns the algorithm; the aggregator owns the
signal reduction. None of those layers reach into graphon directly —
keeping the SPI testable without spinning up a graph runtime is the
explicit goal of EXTENSIBILITY_SPEC §1.1 ("runner / aggregator stay
graphon-decoupled").

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
3. **Capability filter** (coarse, per-alias). Cheap, no IO. Issues
   short-circuit the next pass.
4. **Requirements** (precision, per-alias × per-requirement). Calls
   the backend class's ``validate_requirements`` — still no instances
   created.
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
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, ClassVar

from graphon.enums import NodeType, WorkflowNodeExecutionStatus
from graphon.node_events.base import NodeEventBase, NodeRunResult
from graphon.node_events.node import StreamChunkEvent, StreamCompletedEvent
from graphon.nodes.base.node import Node

from . import PARALLEL_ENSEMBLE_NODE_TYPE
from .entities import ParallelEnsembleConfig, ParallelEnsembleNodeData
from .exceptions import StructuredValidationError
from .spi.aggregator import Aggregator
from .spi.backend import BaseSpec, ModelBackend
from .spi.requirements import ValidationIssue
from .spi.runner import EnsembleRunner
from .spi.trace import EnsembleTrace, TraceCollector

if TYPE_CHECKING:
    from .registry.aggregator_registry import AggregatorRegistry
    from .registry.backend_registry import BackendRegistry
    from .registry.model_registry import ModelRegistry
    from .registry.runner_registry import RunnerRegistry

logger = logging.getLogger(__name__)


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

        question = self._read_question(cfg)

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
        )

        backends = self._instantiate_backends(cfg.model_aliases)
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
        for event in runner.run(question, backends, aggregator, runner_config, trace):
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
            cfg=cfg,
        )

        yield StreamCompletedEvent(
            node_run_result=NodeRunResult(
                status=status,
                inputs={
                    "question": question,
                    "models": list(backends.keys()),
                    "runner": cfg.runner_name,
                    "aggregator": cfg.aggregator_name,
                },
                process_data=process_data,
                outputs=outputs,
                error=("" if status == WorkflowNodeExecutionStatus.SUCCEEDED else "all backends failed"),
            )
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _read_question(self, cfg: ParallelEnsembleConfig) -> str:
        """Resolve ``question_variable`` against the variable pool.

        Returns ``""`` when the pool has no entry — a missing question
        is *not* a fatal error, so a chat-mode node receiving an empty
        first message still produces a sensible empty-answer trace
        instead of crashing the workflow. The runner's own logic
        (``majority_vote`` filtering, token-step max-len cap) handles
        the empty input gracefully.
        """
        segment = self.graph_runtime_state.variable_pool.get(cfg.question_variable)
        if segment is None:
            return ""
        # Use ``segment.text`` rather than ``str(segment.value)`` for
        # consistency with how every other graphon node renders
        # variable-pool values (matches ensemble_aggregator/node.py:82).
        return segment.text

    def _validate_at_startup(
        self,
        *,
        runner_cls: type[EnsembleRunner],
        aggregator_cls: type[Aggregator],
        runner_config: Any,
        cfg: ParallelEnsembleConfig,
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
        # Track which aliases failed capability so the requirements
        # pass can skip them (avoids double-stacking issues for the
        # same alias when both checks would surface a problem).
        capability_failed: set[str] = set()

        # Step 3: capability filter (coarse, per-alias).
        for alias in cfg.model_aliases:
            spec = self._model_registry.get(alias)
            backend_cls = self._backend_registry.get(spec.backend)
            caps = backend_cls.capabilities(spec)
            missing = runner_cls.required_capabilities - caps
            if missing:
                capability_failed.add(alias)
                issues.append(
                    {
                        "severity": "error",
                        "requirement": {
                            "kind": "needs_chat_template",
                            "value": False,
                            "rationale": (
                                f"alias '{alias}' (backend={spec.backend}) declares "
                                f"capabilities {sorted(c.value for c in caps)}; runner "
                                f"'{runner_cls.name}' needs "
                                f"{sorted(c.value for c in missing)}"
                            ),
                        },
                        "message": (
                            f"Model '{alias}' (backend={spec.backend}) lacks required "
                            f"capabilities for runner '{runner_cls.name}': "
                            f"{sorted(c.value for c in missing)}"
                        ),
                        "i18n_key": "parallelEnsemble.errors.capabilityMissing",
                    }
                )

        # Step 4: requirements (precision, per-alias × per-requirement).
        requirements = runner_cls.requirements(runner_config)
        if requirements:
            for alias in cfg.model_aliases:
                if alias in capability_failed:
                    continue
                spec = self._model_registry.get(alias)
                backend_cls = self._backend_registry.get(spec.backend)
                issues.extend(backend_cls.validate_requirements(spec, requirements))

        # Step 5: cross-field ``validate_selection`` (e.g. judge_alias
        # must be in model_aliases, token_step needs ≥ 2 contestants).
        issues.extend(runner_cls.validate_selection(runner_config, list(cfg.model_aliases), self._model_registry))

        errors = [issue for issue in issues if issue["severity"] == "error"]
        if errors:
            raise StructuredValidationError(errors)

    def _instantiate_backends(self, aliases: list[str]) -> dict[str, ModelBackend]:
        """alias → fresh-per-run backend mapping."""
        backends: dict[str, ModelBackend] = {}
        for alias in aliases:
            spec: BaseSpec = self._model_registry.get(alias)
            backend_cls = self._backend_registry.get(spec.backend)
            backends[alias] = backend_cls(spec, http=self._http_client)
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
        cfg: ParallelEnsembleConfig,
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
        backends_info = self._model_registry.list_aliases()
        chosen = [info for info in backends_info if info["id"] in backends]
        trace_data = trace.finalize(
            runner_name=runner_cls.name,
            runner_config=runner_config.model_dump(),
            aggregator_name=aggregator_cls.name,
            aggregator_config=aggregator_config.model_dump(),
            backends=chosen,
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

        if cfg.diagnostics.storage == "inline":
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
