"""``ResponseLevelRunner`` — covers the P2.7 R-axis behaviours:

* registration + capability surface (no required caps, response scope),
* requirements() → [],
* validate_selection enforces ≥ 2 models,
* concurrent ``backend.generate`` → ResponseAggregator → DoneEvent,
* per-backend errors do not abort the run; aggregator filters them,
* ``trace.record_response`` is called once per alias (success or error),
* defensive type-mismatch (TokenAggregator passed in) raises loudly,
* P1 majority_vote / concat metadata round-trips through the runner,
* ``elapsed_ms`` is per-alias, not inherited from a slow predecessor,
* ``GenerationParams`` is a fresh dict per submit (no cross-thread sharing).
"""

from __future__ import annotations

import threading

import pytest

from core.workflow.nodes.parallel_ensemble.aggregators.response.concat import (
    ConcatAggregator,
    ConcatConfig,
)
from core.workflow.nodes.parallel_ensemble.aggregators.response.majority_vote import (
    MajorityVoteAggregator,
    MajorityVoteConfig,
)
from core.workflow.nodes.parallel_ensemble.aggregators.token.sum_score import (
    SumScoreAggregator,
    SumScoreConfig,
)
from core.workflow.nodes.parallel_ensemble.registry.runner_registry import (
    RunnerRegistry,
)
from core.workflow.nodes.parallel_ensemble.runners.response_level import (
    ResponseLevelConfig,
    ResponseLevelRunner,
)
from core.workflow.nodes.parallel_ensemble.spi.backend import GenerationResult
from core.workflow.nodes.parallel_ensemble.spi.capability import Capability
from core.workflow.nodes.parallel_ensemble.spi.trace import (
    DiagnosticsConfig,
    TraceCollector,
)

from .conftest import FakeBackend


def test_registered_with_response_scope():
    """Side-effect import wires runner into ``RunnerRegistry``."""
    cls = RunnerRegistry.get("response_level")
    assert cls is ResponseLevelRunner
    assert cls.aggregator_scope == "response"
    assert cls.required_capabilities == frozenset()
    assert cls.optional_capabilities == frozenset({Capability.STREAMING})
    assert cls.ui_schema == {}


def test_requirements_empty():
    """response_level imposes no config-derived requirements."""
    assert ResponseLevelRunner.requirements(ResponseLevelConfig()) == []


def test_validate_selection_too_few_models():
    """A single-model selection is not a valid ensemble."""
    issues = ResponseLevelRunner.validate_selection(
        ResponseLevelConfig(),
        ["only_one"],
        registry=None,  # type: ignore[arg-type]
    )
    assert len(issues) == 1
    assert issues[0]["severity"] == "error"
    assert "at least 2" in issues[0]["message"]
    assert issues[0]["i18n_key"] == "parallelEnsemble.errors.tooFewModels"


def test_validate_selection_two_models_ok():
    """Two aliases pass validation with no issues."""
    issues = ResponseLevelRunner.validate_selection(
        ResponseLevelConfig(),
        ["a", "b"],
        registry=None,  # type: ignore[arg-type]
    )
    assert issues == []


def test_response_level_runner_majority_vote_happy_path(executor):
    """3 backends voting "yes/yes/no" → MajorityVote picks "yes"."""
    backends = {
        "m1": FakeBackend(
            "m1",
            scripted_generate=GenerationResult(text="yes", finish_reason="stop", metadata={}),
        ),
        "m2": FakeBackend(
            "m2",
            scripted_generate=GenerationResult(text="yes", finish_reason="stop", metadata={}),
        ),
        "m3": FakeBackend(
            "m3",
            scripted_generate=GenerationResult(text="no", finish_reason="stop", metadata={}),
        ),
    }
    runner = ResponseLevelRunner(executor=executor, aggregator_config=MajorityVoteConfig())
    trace = TraceCollector(DiagnosticsConfig())

    events = list(
        runner.run(
            question="is the sky blue?",
            backends=backends,
            aggregator=MajorityVoteAggregator(),
            config=ResponseLevelConfig(),
            trace=trace,
        )
    )

    assert [e["kind"] for e in events] == ["done"]
    done = events[0]
    assert done["text"] == "yes"  # type: ignore[typeddict-item]
    metadata = done["metadata"]  # type: ignore[typeddict-item]
    assert metadata["strategy"] == "majority_vote"
    assert metadata["votes"] == {"yes": 2, "no": 1}
    assert metadata["winner_votes"] == 2
    assert metadata["tie_break_applied"] is False
    assert metadata["contributions"] == {"m1": "yes", "m2": "yes", "m3": "no"}


