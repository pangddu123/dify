"""Response-scope ``majority_vote`` aggregator (P2.5 v2.4 migration).

Behaviourally identical to the P1 ``ensemble_aggregator`` MajorityVote
strategy: tie-break is deterministic on the lex-smallest source_id of
the tied texts. The signature changes (``ResponseSignal`` instead of
``AggregationInput``, typed ``ResponseAggregationResult`` instead of
``dict``) but the metadata keys are preserved 1:1 so downstream nodes
that already keyed off ``votes`` / ``winner_votes`` /
``tie_break_applied`` / ``contributions`` keep working.
"""

from __future__ import annotations

from collections import Counter
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ...registry.aggregator_registry import register_aggregator
from ...spi.aggregator import (
    ResponseAggregationResult,
    ResponseAggregator,
    ResponseSignal,
    SourceAggregationContext,
)


class MajorityVoteConfig(BaseModel):
    """Empty config — majority vote has no tunables.

    ``extra="forbid"`` so a yaml typo (e.g. ``majority_vote: {threshold: 2}``)
    is rejected at startup instead of silently ignored.
    """

    model_config = ConfigDict(extra="forbid")


@register_aggregator("majority_vote", scope="response")
class MajorityVoteAggregator(ResponseAggregator[MajorityVoteConfig]):
    """Pick the response that the most backends agree on (lex tie-break)."""

    config_class: ClassVar[type[BaseModel]] = MajorityVoteConfig
    i18n_key_prefix: ClassVar[str] = "parallelEnsemble.aggregators.majorityVote"
    ui_schema: ClassVar[dict] = {}

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: SourceAggregationContext,
        config: MajorityVoteConfig,
    ) -> ResponseAggregationResult:
        # Errored backends contribute no vote — same as P1 strategy: P1
        # filtered them upstream in the node, here we filter in-place so
        # the aggregator stays runner-agnostic.
        valid = [s for s in signals if s.get("error") is None]
        if not valid:
            return {
                "text": "",
                "metadata": {
                    "strategy": "majority_vote",
                    "votes": {},
                    "winner_votes": 0,
                    "tie_break_applied": False,
                    "contributions": {},
                },
            }

        vote_count: Counter[str] = Counter(s["text"] for s in valid)
        max_votes = max(vote_count.values())
        tied_texts = [t for t, c in vote_count.items() if c == max_votes]

        if len(tied_texts) == 1:
            winner = tied_texts[0]
        else:
            # Determinism: identical to P1 lex tie-break on the earliest
            # (lex-smallest) source_id that voted for each tied text.
            earliest_voter: dict[str, str] = {}
            for s in valid:
                text = s["text"]
                if text in tied_texts:
                    sid = s["source_id"]
                    if text not in earliest_voter or sid < earliest_voter[text]:
                        earliest_voter[text] = sid
            winner = min(tied_texts, key=lambda t: earliest_voter[t])

        contributions: dict[str, str] = {s["source_id"]: s["text"] for s in valid}

        return {
            "text": winner,
            "metadata": {
                "strategy": "majority_vote",
                "votes": dict(vote_count),
                "winner_votes": max_votes,
                "tie_break_applied": len(tied_texts) > 1,
                "contributions": contributions,
            },
        }
