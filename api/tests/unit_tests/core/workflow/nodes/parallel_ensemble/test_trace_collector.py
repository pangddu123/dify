"""P2.7 ``TraceCollector`` — diagnostics-gating + last-N truncation.

The collector is the only object runner / aggregator code touches at
runtime; ``DiagnosticsConfig`` flags decide what gets persisted. These
tests pin two contracts the runner / aggregator code relies on:

* ``record_*`` is effectively a no-op when the corresponding flag is
  off — the runner never has to branch on diagnostics flags, the
  collector silently drops the field at record time;
* ``record_token_step`` enforces ``max_trace_tokens`` by dropping the
  oldest entries (last-N retention) and stamps ``summary["truncated"]``
  on finalize so consumers can tell when data is missing.

A third group covers the response / think gating contracts so
``DiagnosticsConfig`` redaction stays coherent across all three
``record_*`` channels — runner code that flips a single config flag
should see consistent behaviour regardless of which trace channel is
involved.
"""

from __future__ import annotations

from core.workflow.nodes.parallel_ensemble.spi.trace import (
    DiagnosticsConfig,
    ResponseTraceEntry,
    ThinkTraceEntry,
    TokenStepTraceEntry,
    TraceCollector,
)


def _token_entry(step: int) -> TokenStepTraceEntry:
    """Build a fully populated token-step entry for redaction probes."""
    return {
        "step": step,
        "selected_token": f"t{step}",
        "selected_score": 0.5,
        "elapsed_ms": 1,
        "per_model": {"m1": [{"token": f"t{step}", "prob": 0.5, "logit": None}]},
        "per_model_errors": {"m2": "boom"},
        "aggregator_reasoning": {"per_token_score": {f"t{step}": 0.5}},
    }


# ── Diagnostics gating: token channel ─────────────────────────────────


def test_trace_collector_no_op_when_token_candidates_disabled():
    """``include_token_candidates=False`` → ``per_model`` /
    ``aggregator_reasoning`` / ``per_model_errors`` are dropped at
    record time, so the finalised ``token_trace`` carries only the
    lightweight always-on fields. Runner code never had to check the
    flag — it called ``record_token_step`` blindly with the full entry."""
    cfg = DiagnosticsConfig(
        include_token_candidates=False,
        include_per_backend_errors=False,
        include_aggregator_reasoning=False,
    )
    collector = TraceCollector(cfg)
    collector.record_token_step(_token_entry(0))
    collector.record_token_step(_token_entry(1))

    final = collector.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    assert len(final["token_trace"]) == 2
    for row in final["token_trace"]:
        # Lightweight fields always survive.
        assert row["step"] in (0, 1)
        assert row["selected_token"].startswith("t")
        assert row["selected_score"] == 0.5
        assert row["elapsed_ms"] == 1
        # Heavy / opt-in fields are dropped.
        assert "per_model" not in row
        assert "per_model_errors" not in row
        assert "aggregator_reasoning" not in row


def test_trace_collector_keeps_token_candidates_when_enabled():
    """Sanity flip-side: with the flags on, the heavy fields land in the trace."""
    cfg = DiagnosticsConfig(
        include_token_candidates=True,
        include_per_backend_errors=True,
        include_aggregator_reasoning=True,
    )
    collector = TraceCollector(cfg)
    collector.record_token_step(_token_entry(0))

    final = collector.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    row = final["token_trace"][0]
    assert "per_model" in row
    assert "per_model_errors" in row
    assert "aggregator_reasoning" in row
    assert row["per_model"]["m1"][0]["token"] == "t0"


# ── Diagnostics gating: response channel ──────────────────────────────


