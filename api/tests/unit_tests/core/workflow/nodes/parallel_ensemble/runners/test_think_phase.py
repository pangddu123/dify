"""``ThinkPhaseRunner`` — covers think-only dispatch + suffix shape."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from core.workflow.nodes.parallel_ensemble.runners.think_phase import (
    ThinkPhaseRunner,
)
from core.workflow.nodes.parallel_ensemble.spi.backend import GenerationResult
from core.workflow.nodes.parallel_ensemble.spi.trace import (
    DiagnosticsConfig,
    TraceCollector,
)

from .conftest import FakeBackend


def test_only_think_models_dispatched(executor):
    """Non-think backend gets no ``generate`` call; think backend does."""
    normal = FakeBackend(
        "n1",
        scripted_generate=GenerationResult(text="UNUSED", finish_reason="stop", metadata={}),
        spec_type="normal",
    )
    thinker = FakeBackend(
        "t1",
        scripted_generate=GenerationResult(text="step-by-step reasoning", finish_reason="stop", metadata={}),
        spec_type="think",
        stop_think="</think>",
    )
    trace = TraceCollector(DiagnosticsConfig(include_think_trace=True))
    suffixes = ThinkPhaseRunner(executor).run(
        prompts={"n1": "PROMPT_N", "t1": "PROMPT_T"},
        backends={"n1": normal, "t1": thinker},
        trace=trace,
    )
    assert "n1" not in suffixes
    assert suffixes == {"t1": "step-by-step reasoning</think>"}
    # Confirm dispatch parity: normal backend not invoked.
    assert normal.generate_calls == []
    assert len(thinker.generate_calls) == 1
    prompt, params = thinker.generate_calls[0]
    assert prompt == "PROMPT_T"
    assert params["stop"] == ["</think>"]
    assert params["max_tokens"] == 8196


def test_no_think_models_returns_empty(executor):
    """Selection without think models is a no-op (empty dict, no failures)."""
    backends = {"a": FakeBackend("a"), "b": FakeBackend("b")}
    trace = TraceCollector(DiagnosticsConfig())
    assert ThinkPhaseRunner(executor).run({"a": "x", "b": "y"}, backends, trace) == {}


def test_failure_suffix_is_empty(executor):
    """Failing think backend yields ``""`` suffix and is logged via trace."""

    class _BoomBackend(FakeBackend):
        def generate(self, prompt, params):  # type: ignore[override]
            raise RuntimeError("bad gateway")

    thinker = _BoomBackend("t1", spec_type="think", stop_think="</think>")
    trace = TraceCollector(DiagnosticsConfig(include_think_trace=True))
    suffixes = ThinkPhaseRunner(executor).run({"t1": "P"}, {"t1": thinker}, trace)
    assert suffixes == {"t1": ""}
    final = trace.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    assert final["think_trace"][0]["source_id"] == "t1"
    assert final["think_trace"][0]["think_text"] == ""


def test_think_trace_gated_off(executor):
    """``include_think_trace=False`` → trace finalises with empty think_trace."""
    thinker = FakeBackend(
        "t1",
        scripted_generate=GenerationResult(text="thoughts", finish_reason="stop", metadata={}),
        spec_type="think",
        stop_think="</think>",
    )
    trace = TraceCollector(DiagnosticsConfig(include_think_trace=False))
    ThinkPhaseRunner(executor).run({"t1": "P"}, {"t1": thinker}, trace)
    final = trace.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    assert final["think_trace"] == []


def test_executor_owned_externally():
    """Module does not construct its own pool — caller owns lifecycle."""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        runner = ThinkPhaseRunner(pool)
        assert runner._executor is pool
    finally:
        pool.shutdown(wait=True)
