"""Token-scope ``sum_score`` aggregator — covers the four behaviours
called out in TASKS.md P2.7:

* equivalence to PN.py ``calculate_scores``,
* lex tie-break determinism (no ``random.choice``),
* ``per_model_errors`` handling under both ``skip_empty_voters``
  settings,
* weight propagation from ``BackendAggregationContext``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.workflow.nodes.parallel_ensemble.aggregators.token.sum_score import (
    SumScoreAggregator,
    SumScoreConfig,
)
from core.workflow.nodes.parallel_ensemble.registry.aggregator_registry import (
    AggregatorRegistry,
)


def test_registered_under_token_scope():
    cls = AggregatorRegistry.get("sum_score")
    assert cls is SumScoreAggregator
    assert cls.scope == "token"


def test_pn_calculate_scores_equivalence(make_ctx, cand):
    """Equivalent to PN.py: sum probs across models, take argmax."""
    signals = {
        "per_model": {
            "m1": [cand("hello", 0.6), cand("world", 0.4)],
            "m2": [cand("hello", 0.5), cand("world", 0.5)],
        },
        "per_model_errors": {},
    }
    agg = SumScoreAggregator()
    pick = agg.aggregate(signals, make_ctx(), SumScoreConfig(use_weights=False))
    assert pick["token"] == "hello"
    assert pick["score"] == pytest.approx(1.1)
    assert pick["reasoning"]["per_token_score"] == {
        "hello": pytest.approx(1.1),
        "world": pytest.approx(0.9),
    }
    assert pick["reasoning"]["tie_break_applied"] is False


def test_deterministic_tie_break(make_ctx, cand):
    """Tie on score → lex-smallest token wins, every run."""
    signals = {
        "per_model": {
            "m1": [cand("zebra", 0.5), cand("apple", 0.5)],
            "m2": [cand("zebra", 0.5), cand("apple", 0.5)],
        },
        "per_model_errors": {},
    }
    agg = SumScoreAggregator()
    picks = [agg.aggregate(signals, make_ctx(), SumScoreConfig(use_weights=False)) for _ in range(5)]
    assert all(p["token"] == "apple" for p in picks)
    assert picks[0]["reasoning"]["tie_break_applied"] is True


def test_weighted_sum(make_ctx, cand):
    signals = {
        "per_model": {
            # m2 weighted 3× → its 'world' beats m1's 'hello'
            "m1": [cand("hello", 0.6)],
            "m2": [cand("world", 0.4)],
        },
        "per_model_errors": {},
    }
    ctx = make_ctx(weights={"m1": 1.0, "m2": 3.0})
    agg = SumScoreAggregator()
    pick = agg.aggregate(signals, ctx, SumScoreConfig(use_weights=True))
    assert pick["token"] == "world"
    assert pick["score"] == pytest.approx(1.2)


def test_skip_empty_voters_default_silent(make_ctx, cand):
    """``skip_empty_voters=True`` (default) does not surface error keys
    in reasoning — keeps the trace lean for the happy path."""
    signals = {
        "per_model": {"m1": [cand("hi", 1.0)]},
        "per_model_errors": {"m2": "timeout"},
    }
    agg = SumScoreAggregator()
    pick = agg.aggregate(signals, make_ctx(), SumScoreConfig())
    assert pick["token"] == "hi"
    assert "empty_voters" not in pick["reasoning"]


def test_skip_empty_voters_false_records_errors(make_ctx, cand):
    """``skip_empty_voters=False`` exposes which backends came up empty
    so the runner can drive a fallback."""
    signals = {
        "per_model": {"m1": [cand("hi", 1.0)]},
        "per_model_errors": {"m2": "timeout", "m3": "parse"},
    }
    agg = SumScoreAggregator()
    pick = agg.aggregate(signals, make_ctx(), SumScoreConfig(skip_empty_voters=False))
    assert pick["token"] == "hi"
    assert sorted(pick["reasoning"]["empty_voters"]) == ["m2", "m3"]


def test_all_voters_empty(make_ctx):
    signals = {
        "per_model": {},
        "per_model_errors": {"m1": "timeout", "m2": "timeout"},
    }
    agg = SumScoreAggregator()
    pick = agg.aggregate(signals, make_ctx(), SumScoreConfig())
    assert pick["token"] == ""
    assert pick["score"] == 0.0
    assert pick["reasoning"]["all_voters_empty"] is True


def test_use_weights_false_ignores_ctx_weights(make_ctx, cand):
    signals = {
        "per_model": {
            "m1": [cand("a", 0.4)],
            "m2": [cand("b", 0.5)],
        },
        "per_model_errors": {},
    }
    # m1 weight=1000 would otherwise dominate; use_weights=False ignores it.
    ctx = make_ctx(weights={"m1": 1000.0, "m2": 1.0})
    agg = SumScoreAggregator()
    pick = agg.aggregate(signals, ctx, SumScoreConfig(use_weights=False))
    assert pick["token"] == "b"


def test_config_rejects_extra_fields():
    with pytest.raises(ValidationError):
        SumScoreConfig.model_validate({"skip_empty_voters": True, "rogue": 1})