def test_trace_collector_response_redaction():
    """``include_model_outputs=False`` redacts ``text``;
    ``include_per_backend_errors=False`` redacts ``error``. Lightweight
    timing / id fields always survive so the panel can still show
    "this backend ran for 99 ms" without leaking the body."""
    cfg = DiagnosticsConfig(
        include_model_outputs=False,
        include_per_backend_errors=False,
    )
    collector = TraceCollector(cfg)
    entry: ResponseTraceEntry = {
        "source_id": "m1",
        "text": "secret",
        "finish_reason": "stop",
        "tokens_count": 5,
        "elapsed_ms": 99,
        "error": "boom",
    }
    collector.record_response(entry)
    final = collector.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    row = final["response_trace"][0]
    assert row["source_id"] == "m1"
    assert row["finish_reason"] == "stop"
    assert row["tokens_count"] == 5
    assert row["elapsed_ms"] == 99
    assert "text" not in row
    assert "error" not in row


# ── Diagnostics gating: think channel ─────────────────────────────────


def test_trace_collector_think_gated_off_is_no_op():
    """``include_think_trace=False`` → ``record_think`` drops the entry
    entirely (chain-of-thought is sensitive enough that the redaction
    is all-or-nothing rather than per-field)."""
    cfg = DiagnosticsConfig(include_think_trace=False)
    collector = TraceCollector(cfg)
    collector.record_think(ThinkTraceEntry(source_id="t1", think_text="secret thinking", elapsed_ms=99))
    final = collector.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    assert final["think_trace"] == []


def test_trace_collector_think_kept_when_enabled():
    cfg = DiagnosticsConfig(include_think_trace=True)
    collector = TraceCollector(cfg)
    collector.record_think(ThinkTraceEntry(source_id="t1", think_text="reasoning", elapsed_ms=42))
    final = collector.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    assert final["think_trace"] == [{"source_id": "t1", "think_text": "reasoning", "elapsed_ms": 42}]


# ── max_trace_tokens last-N truncation ────────────────────────────────


def test_trace_collector_truncation():
    """Recording > max_trace_tokens drops the oldest entries (last-N)
    and stamps ``summary['truncated'] = True`` on finalize so consumers
    know data was dropped. Last-N rather than first-N because token-step
    failures usually surface at the *tail* of a generation, where the
    model has wandered furthest from the prompt."""
    cfg = DiagnosticsConfig(max_trace_tokens=3)
    collector = TraceCollector(cfg)
    for i in range(7):
        collector.record_token_step(_token_entry(i))

    final = collector.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    kept_steps = [row["step"] for row in final["token_trace"]]
    assert kept_steps == [4, 5, 6]
    assert final["summary"]["truncated"] is True
    assert final["summary"]["truncated_token_steps"] == 4


def test_trace_collector_no_truncation_marker_under_cap():
    """Recording ≤ ``max_trace_tokens`` does *not* stamp the truncated
    marker, so panel consumers can rely on its presence as a real
    "data was dropped" signal rather than a free-floating boolean."""
    cfg = DiagnosticsConfig(max_trace_tokens=10)
    collector = TraceCollector(cfg)
    for i in range(3):
        collector.record_token_step(_token_entry(i))

    final = collector.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    assert "truncated" not in final["summary"]
    assert "truncated_token_steps" not in final["summary"]


def test_trace_collector_truncation_preserves_field_redaction():
    """Truncation interacts correctly with field redaction: dropped
    rows shouldn't somehow re-introduce the heavy fields on the kept
    rows. Pin the joint behaviour so a future refactor of either path
    cannot silently break the other."""
    cfg = DiagnosticsConfig(
        max_trace_tokens=2,
        include_token_candidates=False,
        include_per_backend_errors=False,
        include_aggregator_reasoning=False,
    )
    collector = TraceCollector(cfg)
    for i in range(5):
        collector.record_token_step(_token_entry(i))

    final = collector.finalize(
        runner_name="r",
        runner_config={},
        aggregator_name="a",
        aggregator_config={},
        backends=[],
    )
    assert [row["step"] for row in final["token_trace"]] == [3, 4]
    for row in final["token_trace"]:
        assert "per_model" not in row
        assert "per_model_errors" not in row
        assert "aggregator_reasoning" not in row
    assert final["summary"]["truncated"] is True
    assert final["summary"]["truncated_token_steps"] == 3