def test_response_level_runner_concat(executor):
    """Concat aggregator joins responses with the configured separator."""
    backends = {
        "m1": FakeBackend(
            "m1",
            scripted_generate=GenerationResult(text="alpha", finish_reason="stop", metadata={}),
        ),
        "m2": FakeBackend(
            "m2",
            scripted_generate=GenerationResult(text="beta", finish_reason="stop", metadata={}),
        ),
    }
    runner = ResponseLevelRunner(executor=executor, aggregator_config=ConcatConfig(separator=" || "))
    trace = TraceCollector(DiagnosticsConfig())

    events = list(
        runner.run(
            question="q",
            backends=backends,
            aggregator=ConcatAggregator(),
            config=ResponseLevelConfig(),
            trace=trace,
        )
    )

    done = events[0]
    # Concat aggregator preserves input order from signals; signal order
    # mirrors backends-dict iteration (insertion order under py3.7+).
    assert done["text"] == "alpha || beta"  # type: ignore[typeddict-item]
    assert done["metadata"]["strategy"] == "concat"  # type: ignore[typeddict-item]


def test_response_level_per_backend_error_does_not_abort(executor):
    """One backend raising must not kill the run; survivors still vote."""

    class ExplodingBackend(FakeBackend):
        def generate(self, prompt, params):
            raise RuntimeError("boom")

    backends = {
        "m1": FakeBackend(
            "m1",
            scripted_generate=GenerationResult(text="yes", finish_reason="stop", metadata={}),
        ),
        "m2": FakeBackend(
            "m2",
            scripted_generate=GenerationResult(text="yes", finish_reason="stop", metadata={}),
        ),
        "explodes": ExplodingBackend("explodes"),
    }
    runner = ResponseLevelRunner(executor=executor, aggregator_config=MajorityVoteConfig())
    diagnostics = DiagnosticsConfig(include_model_outputs=True, include_per_backend_errors=True)
    trace = TraceCollector(diagnostics)

    events = list(
        runner.run(
            question="q",
            backends=backends,
            aggregator=MajorityVoteAggregator(),
            config=ResponseLevelConfig(),
            trace=trace,
        )
    )

    done = events[0]
    assert done["text"] == "yes"  # type: ignore[typeddict-item]
    # Only the two healthy backends counted; aggregator filters errors.
    assert done["metadata"]["votes"] == {"yes": 2}  # type: ignore[typeddict-item]

    # Trace got one entry per alias including the error path.
    final = trace.finalize(
        runner_name="response_level",
        runner_config={},
        aggregator_name="majority_vote",
        aggregator_config={},
        backends=[],
    )
    by_alias = {e["source_id"]: e for e in final["response_trace"]}
    assert set(by_alias) == {"m1", "m2", "explodes"}
    assert by_alias["explodes"]["finish_reason"] == "error"
    assert "RuntimeError" in (by_alias["explodes"].get("error") or "")
    # Trace summary records the runner-level error count.
    assert final["summary"]["error_count"] == 1
    assert final["summary"]["backend_count"] == 3


def test_response_level_all_errored_returns_empty(executor):
    """All backends fail → MajorityVote's empty-fallback gives text=''."""

    class Boom(FakeBackend):
        def generate(self, prompt, params):
            raise RuntimeError("nope")

    backends = {
        "m1": Boom("m1"),
        "m2": Boom("m2"),
    }
    runner = ResponseLevelRunner(executor=executor, aggregator_config=MajorityVoteConfig())
    trace = TraceCollector(DiagnosticsConfig())
    events = list(
        runner.run(
            question="q",
            backends=backends,
            aggregator=MajorityVoteAggregator(),
            config=ResponseLevelConfig(),
            trace=trace,
        )
    )
    done = events[0]
    assert done["text"] == ""  # type: ignore[typeddict-item]
    assert done["metadata"]["winner_votes"] == 0  # type: ignore[typeddict-item]
    assert done["metadata"]["votes"] == {}  # type: ignore[typeddict-item]


