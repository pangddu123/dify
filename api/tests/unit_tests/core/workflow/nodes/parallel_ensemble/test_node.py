"""``ParallelEnsembleNode`` — node-level event sequence + §9 startup
validation + storage policy + DSL smuggle defence.

Where the seams sit
-------------------

The node is a thin coordinator: variable-pool I/O, registry lookup, §9
validation, runner ↔ aggregator instantiation, trace storage, event
translation. Runner / aggregator / backend behaviour is covered
exhaustively in their own ``runners/`` / ``aggregators/`` /
``test_llama_cpp_backend.py`` files — these tests stay laser-focused on
what *only* the node owns:

* the ``StreamChunkEvent`` × N + closing ``StreamChunkEvent(is_final=True)``
  + ``StreamCompletedEvent`` event contract (``token_step`` runner doesn't
  flush its accumulator without the closing chunk — see node module
  docstring, matches the agent-node pattern);
* §9 startup validation pipeline ordering — scope alignment, capability
  filter, ``backend.validate_requirements`` , ``runner.validate_selection``
  — every layer wrapped into one ``StructuredValidationError`` so the
  panel renders all offences at once;
* the storage split: ``"inline"`` lands ``trace`` in the variable pool
  (downstream selectable), ``"metadata"`` lands ``ensemble_trace`` in
  ``process_data`` (run-history viewable, variable pool stays clean —
  see node module docstring on why ``process_data`` not graphon's
  enum-keyed ``metadata`` field);
* DSL smuggle defence on both the outer ``BaseNodeData(extra="allow")``
  layer (top-level ``model_url`` rejected by ``mode="before"`` validator)
  and the inner ``runner_config`` layer (rejected at runtime by the
  runner's ``extra="forbid"`` ``config_class``).

Why ``_make_node`` bypasses ``Node.__init__``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Node.__init__`` requires a real ``GraphInitParams`` / ``GraphRuntimeState``
which would pull a graph runtime, a workflow id, a run-context mapping,
etc. — too much surface area for unit tests of one node. Mirroring
``ensemble_aggregator/test_node.py``, we ``__new__`` the instance and
inject only the four attributes ``_run`` actually reads
(``_node_id`` / ``graph_runtime_state`` / ``_node_data`` + the SPI
keyword args the factory wires).

Synthetic runners / backends rather than the built-ins
------------------------------------------------------

P3.B.0 retired the in-package response-mode runner / aggregators
(ADR-v3-9), so node-level tests now exercise storage / failure /
diagnostics paths through tiny synthetic runners that record
``error_count`` / ``backend_count`` / token traces directly into the
``TraceCollector`` — every code path the node owns is reachable that
way.

P3.B.3 fixture shape
--------------------

Tests seed the variable pool with one ``ModelInvocationSpec`` dict per
source (mirroring what an upstream ``token-model-source`` node would
have produced) and reference each spec via ``token_sources[i].spec_selector``.
The synthetic runners ignore ``sources`` content and just yield their
scripted events — node-level concerns (event translation, status
derivation, storage) don't depend on what's inside ``SourceInput``.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.workflow.nodes.parallel_ensemble import PARALLEL_ENSEMBLE_NODE_TYPE
from core.workflow.nodes.parallel_ensemble.aggregators.token.sum_score import (
    SumScoreAggregator,
)
from core.workflow.nodes.parallel_ensemble.entities import (
    ParallelEnsembleConfig,
    ParallelEnsembleNodeData,
)
from core.workflow.nodes.parallel_ensemble.exceptions import (
    InvalidSpecError,
    MissingSpecError,
    StructuredValidationError,
    UnknownModelAliasError,
    WeightResolutionError,
)
from core.workflow.nodes.parallel_ensemble.node import ParallelEnsembleNode
from core.workflow.nodes.parallel_ensemble.runners.token_step import (
    TokenStepConfig,
)
from core.workflow.nodes.parallel_ensemble.spi.aggregator import (
    Aggregator,
    BackendAggregationContext,
    ResponseAggregator,
    ResponseSignal,
    SourceAggregationContext,
    TokenAggregator,
    TokenPick,
    TokenSignals,
)
from core.workflow.nodes.parallel_ensemble.spi.backend import (
    BackendInfo,
    BaseSpec,
    GenerationParams,
    GenerationResult,
    ModelBackend,
)
from core.workflow.nodes.parallel_ensemble.spi.capability import Capability
from core.workflow.nodes.parallel_ensemble.spi.requirements import (
    Requirement,
    ValidationIssue,
)
from core.workflow.nodes.parallel_ensemble.spi.runner import (
    DoneEvent,
    EnsembleRunner,
    RunnerEvent,
    SourceInput,
    TokenEvent,
)
from core.workflow.nodes.parallel_ensemble.spi.trace import (
    TraceCollector,
)
from graphon.enums import WorkflowNodeExecutionStatus
from graphon.node_events.node import StreamChunkEvent, StreamCompletedEvent
from graphon.runtime.variable_pool import VariablePool

# ── Synthetic specs / backends / runners ─────────────────────────────────


class _SyntheticSpec(BaseSpec):
    """Minimal ``BaseSpec`` subclass — backend tag is open-string so a test
    can route different aliases at the same registry to different backend
    classes without standing up a Literal-tagged subclass per scenario."""

    model_config = ConfigDict(extra="allow", frozen=True)

    type: str = "normal"


class _TokenStepBackend(ModelBackend):
    """Declares the full PN.py capability set (TOKEN_STEP + TOP_PROBS)."""

    name = "token_step_backend"
    spec_class: ClassVar[type[BaseSpec]] = _SyntheticSpec

    @classmethod
    def capabilities(cls, spec: BaseSpec) -> frozenset[Capability]:
        del spec
        return frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS})

    @classmethod
    def validate_requirements(cls, spec: BaseSpec, requirements: list[Requirement]) -> list[ValidationIssue]:
        del spec, requirements
        return []

    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult:
        del prompt, params
        return GenerationResult(text="ok", finish_reason="stop", metadata={})


class _ResponseOnlyBackend(ModelBackend):
    """No required caps — pairs with response-scope synthetic runners."""

    name = "response_only_backend"
    spec_class: ClassVar[type[BaseSpec]] = _SyntheticSpec

    def __init__(
        self,
        spec: BaseSpec,
        http: object,
        *,
        scripted_text: str = "ok",
        scripted_exc: Exception | None = None,
    ) -> None:
        super().__init__(spec=spec, http=http)
        self._scripted_text = scripted_text
        self._scripted_exc = scripted_exc

    @classmethod
    def capabilities(cls, spec: BaseSpec) -> frozenset[Capability]:
        del spec
        return frozenset()

    @classmethod
    def validate_requirements(cls, spec: BaseSpec, requirements: list[Requirement]) -> list[ValidationIssue]:
        del spec, requirements
        return []

    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult:
        del prompt, params
        if self._scripted_exc is not None:
            raise self._scripted_exc
        return GenerationResult(text=self._scripted_text, finish_reason="stop", metadata={})


class _StreamingOnlyBackend(ModelBackend):
    """Declares ``STREAMING`` only — used to drive the §9 capability-miss
    branch for token-step runners that need ``TOKEN_STEP + TOP_PROBS``."""

    name = "streaming_only_backend"
    spec_class: ClassVar[type[BaseSpec]] = _SyntheticSpec

    @classmethod
    def capabilities(cls, spec: BaseSpec) -> frozenset[Capability]:
        del spec
        return frozenset({Capability.STREAMING})

    @classmethod
    def validate_requirements(cls, spec: BaseSpec, requirements: list[Requirement]) -> list[ValidationIssue]:
        del spec, requirements
        return []

    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult:
        del prompt, params
        return GenerationResult(text="", finish_reason="stop", metadata={})


class _OpenAIStyleBackend(ModelBackend):
    """Backend that mirrors the OpenAI ``top_logprobs <= 20`` cap.

    OpenAI's chat-completions API caps ``top_logprobs`` at 20; a runner
    that wants 25 candidates per step should be rejected by the §9
    requirements pass before any HTTP call. v0.2 ships only the
    ``llama_cpp`` backend (which has no such cap), so the test
    synthesises the cap here to pin the runner ↔ backend wiring without
    waiting for the openai_compat backend to land.
    """

    name = "openai_style_backend"
    spec_class: ClassVar[type[BaseSpec]] = _SyntheticSpec

    @classmethod
    def capabilities(cls, spec: BaseSpec) -> frozenset[Capability]:
        del spec
        return frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS})

    @classmethod
    def validate_requirements(cls, spec: BaseSpec, requirements: list[Requirement]) -> list[ValidationIssue]:
        del spec
        issues: list[ValidationIssue] = []
        for req in requirements:
            if req.get("kind") == "min_top_k":
                value = req.get("value")
                if isinstance(value, int) and value > 20:
                    issues.append(
                        {
                            "severity": "error",
                            "requirement": req,
                            "message": (f"top_logprobs is capped at 20, runner requested {value}"),
                            "i18n_key": "parallelEnsemble.errors.topKExceeded",
                        }
                    )
        return issues

    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult:
        del prompt, params
        return GenerationResult(text="", finish_reason="stop", metadata={})


# ── Synthetic runners ────────────────────────────────────────────────────


class _ScriptedConfig(BaseModel):
    """Empty schema — the scripted runner has no tunables."""

    model_config = ConfigDict(extra="forbid")


class _ScriptedRunner(EnsembleRunner[_ScriptedConfig]):
    """Yields a pre-recorded event sequence; no fan-out, no aggregator use.

    Lets event-sequence tests assert the node's translation contract
    (``token`` → ``StreamChunkEvent(is_final=False)``; closing
    ``StreamChunkEvent(is_final=True)``; final ``StreamCompletedEvent``)
    without having to script per-token candidate lists for a real runner.
    The class-level ``scripted_events`` is set per-test before the runner
    is instantiated by the node.
    """

    name = "_scripted"
    config_class: ClassVar[type[BaseModel]] = _ScriptedConfig
    aggregator_scope: ClassVar[str] = "response"
    required_capabilities: ClassVar[frozenset[Capability]] = frozenset()
    i18n_key_prefix: ClassVar[str] = "test.scripted"
    ui_schema: ClassVar[dict] = {}

    scripted_events: ClassVar[list[RunnerEvent]] = []

    def __init__(self, executor: ThreadPoolExecutor, aggregator_config: BaseModel) -> None:
        del executor, aggregator_config

    @classmethod
    def requirements(cls, config: _ScriptedConfig) -> list[Requirement]:
        del config
        return []

    def run(
        self,
        sources: dict[str, SourceInput],
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: _ScriptedConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        del sources, backends, aggregator, config, trace
        yield from type(self).scripted_events


class _BigTopKConfig(BaseModel):
    """Synthetic runner config with no top_k cap.

    Post-ADR-v3-16 ``TokenStepConfig`` no longer carries ``top_k`` —
    per-source ``TokenStepParams`` own it. To exercise the §9
    requirements-rejection branch in isolation we still need a
    runner-config schema that emits a ``min_top_k`` requirement;
    ``_BigTopKConfig`` keeps the legacy shape so the test reaches the
    cap-rejection path through both code paths.
    """

    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(default=25, gt=0)


class _BigTopKRunner(EnsembleRunner[_BigTopKConfig]):
    """Emits ``min_top_k=25`` so the OpenAI-style cap rejection fires."""

    name = "_big_top_k"
    config_class: ClassVar[type[BaseModel]] = _BigTopKConfig
    aggregator_scope: ClassVar[str] = "token"
    required_capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS})
    i18n_key_prefix: ClassVar[str] = "test.bigTopK"
    ui_schema: ClassVar[dict] = {"top_k": {"control": "number_input"}}

    def __init__(self, executor: ThreadPoolExecutor, aggregator_config: BaseModel) -> None:
        del executor, aggregator_config

    @classmethod
    def requirements(cls, config: _BigTopKConfig) -> list[Requirement]:
        return [{"kind": "min_top_k", "value": config.top_k, "rationale": "test"}]

    def run(
        self,
        sources: dict[str, SourceInput],
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: _BigTopKConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        del sources, backends, aggregator, config, trace
        yield DoneEvent(kind="done", text="", metadata={})


class _RejectingRunner(EnsembleRunner[_ScriptedConfig]):
    """``validate_selection`` always returns one ``error`` issue.

    Pins the §9 step-5 propagation: an issue from the runner's own
    cross-field hook lands in ``StructuredValidationError`` exactly the
    same way capability / requirements issues do.
    """

    name = "_rejecting"
    config_class: ClassVar[type[BaseModel]] = _ScriptedConfig
    aggregator_scope: ClassVar[str] = "response"
    required_capabilities: ClassVar[frozenset[Capability]] = frozenset()
    i18n_key_prefix: ClassVar[str] = "test.rejecting"
    ui_schema: ClassVar[dict] = {}

    def __init__(self, executor: ThreadPoolExecutor, aggregator_config: BaseModel) -> None:
        del executor, aggregator_config

    @classmethod
    def requirements(cls, config: _ScriptedConfig) -> list[Requirement]:
        del config
        return []

    @classmethod
    def validate_selection(
        cls,
        config: _ScriptedConfig,
        model_aliases: list[str],
        registry: Any,
    ) -> list[ValidationIssue]:
        del config, registry
        return [
            {
                "severity": "error",
                "requirement": {
                    "kind": "model_allowlist",
                    "value": list(model_aliases),
                    "rationale": "test rejection",
                },
                "message": "rejected by validate_selection for testing",
                "i18n_key": "test.rejected",
            }
        ]

    def run(
        self,
        sources: dict[str, SourceInput],
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: _ScriptedConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        del sources, backends, aggregator, config, trace
        yield DoneEvent(kind="done", text="", metadata={})


class _NoSignalConfig(BaseModel):
    """Empty config — paired aggregators have no tunables."""

    model_config = ConfigDict(extra="forbid")


class _NoSignalAggregator(ResponseAggregator[_NoSignalConfig]):
    """Stand-in response aggregator that ignores signals; used to pair
    with synthetic runners without dragging in real strategy semantics."""

    name = "_no_signal"
    config_class: ClassVar[type[BaseModel]] = _NoSignalConfig
    i18n_key_prefix: ClassVar[str] = "test.noSignal"
    ui_schema: ClassVar[dict] = {}

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: SourceAggregationContext,
        config: _NoSignalConfig,
    ) -> dict:
        del signals, context, config
        return {"text": "", "metadata": {}}


class _NoSignalTokenAggregator(TokenAggregator[_NoSignalConfig]):
    """Stand-in token aggregator paired with ``_BigTopKRunner``."""

    name = "_no_signal_token"
    config_class: ClassVar[type[BaseModel]] = _NoSignalConfig
    i18n_key_prefix: ClassVar[str] = "test.noSignalToken"
    ui_schema: ClassVar[dict] = {}

    def aggregate(
        self,
        signals: TokenSignals,
        context: BackendAggregationContext,
        config: _NoSignalConfig,
    ) -> TokenPick:
        del signals, context, config
        return {"token": "", "score": 0.0, "reasoning": {}}


# ── Fake registries ──────────────────────────────────────────────────────


class _FakeModelRegistry:
    """Minimal stand-in: ``get`` returns a ``BaseSpec``; ``list_aliases``
    returns one ``BackendInfo`` per alias for ``_finalize_outputs``."""

    def __init__(self, alias_to_spec: dict[str, BaseSpec]) -> None:
        self._specs = alias_to_spec

    def get(self, alias: str) -> BaseSpec:
        try:
            return self._specs[alias]
        except KeyError as exc:
            raise UnknownModelAliasError(alias) from exc

    def list_aliases(self) -> list[BackendInfo]:
        out: list[BackendInfo] = []
        for alias, spec in self._specs.items():
            out.append(
                BackendInfo(
                    id=spec.id,
                    backend=spec.backend,
                    model_name=spec.model_name,
                    capabilities=[],
                    metadata={},
                )
            )
            del alias
        return out

    def __contains__(self, alias: object) -> bool:
        return isinstance(alias, str) and alias in self._specs


class _FakeRunnerRegistry:
    def __init__(self, runners: dict[str, type[EnsembleRunner]]) -> None:
        self._runners = runners

    def get(self, name: str) -> type[EnsembleRunner]:
        return self._runners[name]


class _FakeAggregatorRegistry:
    def __init__(self, aggs: dict[str, type[Aggregator]]) -> None:
        self._aggs = aggs

    def get(self, name: str) -> type[Aggregator]:
        return self._aggs[name]


class _FakeBackendRegistry:
    def __init__(self, backends: dict[str, type[ModelBackend]]) -> None:
        self._backends = backends

    def get(self, name: str) -> type[ModelBackend]:
        return self._backends[name]


# ── Node factory helper ──────────────────────────────────────────────────


def _make_spec_dict(
    *,
    model_alias: str,
    prompt: str = "hi",
    sampling_params: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ``ModelInvocationSpec``-shaped dict for the variable pool.

    Mirrors what an upstream ``token-model-source`` node would have
    yielded into ``outputs.spec``. ``sampling_params`` defaults to the
    PN.py-friendly ``top_k=5`` so the synthetic ``_BigTopKRunner`` /
    requirements path lands on a value the §9 pipeline can compare
    against the runner's own ``min_top_k``.
    """
    return {
        "model_alias": model_alias,
        "prompt": prompt,
        "sampling_params": dict(sampling_params or {"top_k": 5}),
        "extra": dict(extra or {}),
    }


