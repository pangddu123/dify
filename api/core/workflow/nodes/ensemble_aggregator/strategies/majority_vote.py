"""Majority-vote response strategy.

v3 upgrade: when ``context.weights`` is non-trivial (any source carries
weight ≠ 1.0), the strategy switches to weighted-vote tallying — same
shape as the v2.4 plain count, but each source contributes its weight
instead of a fixed 1. With unit weights everywhere the output matches
v2.4 exactly.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from .base import (
    ResponseAggregationResult,
    ResponseAggregator,
    ResponseSignal,
    SourceAggregationContext,
)
from .registry import register


class _MajorityVoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register("majority_vote")
class MajorityVoteStrategy(ResponseAggregator[_MajorityVoteConfig]):
    """Pick the most-voted text; lex tie-break on lex-smallest source_id.

    With unit weights this is the v2.4 majority count. With non-unit
    weights it's a weighted majority — equivalent to unit when all
    weights collapse to the same number, so DSLs that don't set
    ``weight`` keep the v2.4 output verbatim.
    """

    config_class: ClassVar[type[BaseModel]] = _MajorityVoteConfig
    i18n_key_prefix: ClassVar[str] = "nodes.ensembleAggregator.majorityVote"
    ui_schema: ClassVar[dict] = {}

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: SourceAggregationContext,
        config: _MajorityVoteConfig,
    ) -> ResponseAggregationResult:
        weights = context.weights

        weighted_votes: dict[str, float] = defaultdict(float)
        for s in signals:
            w = weights.get(s["source_id"], 1.0)
            weighted_votes[s["text"]] += w

        max_score = max(weighted_votes.values())
        tied_texts = [t for t, c in weighted_votes.items() if math.isclose(c, max_score)]

        if len(tied_texts) == 1:
            winner = tied_texts[0]
        else:
            # Tie-break by lexicographically-smallest voting source_id; keeps
            # output deterministic regardless of input order. Same algorithm
            # as v2.4 — preserved 1:1 because DSL tests pinned down winners
            # under specific tie configurations.
            earliest_voter: dict[str, str] = {}
            for s in signals:
                text = s["text"]
                if text in tied_texts:
                    sid = s["source_id"]
                    if text not in earliest_voter or sid < earliest_voter[text]:
                        earliest_voter[text] = sid
            winner = min(tied_texts, key=lambda t: earliest_voter[t])

        contributions: dict[str, str] = {s["source_id"]: s["text"] for s in signals}

        # ``votes`` payload: when weights are all 1.0, this matches v2.4
        # integer counts exactly; otherwise it's the weighted sum per
        # text. Frontend metadata viewers handle both shapes (number).
        all_unit = all(math.isclose(weights.get(s["source_id"], 1.0), 1.0) for s in signals)
        votes_payload: dict[str, float] = {
            t: (int(c) if all_unit else c) for t, c in weighted_votes.items()
        }

        return {
            "text": winner,
            "metadata": {
                "strategy": "majority_vote",
                "votes": votes_payload,
                "winner_votes": int(max_score) if all_unit else max_score,
                "tie_break_applied": len(tied_texts) > 1,
                "contributions": contributions,
                "weighted": not all_unit,
            },
        }
