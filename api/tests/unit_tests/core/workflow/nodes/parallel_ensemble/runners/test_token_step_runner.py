"""``TokenStepRunner`` — covers the four behaviours called out in
TASKS.md P2.7 for the joint loop:

* registration + capability surface,
* deterministic event sequence (N × TokenEvent + 1 × DoneEvent) on EOS,
* ``stopped_by="max_len"`` when the model never emits ``<end>``,
* prompt-sync invariant (every backend's running prompt grows by the
  same winning token each round),
* validate_selection + requirements derivation.
"""

from __future__ import annotations

import pytest

from core.workflow.nodes.parallel_ensemble.aggregators.token.sum_score import (
    SumScoreAggregator,
    SumScoreConfig,
)
from core.workflow.nodes.parallel_ensemble.registry.runner_registry import (
    RunnerRegistry,
)
from core.workflow.nodes.parallel_ensemble.runners.token_step import (
    TokenStepConfig,
    TokenStepRunner,
)
from core.workflow.nodes.parallel_ensemble.spi.capability import Capability
from core.workflow.nodes.parallel_ensemble.spi.trace import (
    DiagnosticsConfig,
    TraceCollector,
)

from .conftest import FakeBackend


def test_registered_with_token_scope():
    """Side-effect import wires runner into ``RunnerRegistry``."""
    cls = RunnerRegistry.get("token_step")
    assert cls is TokenStepRunner
    assert cls.aggregator_scope == "token"
    assert cls.required_capabilities == frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS})


def test_requirements_derive_from_top_k():
    cfg = TokenStepConfig(top_k=7)
    reqs = TokenStepRunner.requirements(cfg)
    kinds = {r["kind"]: r["value"] for r in reqs}
    assert kinds == {"min_top_k": 7, "needs_logprobs": True}


def test_token_step_runner_eos(executor, cand):
    """EOS path: 3 tokens then ``<end>`` → 3 TokenEvents + 1 DoneEvent."""
    backends = {
        "m1": FakeBackend(
            "m1",
            scripted_steps=[
                [cand("hello", 0.6), cand("world", 0.4)],
                [cand(" ", 0.5), cand(",", 0.5)],
                [cand("there", 0.7)],
                [cand("<end>", 1.0)],
            ],
        ),
        "m2": FakeBackend(
            "m2",
            scripted_steps=[
                [cand("hello", 0.5)],
                [cand(" ", 0.6)],
                [cand("there", 0.6)],
                [cand("<end>", 1.0)],
            ],
        ),
    }
    runner = TokenStepRunner(executor=executor, aggregator_config=SumScoreConfig())
    trace = TraceCollector(DiagnosticsConfig())
    events = list(
        runner.run(
            question="hi",
            backends=backends,
            aggregator=SumScoreAggregator(),
            config=TokenStepConfig(max_len=20, enable_think=False),
            trace=trace,
        )
    )

    kinds = [e["kind"] for e in events]
    assert kinds == ["token", "token", "token", "done"]
    deltas = [e["delta"] for e in events if e["kind"] == "token"]  # type: ignore[typeddict-item]
    assert deltas == ["hello", " ", "there"]

    done = events[-1]
    assert done["text"] == "hello there"  # type: ignore[typeddict-item]
    assert done["metadata"]["stopped_by"] == "eos"  # type: ignore[typeddict-item]
    assert done["metadata"]["tokens_count"] == 3  # type: ignore[typeddict-item]


def test_token_step_runner_max_len(executor, cand):
    """Backend never returns ``<end>`` → loop force-stops at max_len."""
    backends = {
        "m1": FakeBackend("m1", always_emit=[cand("a", 1.0)]),
        "m2": FakeBackend("m2", always_emit=[cand("a", 1.0)]),
    }
    runner = TokenStepRunner(executor=executor, aggregator_config=SumScoreConfig())
    trace = TraceCollector(DiagnosticsConfig())
    events = list(
        runner.run(
            question="hi",
            backends=backends,
            aggregator=SumScoreAggregator(),
            config=TokenStepConfig(max_len=4, enable_think=False),
            trace=trace,
        )
    )

    token_events = [e for e in events if e["kind"] == "token"]
    done_events = [e for e in events if e["kind"] == "done"]
    assert len(token_events) == 4
    assert len(done_events) == 1
    assert done_events[0]["text"] == "aaaa"  # type: ignore[typeddict-item]
    assert done_events[0]["metadata"]["stopped_by"] == "max_len"  # type: ignore[typeddict-item]
    assert done_events[0]["metadata"]["tokens_count"] == 4  # type: ignore[typeddict-item]


def test_token_step_prompt_sync(executor, cand):
    """Every backend's running prompt must grow by the same winning token each step."""
    m1 = FakeBackend(
        "m1",
        scripted_steps=[
            [cand("hello", 0.6)],
            [cand("<end>", 1.0)],
        ],
    )
    m2 = FakeBackend(
        "m2",
        scripted_steps=[
            [cand("hello", 0.5)],
            [cand("<end>", 1.0)],
        ],
    )
    runner = TokenStepRunner(executor=executor, aggregator_config=SumScoreConfig(use_weights=False))
    trace = TraceCollector(DiagnosticsConfig())
    list(
        runner.run(
            question="hi",
            backends={"m1": m1, "m2": m2},
            aggregator=SumScoreAggregator(),
            config=TokenStepConfig(max_len=10, enable_think=False),
            trace=trace,
        )
    )

    # Step 0 prompts must match (both fed templated initial prompt).
    assert m1.step_calls[0][0] == m2.step_calls[0][0]
    # Step 1 prompts: each backend's prompt should have gained "hello".
    p0_m1, _ = m1.step_calls[0]
    p1_m1, _ = m1.step_calls[1]
    p0_m2, _ = m2.step_calls[0]
    p1_m2, _ = m2.step_calls[1]
    assert p1_m1 == p0_m1 + "hello"
    assert p1_m2 == p0_m2 + "hello"


