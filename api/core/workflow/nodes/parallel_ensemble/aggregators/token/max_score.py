"""Token-scope ``max_score`` aggregator — pick the single most-confident vote.

Useful when the user wants a "loudest dissenter wins" behaviour: instead
of summing across backends, take the maximum single per-backend
probability for each token and pick whichever token has the largest
single endorsement. Lex tie-break on token, same determinism contract
as ``sum_score``.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ...registry.aggregator_registry import register_aggregator
from ...spi.aggregator import (
    BackendAggregationContext,
    TokenAggregator,
    TokenPick,
    TokenSignals,
)


class MaxScoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skip_empty_voters: bool = True
    use_weights: bool = True


@register_aggregator("max_score", scope="token")
class MaxScoreAggregator(TokenAggregator[MaxScoreConfig]):
    """Pick the token whose single highest weighted prob beats every other token."""

    config_class: ClassVar[type[BaseModel]] = MaxScoreConfig
    i18n_key_prefix: ClassVar[str] = "parallelEnsemble.aggregators.maxScore"
    ui_schema: ClassVar[dict] = {
        "skip_empty_voters": {"control": "switch"},
        "use_weights": {"control": "switch"},
    }

    def aggregate(
        self,
        signals: TokenSignals,
        context: BackendAggregationContext,
        config: MaxScoreConfig,
    ) -> TokenPick:
        per_model = signals["per_model"]
        per_model_errors = signals.get("per_model_errors", {})

        weights = context.weights if config.use_weights else {}

        token_max: dict[str, float] = {}
        token_argmax_alias: dict[str, str] = {}

        for alias, candidates in per_model.items():
            w = weights.get(alias, 1.0) if config.use_weights else 1.0
            for cand in candidates:
                tok = cand["token"]
                score = cand["prob"] * w
                # Tie within the same token across backends: keep the
                # lex-smallest alias, mirroring the lex-tie-break rule
                # we apply across tokens.
                if (
                    tok not in token_max
                    or score > token_max[tok]
                    or (score == token_max[tok] and alias < token_argmax_alias[tok])
                ):
                    token_max[tok] = score
                    token_argmax_alias[tok] = alias

        if not token_max:
            return {
                "token": "",
                "score": 0.0,
                "reasoning": {
                    "per_token_max": {},
                    "empty_voters": list(per_model_errors),
                    "all_voters_empty": True,
                },
            }

        peak = max(token_max.values())
        tied = [t for t, s in token_max.items() if s == peak]
        winner = min(tied)

        reasoning: dict = {
            "per_token_max": dict(token_max),
            "winner_alias": token_argmax_alias[winner],
            "tie_break_applied": len(tied) > 1,
        }
        if not config.skip_empty_voters and per_model_errors:
            reasoning["empty_voters"] = list(per_model_errors)

        return {
            "token": winner,
            "score": peak,
            "reasoning": reasoning,
        }