def _build_pool(
    aliases: list[str],
    *,
    sampling_params: dict[str, Any] | None = None,
) -> tuple[VariablePool, list[list[str]]]:
    """Seed one ``ModelInvocationSpec`` per alias, return pool + selectors.

    Each spec lives at ``["src_<i>", "spec"]`` so the test can wire
    matching ``token_sources[i].spec_selector`` entries with the same
    shape an upstream ``token-model-source`` node would produce.
    """
    pool = VariablePool()
    selectors: list[list[str]] = []
    for i, alias in enumerate(aliases):
        node_id = f"src_{i}"
        pool.add([node_id, "spec"], _make_spec_dict(model_alias=alias, sampling_params=sampling_params))
        selectors.append([node_id, "spec"])
    return pool, selectors


def _make_node(
    *,
    runner_name: str = "_scripted",
    aggregator_name: str = "_no_signal",
    model_aliases: list[str] | None = None,
    runner_config: dict[str, object] | None = None,
    aggregator_config: dict[str, object] | None = None,
    diagnostics: dict[str, object] | None = None,
    runners: dict[str, type[EnsembleRunner]] | None = None,
    aggregators: dict[str, type[Aggregator]] | None = None,
    backends: dict[str, type[ModelBackend]] | None = None,
    specs: dict[str, BaseSpec] | None = None,
    pool: VariablePool | None = None,
    selectors: list[list[str]] | None = None,
    sampling_params: dict[str, Any] | None = None,
    token_source_overrides: list[dict[str, Any]] | None = None,
    extra_node_data: dict[str, object] | None = None,
) -> ParallelEnsembleNode:
    """Bypass ``Node.__init__`` and inject just the pieces ``_run`` reads.

    Mirrors ``ensemble_aggregator/test_node.py::_make_node`` so the two
    suites read symmetrically. The caller supplies registries / specs;
    sensible defaults paint a 2-source scripted-runner setup that most
    tests can override one field of.
    """
    aliases = model_aliases or ["m1", "m2"]
    specs = specs or {alias: _SyntheticSpec(id=alias, backend="synthetic", model_name=alias) for alias in aliases}
    runners = runners or {"_scripted": _ScriptedRunner}
    aggregators = aggregators or {"_no_signal": _NoSignalAggregator}
    backends = backends or {"synthetic": _ResponseOnlyBackend}

    if pool is None:
        pool, default_selectors = _build_pool(aliases, sampling_params=sampling_params)
        if selectors is None:
            selectors = default_selectors
    if selectors is None:
        selectors = [[f"src_{i}", "spec"] for i in range(len(aliases))]

    token_sources: list[dict[str, Any]] = []
    for i, sel in enumerate(selectors):
        ref: dict[str, Any] = {
            "source_id": f"s{i}",
            "spec_selector": sel,
        }
        if token_source_overrides and i < len(token_source_overrides):
            ref.update(token_source_overrides[i])
        token_sources.append(ref)

    payload: dict[str, Any] = {
        "type": PARALLEL_ENSEMBLE_NODE_TYPE,
        "title": "pe",
        "ensemble": {
            "token_sources": token_sources,
            "runner_name": runner_name,
            "runner_config": runner_config or {},
            "aggregator_name": aggregator_name,
            "aggregator_config": aggregator_config or {},
            "diagnostics": diagnostics or {"storage": "metadata"},
        },
    }
    if extra_node_data:
        payload.update(extra_node_data)

    node = ParallelEnsembleNode.__new__(ParallelEnsembleNode)
    node._node_id = "pe_1"
    node._model_registry = _FakeModelRegistry(specs)  # type: ignore[assignment]
    node._runner_registry = _FakeRunnerRegistry(runners)  # type: ignore[assignment]
    node._aggregator_registry = _FakeAggregatorRegistry(aggregators)  # type: ignore[assignment]
    node._backend_registry = _FakeBackendRegistry(backends)  # type: ignore[assignment]
    node._executor = ThreadPoolExecutor(max_workers=2)  # type: ignore[assignment]
    node._http_client = None  # type: ignore[assignment]

    class _RS:
        pass

    rs = _RS()
    rs.variable_pool = pool
    node.graph_runtime_state = rs  # type: ignore[assignment]
    node._node_data = ParallelEnsembleNodeData.model_validate(payload)
    return node


