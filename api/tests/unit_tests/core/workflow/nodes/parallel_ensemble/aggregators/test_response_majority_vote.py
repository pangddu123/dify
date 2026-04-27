"""Response-scope ``majority_vote`` aggregator (P2.5).

Mirrors P1's `ensemble_aggregator` MajorityVote tests so the migration
is verifiably behaviour-preserving.
"""

from __future__ import annotations

from core.workflow.nodes.parallel_ensemble.aggregators.response.majority_vote import (
    MajorityVoteAggregator,
    MajorityVoteConfig,
)
from core.workflow.nodes.parallel_ensemble.registry.aggregator_registry import (
    AggregatorRegistry,
)
from core.workflow.nodes.parallel_ensemble.spi import ResponseSignal


def _signal(source_id: str, text: str, error: str | None = None) -> ResponseSignal:
    return {
        "source_id": source_id,
        "text": text,
        "finish_reason": "stop",
        "elapsed_ms": 1,
        "error": error,
    }


def test_registered_under_response_scope():
    cls = AggregatorRegistry.get("majority_vote")
    assert cls is MajorityVoteAggregator
    assert cls.scope == "response"
    assert cls.name == "majority_vote"


def test_three_way_majority(make_ctx):
    agg = MajorityVoteAggregator()
    result = agg.aggregate(
        [_signal("s1", "A"), _signal("s2", "A"), _signal("s3", "B")],
        make_ctx(),
        MajorityVoteConfig(),
    )
    assert result["text"] == "A"
    md = result["metadata"]
    assert md["votes"] == {"A": 2, "B": 1}
    assert md["winner_votes"] == 2
    assert md["tie_break_applied"] is False
    assert md["contributions"] == {"s1": "A", "s2": "A", "s3": "B"}


def test_lex_tie_break_independent_of_input_order(make_ctx):
    agg = MajorityVoteAggregator()
    forward = agg.aggregate(
        [_signal("alice", "X"), _signal("bob", "Y")],
        make_ctx(),
        MajorityVoteConfig(),
    )
    reverse = agg.aggregate(
        [_signal("bob", "Y"), _signal("alice", "X")],
        make_ctx(),
        MajorityVoteConfig(),
    )
    assert forward["text"] == reverse["text"] == "X"
    assert forward["metadata"]["tie_break_applied"] is True


def test_errored_backend_does_not_vote(make_ctx):
    agg = MajorityVoteAggregator()
    result = agg.aggregate(
        [
            _signal("s1", "A"),
            _signal("s2", "B"),
            _signal("s3", "B", error="timeout"),
        ],
        make_ctx(),
        MajorityVoteConfig(),
    )
    # s3 errored, so its "B" vote does not count → tie 1-1, lex wins.
    assert result["text"] == "A"
    assert "s3" not in result["metadata"]["contributions"]
    assert result["metadata"]["votes"] == {"A": 1, "B": 1}


def test_all_errored_returns_empty(make_ctx):
    agg = MajorityVoteAggregator()
    result = agg.aggregate(
        [_signal("s1", "A", error="x"), _signal("s2", "B", error="y")],
        make_ctx(),
        MajorityVoteConfig(),
    )
    assert result["text"] == ""
    assert result["metadata"]["winner_votes"] == 0


def test_config_rejects_extra_fields():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MajorityVoteConfig.model_validate({"threshold": 2})
