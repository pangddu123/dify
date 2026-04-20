from collections import Counter

from pydantic import BaseModel, ConfigDict, ValidationError

from ..exceptions import StrategyConfigError
from .base import AggregationInput, AggregationResult, AggregationStrategy
from .registry import register


class _MajorityVoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register("majority_vote")
class MajorityVoteStrategy(AggregationStrategy):
    config_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def aggregate(
        self,
        inputs: list[AggregationInput],
        config: dict[str, object],
    ) -> AggregationResult:
        try:
            _MajorityVoteConfig.model_validate(config)
        except ValidationError as e:
            raise StrategyConfigError("majority_vote", str(e)) from e

        vote_count: Counter[str] = Counter(item["text"] for item in inputs)
        max_votes = max(vote_count.values())
        tied_texts = [t for t, c in vote_count.items() if c == max_votes]

        if len(tied_texts) == 1:
            winner = tied_texts[0]
        else:
            # Tie-break by lexicographically-smallest voting source_id; keeps
            # output deterministic regardless of input order.
            earliest_voter: dict[str, str] = {}
            for item in inputs:
                text = item["text"]
                if text in tied_texts:
                    sid = item["source_id"]
                    if text not in earliest_voter or sid < earliest_voter[text]:
                        earliest_voter[text] = sid
            winner = min(tied_texts, key=lambda t: earliest_voter[t])

        contributions: dict[str, str] = {
            item["source_id"]: item["text"] for item in inputs
        }

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