# ── §9.1 Event sequence + final outputs ──────────────────────────────────


class TestEventSequence:
    """``_run`` translates runner events into the streaming-protocol
    event triple the downstream Answer node expects: N×StreamChunk(non-final)
    then 1×StreamChunk(empty + is_final=True) then 1×StreamCompleted."""

    def test_event_sequence_streaming(self):
        """3 token deltas + done → 3 non-final chunks, 1 closing chunk, 1 completed."""
        _ScriptedRunner.scripted_events = [
            TokenEvent(kind="token", delta="hel"),
            TokenEvent(kind="token", delta="lo "),
            TokenEvent(kind="token", delta="world"),
            DoneEvent(kind="done", text="hello world", metadata={}),
        ]
        node = _make_node()
        events = list(node._run())

        chunks = events[:-1]
        assert len(chunks) == 4
        assert all(isinstance(e, StreamChunkEvent) for e in chunks)
        assert all(e.selector == ["pe_1", "text"] for e in chunks)
        # First three carry the delta; last is the empty closing chunk.
        assert [e.chunk for e in chunks[:3]] == ["hel", "lo ", "world"]
        assert [e.is_final for e in chunks[:3]] == [False, False, False]
        assert chunks[3].chunk == ""
        assert chunks[3].is_final is True

        completed = events[-1]
        assert isinstance(completed, StreamCompletedEvent)
        assert completed.node_run_result.outputs["text"] == "hello world"

    def test_completed_outputs(self):
        """``outputs.text`` mirrors accumulated tokens; ``elapsed_ms`` is
        a non-negative int. ``tokens_count`` defaults to 0 here because
        the scripted runner does not call ``trace.record_summary("tokens_count", ...)``
        — that field is the runner's contract, see token_step runner."""
        _ScriptedRunner.scripted_events = [
            TokenEvent(kind="token", delta="a"),
            TokenEvent(kind="token", delta="b"),
            TokenEvent(kind="token", delta="c"),
            DoneEvent(kind="done", text="abc", metadata={}),
        ]
        node = _make_node()
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        assert nrr.outputs["text"] == "abc"
        assert nrr.outputs["tokens_count"] == 0
        assert isinstance(nrr.outputs["elapsed_ms"], int)
        assert nrr.outputs["elapsed_ms"] >= 0
        # Post-ADR-v3-16 inputs view: ``sources`` lists source_ids,
        # ``models`` lists the resolved aliases per source.
        assert nrr.inputs["sources"] == ["s0", "s1"]
        assert nrr.inputs["models"] == ["m1", "m2"]


