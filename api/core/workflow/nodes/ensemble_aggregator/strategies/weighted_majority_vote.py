"""Weighted-majority response strategy — fully weight-driven (ADR-v3-9).

Unlike ``majority_vote`` (which collapses to plain count when every
weight is 1.0), this strategy ALWAYS sums weights and reports the
weighted score, even when weights are unit. Useful as a v0.2 SPI
extension example: ships entirely from public ``ResponseAggregator``
+ ``SourceAggregationContext`` surfaces, no patches to the framework.

Tie-break is identical to ``majority_vote`` (lex-smallest voter
source_id) so DSLs that swap one strategy for the other observe the
same winner under tied scores.
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


class _WeightedMajorityVoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register("weighted_majority_vote")
class WeightedMajorityVoteStrategy(ResponseAggregator[_WeightedMajorityVoteConfig]):
    config_class: ClassVar[type[BaseModel]] = _WeightedMajorityVoteConfig
    i18n_key_prefix: ClassVar[str] = "nodes.ensembleAggregator.weightedMajorityVote"
    ui_schema: ClassVar[dict] = {}

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: SourceAggregationContext,
        config: _WeightedMajorityVoteConfig,
    ) -> ResponseAggregationResult:
        weights = context.weights

        scores: dict[str, float] = defaultdict(float)
        for s in signals:
            w = weights.get(s["source_id"], 1.0)
            scores[s["text"]] += w

        max_score = max(scores.values())
        tied_texts = [t for t, c in scores.items() if math.isclose(c, max_score)]

        if len(tied_texts) == 1:
            winner = tied_texts[0]
        else:
            earliest_voter: dict[str, str] = {}
            for s in signals:
                text = s["text"]
                if text in tied_texts:
                    sid = s["source_id"]
                    if text not in earliest_voter or sid < earliest_voter[text]:
                        earliest_voter[text] = sid
            winner = min(tied_texts, key=lambda t: earliest_voter[t])

        contributions: dict[str, str] = {s["source_id"]: s["text"] for s in signals}

        return {
            "text": winner,
            "metadata": {
                "strategy": "weighted_majority_vote",
                "scores": dict(scores),
                "winner_score": max_score,
                "tie_break_applied": len(tied_texts) > 1,
                "contributions": contributions,
                "weights": dict(weights),
            },
        }
