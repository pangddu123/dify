"""P2.10 ``ParallelEnsembleNode`` — node-level event sequence + §9 startup
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
keyword args that P2.9 wires from the factory). This keeps the tests
oriented at *what the node does* rather than at the runtime plumbing
the factory already covers.

Synthetic runners / backends rather than the built-ins
------------------------------------------------------

Most tests use a ``_ScriptedRunner`` that yields a pre-baked event
sequence: it lets us assert event-by-event without spinning up a
``ThreadPoolExecutor`` or having to script per-token candidate lists
just to drive the node-side branch we care about. Where we need a
real runner end-to-end (storage / diagnostics tests), we use the
shipped ``ResponseLevelRunner`` + ``MajorityVoteAggregator`` with a
silent ``FakeBackend`` — that path is already pinned by
``runners/test_response_level_runner.py``, so here we only assert what
the *node* does with the trace it produces.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.workflow.nodes.parallel_ensemble import PARALLEL_ENSEMBLE_NODE_TYPE
from core.workflow.nodes.parallel_ensemble.aggregators.response.majority_vote import (
    MajorityVoteAggregator,
    MajorityVoteConfig,
)
from core.workflow.nodes.parallel_ensemble.aggregators.token.sum_score import (
    SumScoreAggregator,
)
from core.workflow.nodes.parallel_ensemble.entities import (
    ParallelEnsembleConfig,
    ParallelEnsembleNodeData,
)
from core.workflow.nodes.parallel_ensemble.exceptions import (
    StructuredValidationError,
    UnknownModelAliasError,
)
from core.workflow.nodes.parallel_ensemble.node import ParallelEnsembleNode
from core.workflow.nodes.parallel_ensemble.runners.response_level import (
    ResponseLevelConfig,
    ResponseLevelRunner,
)
from core.workflow.nodes.parallel_ensemble.spi.aggregator import (
    AggregationContext,
    Aggregator,
    ResponseAggregator,
    ResponseSignal,
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
    """No required caps — pairs with ``ResponseLevelRunner``."""

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
        question: str,
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: _ScriptedConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        del question, backends, aggregator, config, trace
        yield from type(self).scripted_events


class _BigTopKConfig(BaseModel):
    """Synthetic runner config with no top_k cap.

    ``TokenStepConfig`` caps ``top_k`` at 20 (matches OpenAI), so the
    natural runner-config path can never produce a ``min_top_k=25``
    requirement. To exercise the §9 requirements-rejection branch we
    need a config schema that lets a 25 through to ``requirements()``
    without pydantic intercepting it first.
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
        question: str,
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: _BigTopKConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        del question, backends, aggregator, config, trace
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
        question: str,
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: _ScriptedConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        del question, backends, aggregator, config, trace
        yield DoneEvent(kind="done", text="", metadata={})


class _NoSignalAggregator(ResponseAggregator[MajorityVoteConfig]):
    """Stand-in response aggregator that ignores signals; used to pair
    with synthetic runners without dragging in P2.5 vote semantics."""

    name = "_no_signal"
    config_class: ClassVar[type[BaseModel]] = MajorityVoteConfig
    i18n_key_prefix: ClassVar[str] = "test.noSignal"
    ui_schema: ClassVar[dict] = {}

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: AggregationContext,
        config: MajorityVoteConfig,
    ) -> dict:
        del signals, context, config
        return {"text": "", "metadata": {}}