def test_response_level_records_response_per_backend(executor):
    """``trace.record_response`` is called once per alias on the happy path."""
    backends = {
        "m1": FakeBackend(
            "m1",
            scripted_generate=GenerationResult(text="x", finish_reason="stop", metadata={}),
        ),
        "m2": FakeBackend(
            "m2",
            scripted_generate=GenerationResult(text="x", finish_reason="length", metadata={}),
        ),
    }
    runner = ResponseLevelRunner(executor=executor, aggregator_config=MajorityVoteConfig())
    trace = TraceCollector(DiagnosticsConfig())
    list(
        runner.run(
            question="q",
            backends=backends,
            aggregator=MajorityVoteAggregator(),
            config=ResponseLevelConfig(),
            trace=trace,
        )
    )
    final = trace.finalize(
        runner_name="response_level",
        runner_config={},
        aggregator_name="majority_vote",
        aggregator_config={},
        backends=[],
    )
    by_alias = {e["source_id"]: e for e in final["response_trace"]}
    assert set(by_alias) == {"m1", "m2"}
    # ``finish_reason`` propagates straight from the GenerationResult.
    assert by_alias["m1"]["finish_reason"] == "stop"
    assert by_alias["m2"]["finish_reason"] == "length"
    # No errors on either alias.
    assert by_alias["m1"].get("error") is None
    assert by_alias["m2"].get("error") is None


def test_response_level_tokens_count_from_metadata(executor):
    """Backend metadata tokens_count flows into the trace; non-int → 0."""
    backends = {
        "good": FakeBackend(
            "good",
            scripted_generate=GenerationResult(text="t", finish_reason="stop", metadata={"tokens_count": 42}),
        ),
        "predicted": FakeBackend(
            "predicted",
            scripted_generate=GenerationResult(text="t", finish_reason="stop", metadata={"tokens_predicted": 17}),
        ),
        "bogus": FakeBackend(
            "bogus",
            scripted_generate=GenerationResult(text="t", finish_reason="stop", metadata={"tokens_count": "many"}),
        ),
    }
    runner = ResponseLevelRunner(executor=executor, aggregator_config=ConcatConfig())
    trace = TraceCollector(DiagnosticsConfig())
    list(
        runner.run(
            question="q",
            backends=backends,
            aggregator=ConcatAggregator(),
            config=ResponseLevelConfig(),
            trace=trace,
        )
    )
    final = trace.finalize(
        runner_name="response_level",
        runner_config={},
        aggregator_name="concat",
        aggregator_config={},
        backends=[],
    )
    by_alias = {e["source_id"]: e for e in final["response_trace"]}
    assert by_alias["good"]["tokens_count"] == 42
    assert by_alias["predicted"]["tokens_count"] == 17
    # Non-int defaults to 0 rather than crashing record_response.
    assert by_alias["bogus"]["tokens_count"] == 0


def test_run_rejects_token_aggregator(executor):
    """Defensive: token-scope aggregator handed in by mistake fails loud."""
    runner = ResponseLevelRunner(executor=executor, aggregator_config=SumScoreConfig())
    trace = TraceCollector(DiagnosticsConfig())
    backends = {"m1": FakeBackend("m1"), "m2": FakeBackend("m2")}
    with pytest.raises(TypeError, match="ResponseAggregator"):
        list(
            runner.run(
                question="q",
                backends=backends,
                aggregator=SumScoreAggregator(),  # wrong scope
                config=ResponseLevelConfig(),
                trace=trace,
            )
        )


def test_response_level_emits_single_done_event(executor):
    """Non-streaming runner: exactly one DoneEvent, no TokenEvents."""
    backends = {
        "m1": FakeBackend(
            "m1",
            scripted_generate=GenerationResult(text="ok", finish_reason="stop", metadata={}),
        ),
        "m2": FakeBackend(
            "m2",
            scripted_generate=GenerationResult(text="ok", finish_reason="stop", metadata={}),
        ),
    }
    runner = ResponseLevelRunner(executor=executor, aggregator_config=MajorityVoteConfig())
    trace = TraceCollector(DiagnosticsConfig())
    events = list(
        runner.run(
            question="q",
            backends=backends,
            aggregator=MajorityVoteAggregator(),
            config=ResponseLevelConfig(),
            trace=trace,
        )
    )
    assert len(events) == 1
    assert events[0]["kind"] == "done"