# ── §9 startup validation ────────────────────────────────────────────────


class TestStartupValidation:
    """The five-step §9 pipeline aggregates every error-severity issue
    into one ``StructuredValidationError`` so the panel can render them
    all in one pass — these tests pin each step in isolation."""

    def test_scope_mismatch_raises(self):
        """§9 step 1: response runner + token aggregator → reject before
        capability / requirements / cross-field even run."""
        node = _make_node(
            runner_name="_scripted",
            aggregator_name="sum_score",  # token-scope, paired with response runner
            aggregators={"sum_score": SumScoreAggregator},
        )
        _ScriptedRunner.scripted_events = []
        with pytest.raises(StructuredValidationError) as exc:
            list(node._run())
        assert exc.value.issues[0]["i18n_key"] == "parallelEnsemble.errors.scopeMismatch"

    def test_capability_mismatch_raises(self):
        """§9 step 3: runner needs ``TOKEN_STEP`` but backend declares
        ``STREAMING`` only → ``StructuredValidationError`` at startup."""
        node = _make_node(
            runner_name="_big_top_k",
            runner_config={"top_k": 5},
            aggregator_name="_no_signal_token",
            backends={"synthetic": _StreamingOnlyBackend},
            runners={"_big_top_k": _BigTopKRunner},
            aggregators={"_no_signal_token": _NoSignalTokenAggregator},
        )
        with pytest.raises(StructuredValidationError) as exc:
            list(node._run())
        assert exc.value.issues[0]["i18n_key"] == "parallelEnsemble.errors.capabilityMissing"

    def test_requirements_mismatch_raises(self):
        """§9 step 4: ``min_top_k=25`` against an OpenAI-style backend
        capped at 20 → structured error with the cap message."""
        node = _make_node(
            runner_name="_big_top_k",
            runner_config={"top_k": 25},
            aggregator_name="_no_signal_token",
            backends={"synthetic": _OpenAIStyleBackend},
            runners={"_big_top_k": _BigTopKRunner},
            aggregators={"_no_signal_token": _NoSignalTokenAggregator},
            sampling_params={"top_k": 25},
        )
        with pytest.raises(StructuredValidationError) as exc:
            list(node._run())
        # Two sources both reject → both issues land in the structured error.
        messages = [issue["message"] for issue in exc.value.issues]
        assert all("top_logprobs is capped at 20" in m for m in messages)
        assert all("requested 25" in m for m in messages)

    def test_validate_selection_propagates(self):
        """§9 step 5: a runner whose ``validate_selection`` returns an
        error issue surfaces verbatim in ``StructuredValidationError``."""
        node = _make_node(
            runner_name="_rejecting",
            runners={"_rejecting": _RejectingRunner},
        )
        with pytest.raises(StructuredValidationError) as exc:
            list(node._run())
        issue = exc.value.issues[0]
        assert issue["i18n_key"] == "test.rejected"
        assert issue["message"] == "rejected by validate_selection for testing"

    def test_top_k_override_drives_per_source_requirement(self):
        """ADR-v3-6 + P3.B.3: a single source's ``top_k_override`` drives
        the §9 requirements pass for *that* source — sibling sources at
        the spec's default top_k stay un-rejected. Pins that the
        per-source effective top_k actually flows into
        ``backend.validate_requirements`` rather than the shared
        runner-config value."""
        node = _make_node(
            runner_name="_big_top_k",
            runner_config={"top_k": 5},
            aggregator_name="_no_signal_token",
            backends={"synthetic": _OpenAIStyleBackend},
            runners={"_big_top_k": _BigTopKRunner},
            aggregators={"_no_signal_token": _NoSignalTokenAggregator},
            sampling_params={"top_k": 5},
            token_source_overrides=[{"top_k_override": 25}, {}],
        )
        with pytest.raises(StructuredValidationError) as exc:
            list(node._run())
        messages = [issue["message"] for issue in exc.value.issues]
        # Only the overridden source trips the cap; sibling stays clean.
        assert len(messages) == 1
        assert "requested 25" in messages[0]