def test_token_step_handles_per_model_errors(executor, cand):
    """A single backend raising mid-step does not abort the round; the
    remaining voters still pick a winner and the alias lands in the
    trace's per_model_errors field."""
    m1 = FakeBackend(
        "m1",
        scripted_steps=[[cand("hi", 1.0)], [cand("<end>", 1.0)]],
    )
    m2 = FakeBackend(
        "m2",
        scripted_steps=[[cand("<end>", 1.0)]],
        step_raises=[RuntimeError("boom")],
    )
    runner = TokenStepRunner(executor=executor, aggregator_config=SumScoreConfig())
    diagnostics = DiagnosticsConfig(include_token_candidates=True, include_per_backend_errors=True)
    trace = TraceCollector(diagnostics)
    events = list(
        runner.run(
            question="hi",
            backends={"m1": m1, "m2": m2},
            aggregator=SumScoreAggregator(),
            config=TokenStepConfig(max_len=10, enable_think=False),
            trace=trace,
        )
    )

    # Final text covers the surviving voter's pick.
    done = events[-1]
    assert done["text"] == "hi"  # type: ignore[typeddict-item]
    # Trace recorded m2 in per_model_errors for step 0.
    final = trace.finalize(
        runner_name="token_step",
        runner_config={},
        aggregator_name="sum_score",
        aggregator_config={},
        backends=[],
    )
    assert "m2" in final["token_trace"][0]["per_model_errors"]


def test_validate_selection_too_few_models():
    """A single-model selection is not a valid ensemble."""
    issues = TokenStepRunner.validate_selection(
        TokenStepConfig(),
        ["only_one"],
        registry=_FakeRegistry({}),
    )
    assert any(i["severity"] == "error" and "at least 2" in i["message"] for i in issues)


def test_validate_selection_enable_think_no_think_models_warns():
    """``enable_think=True`` with no think-type aliases → warning."""
    registry = _FakeRegistry({"a": _RegSpec(type="normal"), "b": _RegSpec(type="normal")})
    issues = TokenStepRunner.validate_selection(TokenStepConfig(enable_think=True), ["a", "b"], registry=registry)
    assert any(i["severity"] == "warning" and i["i18n_key"] == "parallelEnsemble.errors.thinkNoModels" for i in issues)


def test_validate_selection_enable_think_off_with_think_models_warns():
    """``enable_think=False`` plus a think model → warning."""
    registry = _FakeRegistry({"a": _RegSpec(type="normal"), "b": _RegSpec(type="think")})
    issues = TokenStepRunner.validate_selection(TokenStepConfig(enable_think=False), ["a", "b"], registry=registry)
    assert any(
        i["severity"] == "warning" and i["i18n_key"] == "parallelEnsemble.errors.thinkOffWithThinkModels"
        for i in issues
    )


def test_run_rejects_response_aggregator(executor):
    """Defensive: a response-scope aggregator handed in by mistake fails loud."""
    from core.workflow.nodes.parallel_ensemble.aggregators.response.concat import (
        ConcatAggregator,
        ConcatConfig,
    )

    runner = TokenStepRunner(executor=executor, aggregator_config=ConcatConfig())
    trace = TraceCollector(DiagnosticsConfig())
    backends = {"m1": FakeBackend("m1"), "m2": FakeBackend("m2")}
    with pytest.raises(TypeError, match="TokenAggregator"):
        list(
            runner.run(
                question="hi",
                backends=backends,
                aggregator=ConcatAggregator(),  # wrong scope
                config=TokenStepConfig(max_len=2, enable_think=False),
                trace=trace,
            )
        )


def test_chat_template_invoked_for_capable_backends(executor, cand):
    """Backends that declare CHAT_TEMPLATE get ``apply_template`` called once."""
    backend = FakeBackend(
        "m1",
        scripted_steps=[[cand("<end>", 1.0)]],
        capabilities=frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS, Capability.CHAT_TEMPLATE}),
    )
    bare = FakeBackend("m2", scripted_steps=[[cand("<end>", 1.0)]])
    runner = TokenStepRunner(executor=executor, aggregator_config=SumScoreConfig())
    trace = TraceCollector(DiagnosticsConfig())
    list(
        runner.run(
            question="hi",
            backends={"m1": backend, "m2": bare},
            aggregator=SumScoreAggregator(),
            config=TokenStepConfig(enable_think=False),
            trace=trace,
        )
    )
    # m1 gets templated, m2 falls through with bare question.
    assert len(backend.template_calls) == 1
    assert backend.template_calls[0][1]["content"] == "hi"
    assert bare.template_calls == []
    assert bare.step_calls[0][0] == "hi"


# ── Tiny in-test registry stand-in ────────────────────────────────────


class _RegSpec:
    def __init__(self, type: str = "normal") -> None:
        self.type = type


class _FakeRegistry:
    """Just enough of ``ModelRegistry`` for ``validate_selection`` to walk."""

    def __init__(self, specs: dict[str, _RegSpec]) -> None:
        self._specs = specs

    def __contains__(self, alias: object) -> bool:
        return isinstance(alias, str) and alias in self._specs

    def get(self, alias: str) -> _RegSpec:
        return self._specs[alias]
