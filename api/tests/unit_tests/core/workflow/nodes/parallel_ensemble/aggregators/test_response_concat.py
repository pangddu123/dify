"""Response-scope ``concat`` aggregator (P2.5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.workflow.nodes.parallel_ensemble.aggregators.response.concat import (
    ConcatAggregator,
    ConcatConfig,
)
from core.workflow.nodes.parallel_ensemble.registry.aggregator_registry import (
    AggregatorRegistry,
)
from core.workflow.nodes.parallel_ensemble.spi import ResponseSignal


def _signal(sid: str, text: str, error: str | None = None) -> ResponseSignal:
    return {
        "source_id": sid,
        "text": text,
        "finish_reason": "stop",
        "elapsed_ms": 1,
        "error": error,
    }


def test_registered_under_response_scope():
    cls = AggregatorRegistry.get("concat")
    assert cls is ConcatAggregator
    assert cls.scope == "response"


def test_default_separator(make_ctx):
    agg = ConcatAggregator()
    result = agg.aggregate(
        [_signal("a", "first"), _signal("b", "second")],
        make_ctx(),
        ConcatConfig(),
    )
    assert result["text"] == "first\n\n---\n\nsecond"
    assert result["metadata"]["strategy"] == "concat"


def test_custom_separator(make_ctx):
    agg = ConcatAggregator()
    result = agg.aggregate(
        [_signal("a", "x"), _signal("b", "y")],
        make_ctx(),
        ConcatConfig(separator=" | "),
    )
    assert result["text"] == "x | y"


def test_source_label(make_ctx):
    agg = ConcatAggregator()
    result = agg.aggregate(
        [_signal("a", "hi"), _signal("b", "bye")],
        make_ctx(),
        ConcatConfig(separator="\n", include_source_label=True),
    )
    assert result["text"] == "[a]\nhi\n[b]\nbye"


def test_errored_skipped(make_ctx):
    agg = ConcatAggregator()
    result = agg.aggregate(
        [_signal("a", "ok"), _signal("b", "fail", error="boom")],
        make_ctx(),
        ConcatConfig(separator=" | "),
    )
    assert result["text"] == "ok"
    assert "b" not in result["metadata"]["contributions"]


def test_config_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ConcatConfig.model_validate({"separator": "x", "unknown": True})