class _NoSignalTokenAggregator(TokenAggregator[MajorityVoteConfig]):
    """Stand-in token aggregator paired with ``_BigTopKRunner``."""

    name = "_no_signal_token"
    config_class: ClassVar[type[BaseModel]] = MajorityVoteConfig
    i18n_key_prefix: ClassVar[str] = "test.noSignalToken"
    ui_schema: ClassVar[dict] = {}

    def aggregate(
        self,
        signals: TokenSignals,
        context: AggregationContext,
        config: MajorityVoteConfig,
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


def _build_pool(question: str = "is the sky blue?") -> VariablePool:
    pool = VariablePool()
    pool.add(["start", "user_input"], question)
    return pool


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
    extra_node_data: dict[str, object] | None = None,
) -> ParallelEnsembleNode:
    """Bypass ``Node.__init__`` and inject just the pieces ``_run`` reads.

    Mirrors ``ensemble_aggregator/test_node.py::_make_node`` so the two
    suites read symmetrically. The caller supplies registries / specs;
    sensible defaults paint a 2-alias scripted-runner setup that most
    tests can override one field of.
    """
    aliases = model_aliases or ["m1", "m2"]
    specs = specs or {alias: _SyntheticSpec(id=alias, backend="synthetic", model_name=alias) for alias in aliases}
    runners = runners or {"_scripted": _ScriptedRunner}
    aggregators = aggregators or {"_no_signal": _NoSignalAggregator}
    backends = backends or {"synthetic": _ResponseOnlyBackend}

    payload: dict[str, Any] = {
        "type": PARALLEL_ENSEMBLE_NODE_TYPE,
        "title": "pe",
        "ensemble": {
            "question_variable": ["start", "user_input"],
            "model_aliases": aliases,
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
    rs.variable_pool = pool or _build_pool()
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
        assert nrr.inputs["question"] == "is the sky blue?"
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
        )
        with pytest.raises(StructuredValidationError) as exc:
            list(node._run())
        # Two backends both reject → both issues land in the structured error.
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


# ── Per-backend timeout / failure handling ───────────────────────────────


class TestBackendFailures:
    """``ResponseLevelRunner`` is the cleanest harness for the failure
    semantics: a single backend timeout is absorbed (run keeps running,
    error logged in trace, status SUCCEEDED), every-backend-failed
    degrades to FAILED via the trace summary's ``error_count`` /
    ``backend_count`` invariant the node uses in ``_derive_status``."""

    def test_single_model_timeout(self):
        """One backend raises ``TimeoutError`` → run completes SUCCEEDED;
        trace summary records 1 error against backend_count=2."""
        backends_map = {
            "ok_be": _make_response_backend_class("ok"),
            "timeout_be": _make_response_backend_class("", scripted_exc=TimeoutError("timed out")),
        }
        node = _make_node(
            runner_name="response_level",
            aggregator_name="majority_vote",
            runners={"response_level": ResponseLevelRunner},
            aggregators={"majority_vote": MajorityVoteAggregator},
            backends={
                "ok_be": backends_map["ok_be"],
                "timeout_be": backends_map["timeout_be"],
            },
            specs={
                "m1": _SyntheticSpec(id="m1", backend="ok_be", model_name="m1"),
                "m2": _SyntheticSpec(id="m2", backend="timeout_be", model_name="m2"),
            },
            diagnostics={"storage": "metadata", "include_per_backend_errors": True},
        )
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        # One survivor → majority vote returns its text.
        assert nrr.outputs["text"] == "ok"
        # Trace summary captures the failure even though the run
        # completed; ``_derive_status`` reads ``error_count`` /
        # ``backend_count`` to decide SUCCEEDED vs FAILED.
        trace = nrr.process_data["ensemble_trace"]
        assert trace["summary"]["error_count"] == 1
        assert trace["summary"]["backend_count"] == 2
        # Per-backend trace surfaces the timeout for the failed alias.
        by_alias = {row["source_id"]: row for row in trace["response_trace"]}
        assert "TimeoutError" in by_alias["m2"]["error"]

    def test_all_timeout(self):
        """Every backend raises → ``StreamCompletedEvent.status == FAILED``
        (response_level's trace summary marks ``error_count == backend_count``)."""
        backend_cls = _make_response_backend_class("", scripted_exc=TimeoutError("timed out"))
        node = _make_node(
            runner_name="response_level",
            aggregator_name="majority_vote",
            runners={"response_level": ResponseLevelRunner},
            aggregators={"majority_vote": MajorityVoteAggregator},
            backends={"synthetic": backend_cls},
        )
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error == "all backends failed"


def _make_response_backend_class(
    scripted_text: str,
    *,
    scripted_exc: Exception | None = None,
) -> type[ModelBackend]:
    """Build a ``_ResponseOnlyBackend`` subclass parametrised at class
    level — backend instances are constructed by the node from the
    backend *class*, so per-instance kwargs (text, exception) need to
    travel via the class itself rather than the constructor."""

    class _Param(_ResponseOnlyBackend):
        def __init__(self, spec: BaseSpec, http: object) -> None:
            super().__init__(spec, http, scripted_text=scripted_text, scripted_exc=scripted_exc)

    _Param.__name__ = f"_ParamResponseBackend_{scripted_text}_{type(scripted_exc).__name__}"
    return _Param


# ── Trace storage policy ─────────────────────────────────────────────────


class TestTraceStorage:
    """Storage split: ``inline`` lands trace in the variable pool (so a
    downstream node can ``selector=[node_id, "trace"]``); ``metadata``
    lands it in ``process_data`` (run-history viewable, variable pool
    stays clean — see node module docstring)."""

    def test_storage_inline(self):
        """``storage="inline"`` → ``outputs.trace`` populated; downstream
        nodes can read the trace blob from the variable pool."""
        backend_cls = _make_response_backend_class("ok")
        node = _make_node(
            runner_name="response_level",
            aggregator_name="majority_vote",
            runners={"response_level": ResponseLevelRunner},
            aggregators={"majority_vote": MajorityVoteAggregator},
            backends={"synthetic": backend_cls},
            diagnostics={"storage": "inline"},
        )
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        assert "trace" in nrr.outputs
        assert nrr.outputs["trace"]["runner_name"] == "response_level"
        # process_data is empty under inline storage — keeps the run-history
        # viewer from double-rendering the trace.
        assert "ensemble_trace" not in nrr.process_data

    def test_storage_metadata(self):
        """``storage="metadata"`` → trace lands in ``process_data["ensemble_trace"]``;
        ``outputs`` stays clean (no ``trace`` key, so a downstream
        selector against ``[node_id, "trace"]`` deliberately misses)."""
        backend_cls = _make_response_backend_class("ok")
        node = _make_node(
            runner_name="response_level",
            aggregator_name="majority_vote",
            runners={"response_level": ResponseLevelRunner},
            aggregators={"majority_vote": MajorityVoteAggregator},
            backends={"synthetic": backend_cls},
            diagnostics={"storage": "metadata"},
        )
        events = list(node._run())
        nrr = events[-1].node_run_result
        assert "trace" not in nrr.outputs
        assert "ensemble_trace" in nrr.process_data
        assert nrr.process_data["ensemble_trace"]["runner_name"] == "response_level"


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
                question: str,
                backends: dict[str, ModelBackend],
                aggregator: Aggregator,
                config: _ScriptedConfig,
                trace: TraceCollector,
            ) -> Iterator[RunnerEvent]:
                del question, backends, aggregator, config
                trace.record_token_step(
                    {
                        "step": 0,
                        "selected_token": "x",
                        "selected_score": 0.5,
                        "elapsed_ms": 1,
                        "per_model": {"m1": [{"token": "x", "prob": 0.5, "logit": None}]},
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


# ── DSL smuggle defence ──────────────────────────────────────────────────


class TestDSLSmuggle:
    """Two cooperating defences — the outer ``BaseNodeData`` allow-extras
    layer rejects the named sensitive keys via ``mode="before"``; the
    inner ``ParallelEnsembleConfig`` forbids extras at the business-config
    boundary; the runner's ``config_class.model_validate`` rejects extras
    at the runtime layer (``runner_config`` is dict-typed at the schema
    level, so the smuggle is caught at run start instead of at parse
    time). All three are exercised below."""

    def test_dsl_rejects_model_url(self):
        """Top-level ``model_url`` triggers the ``mode="before"`` validator
        — rejected before pydantic stashes it in ``__pydantic_extra__``."""
        with pytest.raises(ValidationError) as exc:
            ParallelEnsembleNodeData.model_validate(
                {
                    "type": PARALLEL_ENSEMBLE_NODE_TYPE,
                    "title": "pe",
                    "model_url": "http://internal:8080",
                    "ensemble": {
                        "question_variable": ["start", "user_input"],
                        "model_aliases": ["a", "b"],
                        "runner_name": "r",
                        "aggregator_name": "a",
                    },
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
                        "ensemble": {
                            "question_variable": ["start", "user_input"],
                            "model_aliases": ["a", "b"],
                            "runner_name": "r",
                            "aggregator_name": "a",
                        },
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
                    "question_variable": ["start", "user_input"],
                    "model_aliases": ["a", "b"],
                    "runner_name": "r",
                    "aggregator_name": "a",
                    "model_url": "http://x",  # not a declared field of the inner config
                }
            )
        assert "Extra inputs are not permitted" in str(exc.value) or "model_url" in str(exc.value)

    def test_dsl_rejects_runner_config_smuggle(self):
        """``runner_config`` is dict-typed at the schema level so a
        DSL like ``runner_config: {model_url: "..."}`` survives parsing.
        The runner's ``config_class.model_validate`` (run-time, ``extra="forbid"``)
        is the layer that catches the smuggle — pinned here against the
        real ``ResponseLevelConfig``."""
        with pytest.raises(ValidationError) as exc:
            ResponseLevelConfig.model_validate({"model_url": "http://x"})
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
                "ensemble": {
                    "question_variable": ["start", "user_input"],
                    "model_aliases": ["a", "b"],
                    "runner_name": "r",
                    "aggregator_name": "a",
                },
            }
        )
        # Validation passed → the node-data object exists. Extras land in
        # ``__pydantic_extra__`` (BaseNodeData inherits ``extra="allow"``).
        extras = getattr(node_data, "__pydantic_extra__", None) or {}
        assert extras.get("selected") is True
        assert extras.get("params") == {"foo": "bar"}
        assert extras.get("datasource_label") == "x"