def test_response_level_elapsed_ms_independent_per_alias(executor):
    """Each alias's ``elapsed_ms`` reflects its own backend duration.

    Regression for the iteration-order timing bug: with insertion-order
    iteration and ``elapsed_ms`` snapshotted before ``future.result()``,
    a fast backend submitted *behind* a slow one would inherit the
    slow's wait time instead of recording its own near-zero duration.
    The fix uses ``as_completed`` so each future is timed when it
    actually finishes; this test pins that behaviour.
    """
    slow_release = threading.Event()

    class SlowBackend(FakeBackend):
        def generate(self, prompt, params):
            # Hold up so the fast backend completes first under
            # max_workers=2; release after a measurable delay so the
            # slow alias's elapsed_ms is meaningfully larger than the
            # fast alias's.
            assert slow_release.wait(timeout=5)
            return GenerationResult(text="slow", finish_reason="stop", metadata={})

    class FastBackend(FakeBackend):
        def generate(self, prompt, params):
            return GenerationResult(text="fast", finish_reason="stop", metadata={})

    backends = {
        "slow": SlowBackend("slow"),  # submitted first, finishes last
        "fast": FastBackend("fast"),  # submitted second, finishes first
    }
    runner = ResponseLevelRunner(executor=executor, aggregator_config=ConcatConfig())
    trace = TraceCollector(DiagnosticsConfig(include_response_timings=True))

    # Release the slow backend after a small delay so the fast one
    # has time to complete and be picked up by ``as_completed``.
    threading.Timer(0.10, slow_release.set).start()

    list(
        runner.run(
            question="q",
            backends=backends,
            aggregator=ConcatAggregator(),
            config=ResponseLevelConfig(),
            trace=trace,
        )
    )
    final = trace.finalize(
        runner_name="response_level",
        runner_config={},
        aggregator_name="concat",
        aggregator_config={},
        backends=[],
    )
    by_alias = {e["source_id"]: e for e in final["response_trace"]}
    fast_ms = by_alias["fast"]["elapsed_ms"]
    slow_ms = by_alias["slow"]["elapsed_ms"]

    # Old behaviour would have recorded fast_ms ≈ slow_ms (≥ 100 ms),
    # because the for-loop snapshot happened after the slow future's
    # blocking ``result()`` had already burned the wall clock. Under
    # ``as_completed`` the fast future is observed first and its
    # snapshot is small relative to the slow one.
    assert fast_ms < slow_ms - 30, (
        f"fast elapsed_ms ({fast_ms} ms) inherited slow's wait ({slow_ms} ms) — iteration-order timing regression"
    )


def test_response_level_fresh_params_per_submit(executor):
    """Each ``backend.generate`` gets its own ``GenerationParams`` dict.

    A backend that mutates its params (the SPI does not forbid this)
    must not be able to leak state across the concurrent calls.
    Asserted by object identity rather than value: distinct dict
    instances guarantee no cross-thread aliasing regardless of what
    any one backend does to its own copy.
    """
    backends = {
        "m1": FakeBackend(
            "m1",
            scripted_generate=GenerationResult(text="x", finish_reason="stop", metadata={}),
        ),
        "m2": FakeBackend(
            "m2",
            scripted_generate=GenerationResult(text="x", finish_reason="stop", metadata={}),
        ),
        "m3": FakeBackend(
            "m3",
            scripted_generate=GenerationResult(text="x", finish_reason="stop", metadata={}),
        ),
    }
    runner = ResponseLevelRunner(executor=executor, aggregator_config=MajorityVoteConfig())
    trace = TraceCollector(DiagnosticsConfig())
    list(
        runner.run(
            question="q",
            backends=backends,
            aggregator=MajorityVoteAggregator(),
            config=ResponseLevelConfig(),
            trace=trace,
        )
    )

    # ``FakeBackend.generate`` records ``(prompt, params)`` — pull the
    # params object identity for each backend's first (only) call.
    p1 = backends["m1"].generate_calls[0][1]
    p2 = backends["m2"].generate_calls[0][1]
    p3 = backends["m3"].generate_calls[0][1]
    assert p1 is not p2
    assert p2 is not p3
    assert p1 is not p3
