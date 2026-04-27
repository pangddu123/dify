"""Token-scope ``sum_score`` aggregator — equivalent to PN.py ``calculate_scores``.

For every token that appears in any backend's top-k at the current step,
sum the per-backend probability mass (optionally weighted by the
``BackendInfo.weight`` carried on ``AggregationContext``). The token with
the highest total wins; ties resolve to the lex-smallest token so the
choice is deterministic across runs and across worker pool ordering
(PN.py used ``random.choice`` here, which we *deliberately* replace).

``per_model_errors`` interpretation
-----------------------------------

The aggregator does not own state across steps, so "use last step's
fallback" is the runner's job. ``skip_empty_voters`` here only controls
how the *current* step is scored:

* ``True`` (default) — errored backends silently contribute nothing.
* ``False`` — the aggregator records the error keys in
  ``reasoning["empty_voters"]`` so the trace makes the gap visible; the
  runner can read this to decide whether to re-emit last step's pick.

Either way the math runs only over backends that actually returned
candidates this step.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ...registry.aggregator_registry import register_aggregator
from ...spi.aggregator import (
    AggregationContext,
    TokenAggregator,
    TokenPick,
    TokenSignals,
)


class SumScoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skip_empty_voters: bool = True
    use_weights: bool = True


@register_aggregator("sum_score", scope="token")
class SumScoreAggregator(TokenAggregator[SumScoreConfig]):
    """Sum candidate probabilities across backends; lex tie-break on token."""

    config_class: ClassVar[type[BaseModel]] = SumScoreConfig
    i18n_key_prefix: ClassVar[str] = "parallelEnsemble.aggregators.sumScore"
    ui_schema: ClassVar[dict] = {
        "skip_empty_voters": {"control": "switch"},
        "use_weights": {"control": "switch"},
    }

    def aggregate(
        self,
        signals: TokenSignals,
        context: AggregationContext,
        config: SumScoreConfig,
    ) -> TokenPick:
        per_model = signals["per_model"]
        per_model_errors = signals.get("per_model_errors", {})

        weights = context.weights if config.use_weights else {}

        token_score: dict[str, float] = {}
        token_per_model: dict[str, dict[str, float]] = {}

        for alias, candidates in per_model.items():
            w = weights.get(alias, 1.0) if config.use_weights else 1.0
            for cand in candidates:
                tok = cand["token"]
                contribution = cand["prob"] * w
                token_score[tok] = token_score.get(tok, 0.0) + contribution
                token_per_model.setdefault(tok, {})[alias] = contribution

        if not token_score:
            # No backend produced anything this step. Surface a structured
            # empty pick so the runner can decide what to do (fallback /
            # abort) rather than crashing with KeyError.
            return {
                "token": "",
                "score": 0.0,
                "reasoning": {
                    "per_token_score": {},
                    "empty_voters": list(per_model_errors),
                    "all_voters_empty": True,
                },
            }

        max_score = max(token_score.values())
        tied = [t for t, s in token_score.items() if s == max_score]
        # Lex tie-break: PN.py used random.choice, which makes runs
        # non-reproducible. Sort so identical inputs always pick the
        # same token.
        winner = min(tied)

        reasoning: dict = {
            "per_token_score": dict(token_score),
            "winner_per_model": token_per_model.get(winner, {}),
            "tie_break_applied": len(tied) > 1,
        }
        if not config.skip_empty_voters and per_model_errors:
            # Make missing backends visible to the runner / trace consumer
            # so they can drive the "use last step's fallback" path.
            reasoning["empty_voters"] = list(per_model_errors)

        return {
            "token": winner,
            "score": max_score,
            "reasoning": reasoning,
        }
