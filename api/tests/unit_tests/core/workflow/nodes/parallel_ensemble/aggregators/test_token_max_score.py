"""Token-scope ``max_score`` aggregator — picks the single highest
weighted prob (no summing). Lex tie-break on token, alias tie-break
within a single token, both deterministic.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.workflow.nodes.parallel_ensemble.aggregators.token.max_score import (
    MaxScoreAggregator,
    MaxScoreConfig,
)
from core.workflow.nodes.parallel_ensemble.registry.aggregator_registry import (
    AggregatorRegistry,
)


def test_registered_under_token_scope():
    cls = AggregatorRegistry.get("max_score")
    assert cls is MaxScoreAggregator
    assert cls.scope == "token"


def test_max_single_score_wins(make_ctx, cand):
    """Token whose single most confident vote is highest wins, even if
    the cumulative sum across backends would favour something else."""
    signals = {
        "per_model": {
            # 'foo' summed = 1.4, but no single model gives it > 0.7;
            # 'bar' summed = 0.85, but m2 gives 0.85 in one shot.
            "m1": [cand("foo", 0.7), cand("bar", 0.0)],
            "m2": [cand("foo", 0.7), cand("bar", 0.85)],
        },
        "per_model_errors": {},
    }
    agg = MaxScoreAggregator()
    pick = agg.aggregate(signals, make_ctx(), MaxScoreConfig(use_weights=False))
    assert pick["token"] == "bar"
    assert pick["score"] == pytest.approx(0.85)
    assert pick["reasoning"]["winner_alias"] == "m2"


def test_lex_tie_break_on_token(make_ctx, cand):
    signals = {
        "per_model": {
            "m1": [cand("zebra", 0.9)],
            "m2": [cand("apple", 0.9)],
        },
        "per_model_errors": {},
    }
    agg = MaxScoreAggregator()
    pick = agg.aggregate(signals, make_ctx(), MaxScoreConfig(use_weights=False))
    assert pick["token"] == "apple"
    assert pick["reasoning"]["tie_break_applied"] is True


def test_lex_tie_break_on_alias_within_token(make_ctx, cand):
    """Same token, two backends produce identical max → lex-smallest
    alias is recorded as ``winner_alias`` for deterministic provenance."""
    signals = {
        "per_model": {
            "zeta": [cand("hi", 0.9)],
            "alpha": [cand("hi", 0.9)],
        },
        "per_model_errors": {},
    }
    agg = MaxScoreAggregator()
    pick = agg.aggregate(signals, make_ctx(), MaxScoreConfig(use_weights=False))
    assert pick["token"] == "hi"
    assert pick["reasoning"]["winner_alias"] == "alpha"


def test_weighted(make_ctx, cand):
    signals = {
        "per_model": {
            "m1": [cand("foo", 0.5)],
            "m2": [cand("bar", 0.4)],
        },
        "per_model_errors": {},
    }
    ctx = make_ctx(weights={"m1": 1.0, "m2": 2.0})  # bar => 0.8
    agg = MaxScoreAggregator()
    pick = agg.aggregate(signals, ctx, MaxScoreConfig(use_weights=True))
    assert pick["token"] == "bar"
    assert pick["score"] == pytest.approx(0.8)


def test_all_voters_empty(make_ctx):
    signals = {"per_model": {}, "per_model_errors": {"m1": "boom"}}
    agg = MaxScoreAggregator()
    pick = agg.aggregate(signals, make_ctx(), MaxScoreConfig())
    assert pick["token"] == ""
    assert pick["reasoning"]["all_voters_empty"] is True


def test_skip_empty_voters_false_surfaces_errors(make_ctx, cand):
    signals = {
        "per_model": {"m1": [cand("hi", 0.5)]},
        "per_model_errors": {"m2": "timeout"},
    }
    agg = MaxScoreAggregator()
    pick = agg.aggregate(signals, make_ctx(), MaxScoreConfig(skip_empty_voters=False))
    assert pick["reasoning"]["empty_voters"] == ["m2"]


def test_config_rejects_extra_fields():
    with pytest.raises(ValidationError):
        MaxScoreConfig.model_validate({"foo": 1})