# ── Per-backend timeout / failure handling ───────────────────────────────


class _SummaryRecordingRunner(EnsembleRunner[_ScriptedConfig]):
    """Runner that records ``error_count`` + ``backend_count`` into the
    trace summary so the node's ``_derive_status`` SUCCEEDED-vs-FAILED
    branch is reachable without instantiating a real fan-out runner.

    Class-level ``error_count`` / ``backend_count`` are set per-test
    before the node instantiates the runner — mirrors the
    ``_ScriptedRunner.scripted_events`` pattern above.
    """

    name = "_summary_recording"
    config_class: ClassVar[type[BaseModel]] = _ScriptedConfig
    aggregator_scope: ClassVar[str] = "response"
    required_capabilities: ClassVar[frozenset[Capability]] = frozenset()
    i18n_key_prefix: ClassVar[str] = "test.summaryRecording"
    ui_schema: ClassVar[dict] = {}

    error_count: ClassVar[int] = 0
    backend_count: ClassVar[int] = 0
    yield_text: ClassVar[str] = ""
    per_alias_error: ClassVar[dict[str, str]] = {}

    def __init__(self, executor: ThreadPoolExecutor, aggregator_config: BaseModel) -> None:
        del executor, aggregator_config

    @classmethod
    def requirements(cls, config: _ScriptedConfig) -> list[Requirement]:
        del config
        return []

    def run(
        self,
        sources: dict[str, SourceInput],
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: _ScriptedConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        del sources, backends, aggregator, config
        cls = type(self)
        # Surface per-alias errors via record_response so the trace's
        # response_trace section covers the surfaces the node-level
        # storage tests touch (mirrors a real runner's contract).
        for alias, error in cls.per_alias_error.items():
            trace.record_response(
                {
                    "source_id": alias,
                    "text": None,
                    "finish_reason": "error",
                    "tokens_count": 0,
                    "elapsed_ms": 0,
                    "error": error,
                }
            )
        trace.record_summary("error_count", cls.error_count)
        trace.record_summary("backend_count", cls.backend_count)
        yield DoneEvent(kind="done", text=cls.yield_text, metadata={})


class TestBackendFailures:
    """``_SummaryRecordingRunner`` exercises the node's
    SUCCEEDED-vs-FAILED status derivation without depending on a real
    fan-out runner: a single backend timeout is absorbed (status
    SUCCEEDED, error logged in trace), every-backend-failed degrades to
    FAILED via the trace summary's ``error_count`` / ``backend_count``
    invariant the node uses in ``_derive_status``.
    """

    def test_single_model_timeout(self):
        """One backend "errored" in the trace → status SUCCEEDED;
        ``error_count`` < ``backend_count`` so the FAILED branch stays
        unreached."""
        _SummaryRecordingRunner.error_count = 1
        _SummaryRecordingRunner.backend_count = 2
        _SummaryRecordingRunner.yield_text = "ok"
        _SummaryRecordingRunner.per_alias_error = {"s1": "TimeoutError: timed out"}
        node = _make_node(
            runner_name="_summary_recording",
            runners={"_summary_recording": _SummaryRecordingRunner},
            diagnostics={"storage": "metadata", "include_per_backend_errors": True},
        )
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        assert nrr.outputs["text"] == "ok"
        trace = nrr.process_data["ensemble_trace"]
        assert trace["summary"]["error_count"] == 1
        assert trace["summary"]["backend_count"] == 2
        by_alias = {row["source_id"]: row for row in trace["response_trace"]}
        assert "TimeoutError" in by_alias["s1"]["error"]

    def test_all_timeout(self):
        """Every backend "errored" → FAILED branch fires
        (``error_count == backend_count``)."""
        _SummaryRecordingRunner.error_count = 2
        _SummaryRecordingRunner.backend_count = 2
        _SummaryRecordingRunner.yield_text = ""
        _SummaryRecordingRunner.per_alias_error = {
            "s0": "TimeoutError: timed out",
            "s1": "TimeoutError: timed out",
        }
        node = _make_node(
            runner_name="_summary_recording",
            runners={"_summary_recording": _SummaryRecordingRunner},
        )
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error == "all backends failed"


# ── Trace storage policy ─────────────────────────────────────────────────


class TestTraceStorage:
    """Storage split: ``inline`` lands trace in the variable pool (so a
    downstream node can ``selector=[node_id, "trace"]``); ``metadata``
    lands it in ``process_data`` (run-history viewable, variable pool
    stays clean — see node module docstring)."""

    def test_storage_inline(self):
        """``storage="inline"`` → ``outputs.trace`` populated; downstream
        nodes can read the trace blob from the variable pool."""
        _SummaryRecordingRunner.error_count = 0
        _SummaryRecordingRunner.backend_count = 2
        _SummaryRecordingRunner.yield_text = "ok"
        _SummaryRecordingRunner.per_alias_error = {}
        node = _make_node(
            runner_name="_summary_recording",
            runners={"_summary_recording": _SummaryRecordingRunner},
            diagnostics={"storage": "inline"},
        )
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        assert "trace" in nrr.outputs
        assert nrr.outputs["trace"]["runner_name"] == "_summary_recording"
        # process_data is empty under inline storage — keeps the run-history
        # viewer from double-rendering the trace.
        assert "ensemble_trace" not in nrr.process_data

    def test_storage_metadata(self):
        """``storage="metadata"`` → trace lands in ``process_data["ensemble_trace"]``;
        ``outputs`` stays clean (no ``trace`` key, so a downstream
        selector against ``[node_id, "trace"]`` deliberately misses)."""
        _SummaryRecordingRunner.error_count = 0
        _SummaryRecordingRunner.backend_count = 2
        _SummaryRecordingRunner.yield_text = "ok"
        _SummaryRecordingRunner.per_alias_error = {}
        node = _make_node(
            runner_name="_summary_recording",
            runners={"_summary_recording": _SummaryRecordingRunner},
            diagnostics={"storage": "metadata"},
        )
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert "trace" not in nrr.outputs
        assert "ensemble_trace" in nrr.process_data
        assert nrr.process_data["ensemble_trace"]["runner_name"] == "_summary_recording"

    def test_trace_carries_real_backend_info(self):
        """Trace's ``backends`` records each instance's *backend class
        name* + declared capabilities (not a stripped placeholder), so
        a debug viewer can tell llama_cpp from openai_compat at a
        glance and reason about what each contributor *could* do on
        this run. Pinned because the previous P3.B.3 cut downgraded
        these to ``backend=""`` / empty caps."""
        _SummaryRecordingRunner.error_count = 0
        _SummaryRecordingRunner.backend_count = 2
        _SummaryRecordingRunner.yield_text = "ok"
        _SummaryRecordingRunner.per_alias_error = {}
        node = _make_node(
            runner_name="_summary_recording",
            runners={"_summary_recording": _SummaryRecordingRunner},
            backends={"synthetic": _TokenStepBackend},
            diagnostics={"storage": "inline"},
        )
        events = list(node._run())
        trace = events[-1].node_run_result.outputs["trace"]
        backends_info = trace["backends"]
        assert [bi["id"] for bi in backends_info] == ["s0", "s1"]
        assert all(bi["backend"] == "token_step_backend" for bi in backends_info)
        # ``_TokenStepBackend`` declares TOKEN_STEP + TOP_PROBS.
        assert backends_info[0]["capabilities"] == ["token_step", "top_probs"]
        # ``model_alias`` rides on metadata so consumers can
        # cross-reference back to the spec.
        assert backends_info[0]["metadata"]["model_alias"] == "m1"


# ── Diagnostics gating ──────────────────────────────────────────────────


class TestDiagnosticsGating:
    """Heavy diagnostics fields default off so the trace stays small;
    runner code blindly calls ``trace.record_*`` and the collector
    drops the unset fields. This test keeps the wiring honest at the
    node level — that the collector built from ``cfg.diagnostics``
    actually reaches the runner."""

    def test_diagnostics_token_candidates_off(self):
        """``include_token_candidates=False`` → token-trace rows have
        no ``per_model``. A scripted runner that records a step entry
        with full ``per_model`` exercises the redaction path."""

        class _RecordingRunner(EnsembleRunner[_ScriptedConfig]):
            name = "_recording"
            config_class: ClassVar[type[BaseModel]] = _ScriptedConfig
            aggregator_scope: ClassVar[str] = "response"
            required_capabilities: ClassVar[frozenset[Capability]] = frozenset()
            i18n_key_prefix: ClassVar[str] = "test.recording"
            ui_schema: ClassVar[dict] = {}

            def __init__(self, executor: ThreadPoolExecutor, aggregator_config: BaseModel) -> None:
                del executor, aggregator_config

            @classmethod
            def requirements(cls, config: _ScriptedConfig) -> list[Requirement]:
                del config
                return []

            def run(
                self,
                sources: dict[str, SourceInput],
                backends: dict[str, ModelBackend],
                aggregator: Aggregator,
                config: _ScriptedConfig,
                trace: TraceCollector,
            ) -> Iterator[RunnerEvent]:
                del sources, backends, aggregator, config
                trace.record_token_step(
                    {
                        "step": 0,
                        "selected_token": "x",
                        "selected_score": 0.5,
                        "elapsed_ms": 1,
                        "per_model": {"s0": [{"token": "x", "prob": 0.5, "logit": None}]},
                    }
                )
                yield DoneEvent(kind="done", text="x", metadata={})

        node = _make_node(
            runner_name="_recording",
            runners={"_recording": _RecordingRunner},
            diagnostics={"storage": "inline", "include_token_candidates": False},
        )
        events = list(node._run())
        trace = events[-1].node_run_result.outputs["trace"]
        # Token row survives — only the ``per_model`` candidate dict is
        # redacted away by the collector at record time.
        assert len(trace["token_trace"]) == 1
        assert "per_model" not in trace["token_trace"][0]
        assert trace["token_trace"][0]["selected_token"] == "x"


# ── Spec / weight resolution ─────────────────────────────────────────────


class TestSpecResolution:
    """ADR-v3-16 spec resolution: missing / malformed
    ``ModelInvocationSpec`` fail-fast before any backend is touched."""

    def test_missing_spec_raises(self):
        """``spec_selector`` resolving to nothing → ``MissingSpecError``."""
        # Build pool with only one of two expected specs.
        pool = VariablePool()
        pool.add(["src_0", "spec"], _make_spec_dict(model_alias="m1"))
        node = _make_node(pool=pool, selectors=[["src_0", "spec"], ["src_1", "spec"]])
        with pytest.raises(MissingSpecError) as exc:
            list(node._run())
        assert exc.value.source_id == "s1"

    def test_invalid_spec_missing_keys_raises(self):
        """Resolved value lacking ``prompt`` → ``InvalidSpecError``."""
        pool = VariablePool()
        pool.add(["src_0", "spec"], _make_spec_dict(model_alias="m1"))
        # Second source resolves to a dict missing ``prompt``.
        pool.add(["src_1", "spec"], {"model_alias": "m2", "sampling_params": {"top_k": 5}})
        node = _make_node(pool=pool, selectors=[["src_0", "spec"], ["src_1", "spec"]])
        with pytest.raises(InvalidSpecError) as exc:
            list(node._run())
        assert exc.value.source_id == "s1"
        assert "prompt" in exc.value.reason

    def test_invalid_sampling_params_wrapped_with_source_id(self):
        """Spec carrying nonsense sampling (e.g. ``top_k=-1``) trips
        ``TokenStepParams`` validation; the node wraps the bare pydantic
        error in :class:`InvalidSpecError` so the panel sees *which*
        source's sampling is broken instead of getting a stack trace
        with no source attribution."""
        pool = VariablePool()
        pool.add(["src_0", "spec"], _make_spec_dict(model_alias="m1"))
        pool.add(
            ["src_1", "spec"],
            _make_spec_dict(model_alias="m2", sampling_params={"top_k": -1}),
        )
        node = _make_node(pool=pool, selectors=[["src_0", "spec"], ["src_1", "spec"]])
        with pytest.raises(InvalidSpecError) as exc:
            list(node._run())
        assert exc.value.source_id == "s1"
        assert "sampling_params" in exc.value.reason


# ── Backend-private extras routing ───────────────────────────────────────


class TestExtraRouting:
    """ADR-v3-16: backend-private knobs (mirostat, repetition_penalty,
    …) ride on ``TokenStepParams.extra`` so a backend that reads
    ``params.extra`` (e.g. ``llama_cpp.step_token`` writing them
    straight into the request body) sees them on every fan-out call.
    Both the upstream spec's ``extra`` and the per-source
    ``TokenSourceRef.extra`` route through this single channel; ref
    wins on key collision (consumer-vocab overrides producer-vocab)."""

    def test_spec_extra_reaches_token_step_params(self):
        """``spec.extra={"mirostat": 2}`` → effective params carry it."""

        captured: dict[str, Any] = {}

        class _CapturingRunner(EnsembleRunner[_ScriptedConfig]):
            name = "_capturing"
            config_class: ClassVar[type[BaseModel]] = _ScriptedConfig
            aggregator_scope: ClassVar[str] = "response"
            required_capabilities: ClassVar[frozenset[Capability]] = frozenset()
            i18n_key_prefix: ClassVar[str] = "test.capturing"
            ui_schema: ClassVar[dict] = {}

            def __init__(self, executor: ThreadPoolExecutor, aggregator_config: BaseModel) -> None:
                del executor, aggregator_config

            @classmethod
            def requirements(cls, config: _ScriptedConfig) -> list[Requirement]:
                del config
                return []

            def run(
                self,
                sources: dict[str, SourceInput],
                backends: dict[str, ModelBackend],
                aggregator: Aggregator,
                config: _ScriptedConfig,
                trace: TraceCollector,
            ) -> Iterator[RunnerEvent]:
                del backends, aggregator, config, trace
                # Snapshot the sources dict so the assertion can read
                # the per-source ``params.extra`` after the run.
                for sid, src in sources.items():
                    captured[sid] = dict(src["params"].extra)
                yield DoneEvent(kind="done", text="ok", metadata={})

        pool = VariablePool()
        pool.add(
            ["src_0", "spec"],
            _make_spec_dict(model_alias="m1", extra={"mirostat": 2, "rep_penalty": 1.1}),
        )
        pool.add(["src_1", "spec"], _make_spec_dict(model_alias="m2"))
        node = _make_node(
            pool=pool,
            selectors=[["src_0", "spec"], ["src_1", "spec"]],
            runner_name="_capturing",
            runners={"_capturing": _CapturingRunner},
        )
        list(node._run())
        # Source 0: spec.extra reaches params.extra unchanged.
        assert captured["s0"] == {"mirostat": 2, "rep_penalty": 1.1}
        # Source 1: empty spec.extra → empty params.extra (default).
        assert captured["s1"] == {}

    def test_ref_extra_overrides_spec_extra(self):
        """Both layers' extras merge with ref winning on key collision —
        same precedence ``ensemble_aggregator`` uses for source-level
        overrides. New keys from either side land additively."""

        captured: dict[str, Any] = {}

        class _CapturingRunner(EnsembleRunner[_ScriptedConfig]):
            name = "_capturing_ref"
            config_class: ClassVar[type[BaseModel]] = _ScriptedConfig
            aggregator_scope: ClassVar[str] = "response"
            required_capabilities: ClassVar[frozenset[Capability]] = frozenset()
            i18n_key_prefix: ClassVar[str] = "test.capturingRef"
            ui_schema: ClassVar[dict] = {}

            def __init__(self, executor: ThreadPoolExecutor, aggregator_config: BaseModel) -> None:
                del executor, aggregator_config

            @classmethod
            def requirements(cls, config: _ScriptedConfig) -> list[Requirement]:
                del config
                return []

            def run(
                self,
                sources: dict[str, SourceInput],
                backends: dict[str, ModelBackend],
                aggregator: Aggregator,
                config: _ScriptedConfig,
                trace: TraceCollector,
            ) -> Iterator[RunnerEvent]:
                del backends, aggregator, config, trace
                for sid, src in sources.items():
                    captured[sid] = dict(src["params"].extra)
                yield DoneEvent(kind="done", text="ok", metadata={})

        pool = VariablePool()
        pool.add(
            ["src_0", "spec"],
            _make_spec_dict(
                model_alias="m1",
                extra={"mirostat": 2, "shared": "from_spec"},
            ),
        )
        pool.add(["src_1", "spec"], _make_spec_dict(model_alias="m2"))
        node = _make_node(
            pool=pool,
            selectors=[["src_0", "spec"], ["src_1", "spec"]],
            runner_name="_capturing_ref",
            runners={"_capturing_ref": _CapturingRunner},
            token_source_overrides=[
                {"extra": {"shared": "from_ref", "ref_only": True}},
                {},
            ],
        )
        list(node._run())
        # ``shared`` from ref wins; ``mirostat`` (spec only) and
        # ``ref_only`` (ref only) both land in the merged dict.
        assert captured["s0"] == {
            "mirostat": 2,
            "shared": "from_ref",
            "ref_only": True,
        }


# ── Weight resolution ────────────────────────────────────────────────────


class TestWeightResolution:
    """Dynamic ``TokenSourceRef.weight`` selectors fail-fast unless
    ``fallback_weight`` opts into graceful-degrade (ADR-v3-15)."""

    def test_dynamic_weight_unresolved_no_fallback_raises(self):
        """Missing variable → ``WeightResolutionError`` (no fallback set)."""
        # Spec selectors valid; weight selector points at unset variable.
        pool, selectors = _build_pool(["m1", "m2"])
        _ScriptedRunner.scripted_events = [DoneEvent(kind="done", text="ok", metadata={})]
        node = _make_node(
            pool=pool,
            selectors=selectors,
            token_source_overrides=[
                {"weight": ["weights", "missing"]},
                {},
            ],
        )
        with pytest.raises(WeightResolutionError) as exc:
            list(node._run())
        assert exc.value.source_id == "s0"

    def test_dynamic_weight_fallback_used(self):
        """Missing variable + ``fallback_weight`` set → degrade silently."""
        pool, selectors = _build_pool(["m1", "m2"])
        _ScriptedRunner.scripted_events = [DoneEvent(kind="done", text="ok", metadata={})]
        node = _make_node(
            pool=pool,
            selectors=selectors,
            token_source_overrides=[
                {"weight": ["weights", "missing"], "fallback_weight": 0.5},
                {},
            ],
        )
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED

    @pytest.mark.parametrize("bad_value", [0, 0.0, -1.0, -0.0001])
    def test_dynamic_weight_non_positive_rejected(self, bad_value):
        """Static weight has a ``> 0`` schema guard, but a dynamic
        selector is only known at run time — the resolver must apply
        the same rule (zero would silently zero a voter, negative
        would let it cancel siblings in weighted-sum tallying)."""
        pool, selectors = _build_pool(["m1", "m2"])
        pool.add(["weights", "bad"], bad_value)
        node = _make_node(
            pool=pool,
            selectors=selectors,
            token_source_overrides=[{"weight": ["weights", "bad"]}, {}],
        )
        with pytest.raises(WeightResolutionError) as exc:
            list(node._run())
        assert exc.value.source_id == "s0"
        assert "must be > 0" in exc.value.reason


# ── DSL smuggle defence ──────────────────────────────────────────────────


class TestDSLSmuggle:
    """Two cooperating defences — the outer ``BaseNodeData`` allow-extras
    layer rejects the named sensitive keys via ``mode="before"``; the
    inner ``ParallelEnsembleConfig`` forbids extras at the business-config
    boundary; the runner's ``config_class.model_validate`` rejects extras
    at the runtime layer (``runner_config`` is dict-typed at the schema
    level, so the smuggle is caught at run start instead of at parse
    time). All three are exercised below."""

    def _valid_ensemble(self) -> dict[str, Any]:
        return {
            "token_sources": [
                {"source_id": "s0", "spec_selector": ["src_0", "spec"]},
                {"source_id": "s1", "spec_selector": ["src_1", "spec"]},
            ],
            "runner_name": "r",
            "aggregator_name": "a",
        }

    def test_dsl_rejects_model_url(self):
        """Top-level ``model_url`` triggers the ``mode="before"`` validator
        — rejected before pydantic stashes it in ``__pydantic_extra__``."""
        with pytest.raises(ValidationError) as exc:
            ParallelEnsembleNodeData.model_validate(
                {
                    "type": PARALLEL_ENSEMBLE_NODE_TYPE,
                    "title": "pe",
                    "model_url": "http://internal:8080",
                    "ensemble": self._valid_ensemble(),
                }
            )
        assert "model_url" in str(exc.value)
        assert "URLs and credentials live in the model registry yaml" in str(exc.value)

    def test_dsl_rejects_top_level_credentials(self):
        """Each forbidden top-level key (``api_key`` / ``api_key_env`` /
        ``url`` / ``endpoint``) hits the same rejection path."""
        for key in ("api_key", "api_key_env", "url", "endpoint"):
            with pytest.raises(ValidationError) as exc:
                ParallelEnsembleNodeData.model_validate(
                    {
                        "type": PARALLEL_ENSEMBLE_NODE_TYPE,
                        "title": "pe",
                        key: "value",
                        "ensemble": self._valid_ensemble(),
                    }
                )
            assert key in str(exc.value)

    def test_ensemble_config_extra_forbid(self):
        """The nested ``ParallelEnsembleConfig`` forbids extras at parse
        time — a typo / smuggle attempt at the business-config boundary
        is caught even before the node's ``_run`` is reached."""
        with pytest.raises(ValidationError) as exc:
            ParallelEnsembleConfig.model_validate(
                {
                    **self._valid_ensemble(),
                    "model_url": "http://x",  # not a declared field of the inner config
                }
            )
        assert "Extra inputs are not permitted" in str(exc.value) or "model_url" in str(exc.value)

    def test_legacy_keys_rejected(self):
        """Legacy v2.4 ``question_variable`` / ``model_aliases`` are no
        longer declared on ``ParallelEnsembleConfig`` (ADR-v3-16); they
        hit the same ``extra="forbid"`` rejection as any other typo."""
        for legacy_key in ("question_variable", "model_aliases"):
            with pytest.raises(ValidationError) as exc:
                ParallelEnsembleConfig.model_validate(
                    {
                        **self._valid_ensemble(),
                        legacy_key: ["x", "y"],
                    }
                )
            assert legacy_key in str(exc.value) or "Extra inputs are not permitted" in str(exc.value)

    def test_dsl_rejects_runner_config_smuggle(self):
        """``runner_config`` is dict-typed at the schema level so a
        DSL like ``runner_config: {model_url: "..."}`` survives parsing.
        The runner's ``config_class.model_validate`` (run-time,
        ``extra="forbid"``) is the layer that catches the smuggle —
        pinned here against the real ``TokenStepConfig``, the only
        runner that ships in the box post-P3.B.0."""
        with pytest.raises(ValidationError) as exc:
            TokenStepConfig.model_validate({"max_len": 5, "model_url": "http://x"})
        assert "Extra inputs are not permitted" in str(exc.value)

    def test_dsl_compat_keys_allowed(self):
        """``BaseNodeData(extra="allow")`` keeps cross-cutting graph
        extras (``selected`` / ``params`` / ``paramSchemas`` /
        ``datasource_label``) flowing through saved-workflow payloads;
        only the *named* sensitive keys are rejected."""
        node_data = ParallelEnsembleNodeData.model_validate(
            {
                "type": PARALLEL_ENSEMBLE_NODE_TYPE,
                "title": "pe",
                "selected": True,
                "params": {"foo": "bar"},
                "paramSchemas": [{"name": "foo"}],
                "datasource_label": "x",
                "ensemble": self._valid_ensemble(),
            }
        )
        # Validation passed → the node-data object exists. Extras land in
        # ``__pydantic_extra__`` (BaseNodeData inherits ``extra="allow"``).
        extras = getattr(node_data, "__pydantic_extra__", None) or {}
        assert extras.get("selected") is True
        assert extras.get("params") == {"foo": "bar"}
        assert extras.get("datasource_label") == "x"
