"""Strategy-level unit tests — v3 SPI convergence.

Strategies inherit ``ResponseAggregator`` from
``parallel_ensemble.spi.aggregator``: ``aggregate(signals, context, config)``
takes a typed pydantic ``config`` (validated upstream by the node, not
inside the strategy) plus a ``SourceAggregationContext``.

Schema-level coverage lives in test_entities.py; end-to-end wiring
through ``_run`` (which is what triggers config validation +
WeightResolutionError) lives in test_node.py.
"""

import pytest

from core.workflow.nodes.ensemble_aggregator.strategies import (
    ConcatStrategy,
    MajorityVoteStrategy,
    ResponseSignal,
    SourceAggregationContext,
    WeightedMajorityVoteStrategy,
    get_strategy,
    list_strategies,
)
from core.workflow.nodes.ensemble_aggregator.strategies.concat import _ConcatConfig
from core.workflow.nodes.ensemble_aggregator.strategies.majority_vote import (
    _MajorityVoteConfig,
)
from core.workflow.nodes.ensemble_aggregator.strategies.registry import (
    _REGISTRY,
    register,
)
from core.workflow.nodes.ensemble_aggregator.strategies.weighted_majority_vote import (
    _WeightedMajorityVoteConfig,
)


def _signals(*pairs: tuple[str, str]) -> list[ResponseSignal]:
    return [
        ResponseSignal(
            source_id=sid,
            text=text,
            finish_reason="stop",
            elapsed_ms=0,
            error=None,
        )
        for sid, text in pairs
    ]


def _ctx(
    sources: list[str],
    weights: dict[str, float] | None = None,
) -> SourceAggregationContext:
    """Default context: unit weights (matches DSLs that don't set ``weight``)."""
    return SourceAggregationContext(
        sources=sources,
        weights=weights if weights is not None else dict.fromkeys(sources, 1.0),
        source_meta={},
        strategy_config={},
    )


class TestMajorityVoteStrategy:
    def test_three_way_majority_wins(self):
        """The canonical plan spec: ['A','A','B'] → 'A'."""
        strategy = MajorityVoteStrategy()
        signals = _signals(("s1", "A"), ("s2", "A"), ("s3", "B"))
        result = strategy.aggregate(signals, _ctx(["s1", "s2", "s3"]), _MajorityVoteConfig())

        assert result["text"] == "A"
        assert result["metadata"]["strategy"] == "majority_vote"
        assert result["metadata"]["votes"] == {"A": 2, "B": 1}
        assert result["metadata"]["winner_votes"] == 2
        assert result["metadata"]["tie_break_applied"] is False
        assert result["metadata"]["weighted"] is False
        assert result["metadata"]["contributions"] == {
            "s1": "A",
            "s2": "A",
            "s3": "B",
        }

    def test_unanimous_win(self):
        strategy = MajorityVoteStrategy()
        signals = _signals(("s1", "only"), ("s2", "only"))
        result = strategy.aggregate(signals, _ctx(["s1", "s2"]), _MajorityVoteConfig())
        assert result["text"] == "only"
        assert result["metadata"]["tie_break_applied"] is False
        assert result["metadata"]["winner_votes"] == 2

    def test_two_way_tie_breaks_by_source_id_lex_order(self):
        # 'X' is voted by alice (lex-smaller), 'Y' by bob.
        # Tie-break: winner is the text whose earliest (lex-smallest)
        # voter has the lex-smallest source_id → 'X' wins.
        strategy = MajorityVoteStrategy()
        signals = _signals(("bob", "Y"), ("alice", "X"))
        result = strategy.aggregate(signals, _ctx(["bob", "alice"]), _MajorityVoteConfig())
        assert result["text"] == "X"
        assert result["metadata"]["tie_break_applied"] is True
        assert result["metadata"]["winner_votes"] == 1

    def test_tie_break_independent_of_input_order(self):
        """Reversing input order must not change the tie-break winner."""
        strategy = MajorityVoteStrategy()
        forward = strategy.aggregate(
            _signals(("alice", "X"), ("bob", "Y")),
            _ctx(["alice", "bob"]),
            _MajorityVoteConfig(),
        )
        reverse = strategy.aggregate(
            _signals(("bob", "Y"), ("alice", "X")),
            _ctx(["bob", "alice"]),
            _MajorityVoteConfig(),
        )
        assert forward["text"] == reverse["text"] == "X"

    def test_three_way_tie_picks_lex_smallest_voter_group(self):
        strategy = MajorityVoteStrategy()
        signals = _signals(("c1", "C"), ("a1", "A"), ("b1", "B"))
        result = strategy.aggregate(signals, _ctx(["c1", "a1", "b1"]), _MajorityVoteConfig())
        assert result["text"] == "A"
        assert result["metadata"]["tie_break_applied"] is True

    def test_contributions_keyed_by_source_id(self):
        strategy = MajorityVoteStrategy()
        signals = _signals(("gpt4", "hi"), ("claude", "hi"), ("llama", "bye"))
        result = strategy.aggregate(
            signals, _ctx(["gpt4", "claude", "llama"]), _MajorityVoteConfig()
        )
        assert result["metadata"]["contributions"] == {
            "gpt4": "hi",
            "claude": "hi",
            "llama": "bye",
        }

    def test_weights_promote_minority_text_to_winner(self):
        """Plain count would tie 1:1; the heavy weight breaks it without a lex fallback."""
        strategy = MajorityVoteStrategy()
        signals = _signals(("s1", "A"), ("s2", "B"))
        ctx = _ctx(["s1", "s2"], weights={"s1": 1.0, "s2": 3.0})
        result = strategy.aggregate(signals, ctx, _MajorityVoteConfig())
        assert result["text"] == "B"
        assert result["metadata"]["weighted"] is True
        assert result["metadata"]["votes"] == {"A": 1.0, "B": 3.0}
        assert result["metadata"]["winner_votes"] == 3.0
        assert result["metadata"]["tie_break_applied"] is False

    def test_unit_weights_match_v2_4_payload_exactly(self):
        """Regression: a v2.4 DSL (no weights) must keep integer counts in metadata."""
        strategy = MajorityVoteStrategy()
        signals = _signals(("s1", "A"), ("s2", "A"), ("s3", "B"))
        result = strategy.aggregate(signals, _ctx(["s1", "s2", "s3"]), _MajorityVoteConfig())
        assert result["metadata"]["votes"] == {"A": 2, "B": 1}
        assert isinstance(result["metadata"]["winner_votes"], int)
        assert result["metadata"]["weighted"] is False


class TestConcatStrategy:
    def test_default_separator_joins_with_horizontal_rule(self):
        strategy = ConcatStrategy()
        signals = _signals(("s1", "A"), ("s2", "B"))
        result = strategy.aggregate(signals, _ctx(["s1", "s2"]), _ConcatConfig())
        assert result["text"] == "A\n\n---\n\nB"
        assert result["metadata"]["strategy"] == "concat"
        assert result["metadata"]["separator"] == "\n\n---\n\n"
        assert result["metadata"]["include_source_label"] is False
        assert result["metadata"]["order_by_weight"] is False
        assert result["metadata"]["contributions"] == {"s1": "A", "s2": "B"}

    def test_custom_separator(self):
        strategy = ConcatStrategy()
        signals = _signals(("s1", "A"), ("s2", "B"), ("s3", "C"))
        result = strategy.aggregate(
            signals, _ctx(["s1", "s2", "s3"]), _ConcatConfig(separator=" | ")
        )
        assert result["text"] == "A | B | C"
        assert result["metadata"]["separator"] == " | "

    def test_include_source_label_adds_bracketed_prefix(self):
        strategy = ConcatStrategy()
        signals = _signals(("gpt4", "hello"), ("claude", "world"))
        result = strategy.aggregate(
            signals,
            _ctx(["gpt4", "claude"]),
            _ConcatConfig(include_source_label=True),
        )
        assert result["text"] == "[gpt4]\nhello\n\n---\n\n[claude]\nworld"
        assert result["metadata"]["include_source_label"] is True

    def test_include_source_label_with_custom_separator(self):
        strategy = ConcatStrategy()
        signals = _signals(("a", "x"), ("b", "y"))
        result = strategy.aggregate(
            signals,
            _ctx(["a", "b"]),
            _ConcatConfig(include_source_label=True, separator=" || "),
        )
        assert result["text"] == "[a]\nx || [b]\ny"

    def test_input_order_preserved(self):
        """Concat must keep the declared input order — tests depend on this."""
        strategy = ConcatStrategy()
        signals = _signals(("s3", "third"), ("s1", "first"), ("s2", "second"))
        result = strategy.aggregate(
            signals, _ctx(["s3", "s1", "s2"]), _ConcatConfig(separator="|")
        )
        assert result["text"] == "third|first|second"

    def test_order_by_weight_sorts_descending_by_weight(self):
        strategy = ConcatStrategy()
        signals = _signals(("low", "L"), ("high", "H"), ("mid", "M"))
        ctx = _ctx(
            ["low", "high", "mid"],
            weights={"low": 0.5, "high": 5.0, "mid": 1.0},
        )
        result = strategy.aggregate(
            signals, ctx, _ConcatConfig(separator="|", order_by_weight=True)
        )
        assert result["text"] == "H|M|L"
        assert result["metadata"]["order_by_weight"] is True

    def test_order_by_weight_stable_on_tie(self):
        strategy = ConcatStrategy()
        signals = _signals(("a", "A"), ("b", "B"), ("c", "C"))
        ctx = _ctx(["a", "b", "c"], weights={"a": 1.0, "b": 1.0, "c": 1.0})
        result = strategy.aggregate(
            signals, ctx, _ConcatConfig(separator="|", order_by_weight=True)
        )
        # All weights equal → preserve insertion order.
        assert result["text"] == "A|B|C"


class TestWeightedMajorityVoteStrategy:
    def test_picks_highest_weighted_text(self):
        strategy = WeightedMajorityVoteStrategy()
        signals = _signals(("s1", "A"), ("s2", "A"), ("s3", "B"))
        # 'A' weight = 1+1 = 2, 'B' weight = 5 → 'B' wins.
        ctx = _ctx(["s1", "s2", "s3"], weights={"s1": 1.0, "s2": 1.0, "s3": 5.0})
        result = strategy.aggregate(signals, ctx, _WeightedMajorityVoteConfig())
        assert result["text"] == "B"
        assert result["metadata"]["scores"] == {"A": 2.0, "B": 5.0}
        assert result["metadata"]["winner_score"] == 5.0
        assert result["metadata"]["tie_break_applied"] is False

    def test_unit_weights_collapse_to_majority_count(self):
        """Under unit weights, weighted_majority_vote must agree with majority_vote on the winner."""
        strategy = WeightedMajorityVoteStrategy()
        signals = _signals(("s1", "A"), ("s2", "A"), ("s3", "B"))
        result = strategy.aggregate(
            signals, _ctx(["s1", "s2", "s3"]), _WeightedMajorityVoteConfig()
        )
        assert result["text"] == "A"
        assert result["metadata"]["scores"] == {"A": 2.0, "B": 1.0}
        assert result["metadata"]["winner_score"] == 2.0

    def test_tie_break_lex_smallest_voter(self):
        strategy = WeightedMajorityVoteStrategy()
        # X / Y both score 2.0; earliest voters alice / bob → X wins.
        signals = _signals(("bob", "Y"), ("alice", "X"))
        ctx = _ctx(["bob", "alice"], weights={"bob": 2.0, "alice": 2.0})
        result = strategy.aggregate(signals, ctx, _WeightedMajorityVoteConfig())
        assert result["text"] == "X"
        assert result["metadata"]["tie_break_applied"] is True

    def test_metadata_carries_weights(self):
        strategy = WeightedMajorityVoteStrategy()
        signals = _signals(("s1", "A"), ("s2", "B"))
        ctx = _ctx(["s1", "s2"], weights={"s1": 0.7, "s2": 0.3})
        result = strategy.aggregate(signals, ctx, _WeightedMajorityVoteConfig())
        assert result["metadata"]["weights"] == {"s1": 0.7, "s2": 0.3}

    def test_unit_weights_keep_weighted_metadata_shape(self):
        """Regression: unlike majority_vote, the weighted variant must not
        collapse `scores` to int counts even when weights are all 1.0 —
        downstream consumers reading ``metadata.scores`` rely on the
        float-shaped surface (ADR-v3-9 SPI extension example).
        """
        strategy = WeightedMajorityVoteStrategy()
        signals = _signals(("s1", "A"), ("s2", "A"), ("s3", "B"))
        result = strategy.aggregate(
            signals, _ctx(["s1", "s2", "s3"]), _WeightedMajorityVoteConfig()
        )
        assert all(isinstance(v, float) for v in result["metadata"]["scores"].values())
        assert isinstance(result["metadata"]["winner_score"], float)
        # And the strategy literal stays explicit, never silently downgraded.
        assert result["metadata"]["strategy"] == "weighted_majority_vote"

    def test_dominant_single_voter_outweighs_unanimous_minority(self):
        """One source with weight 10 beats four unanimous sources at weight 1."""
        strategy = WeightedMajorityVoteStrategy()
        signals = _signals(
            ("s1", "minority"),
            ("s2", "minority"),
            ("s3", "minority"),
            ("s4", "minority"),
            ("oracle", "dominant"),
        )
        ctx = _ctx(
            ["s1", "s2", "s3", "s4", "oracle"],
            weights={
                "s1": 1.0,
                "s2": 1.0,
                "s3": 1.0,
                "s4": 1.0,
                "oracle": 10.0,
            },
        )
        result = strategy.aggregate(signals, ctx, _WeightedMajorityVoteConfig())
        assert result["text"] == "dominant"
        assert result["metadata"]["scores"] == {"minority": 4.0, "dominant": 10.0}
        assert result["metadata"]["winner_score"] == 10.0
        assert result["metadata"]["tie_break_applied"] is False

    def test_aggregated_minority_overpowers_single_strong_voter(self):
        """Multiple weighted votes for the same text must sum, beating a
        single heavier voter once the cumulative weight exceeds it."""
        strategy = WeightedMajorityVoteStrategy()
        signals = _signals(
            ("a", "T"), ("b", "T"), ("c", "T"), ("strong", "U")
        )
        ctx = _ctx(
            ["a", "b", "c", "strong"],
            weights={"a": 1.5, "b": 1.5, "c": 1.5, "strong": 4.0},
        )
        result = strategy.aggregate(signals, ctx, _WeightedMajorityVoteConfig())
        # T = 1.5 * 3 = 4.5 vs U = 4.0
        assert result["text"] == "T"
        assert result["metadata"]["scores"]["T"] == pytest.approx(4.5)
        assert result["metadata"]["scores"]["U"] == 4.0


class TestSourceAggregationContextIsolation:
    """ADR-v3-8 / Rv3-9 regression: SourceAggregationContext exposes only
    sources/weights/source_meta/strategy_config — no backend or runner
    fields leak in. A response strategy that grew an accidental
    dependency on ``context.backends`` etc. would fail to type-check
    against the v3 contract."""

    def test_source_context_field_set(self):
        # Pydantic ``model_fields`` keys are the public surface third-party
        # strategies see. Pin them so an inadvertent expansion (e.g. someone
        # moves ``backends`` up from the token layer) trips this guard.
        assert set(SourceAggregationContext.model_fields.keys()) == {
            "sources",
            "weights",
            "source_meta",
            "strategy_config",
        }

    def test_strategies_run_without_backend_fields(self):
        # Concretely: build a SourceAggregationContext (no backends /
        # capabilities / runner_name / trace) and run all three local
        # strategies through it — proves they don't reach into the
        # token-layer fields.
        signals = _signals(("s1", "A"), ("s2", "B"))
        ctx = _ctx(["s1", "s2"], weights={"s1": 2.0, "s2": 1.0})
        for strategy_cls, cfg_cls in [
            (MajorityVoteStrategy, _MajorityVoteConfig),
            (ConcatStrategy, _ConcatConfig),
            (WeightedMajorityVoteStrategy, _WeightedMajorityVoteConfig),
        ]:
            result = strategy_cls().aggregate(signals, ctx, cfg_cls())
            assert "text" in result
            assert "metadata" in result


class TestRegistry:
    def test_all_three_strategies_registered(self):
        names = {entry["name"] for entry in list_strategies()}
        assert {"majority_vote", "concat", "weighted_majority_vote"} <= names

    def test_list_strategies_carries_ui_schema(self):
        # ConcatStrategy publishes ui_schema entries; the panel
        # depends on this surface for v3 ui_schema reflection.
        entries = {e["name"]: e for e in list_strategies()}
        assert entries["concat"]["ui_schema"]["separator"]["control"] == "text_input"
        assert entries["majority_vote"]["ui_schema"] == {}

    def test_get_strategy_returns_fresh_instance(self):
        a = get_strategy("majority_vote")
        b = get_strategy("majority_vote")
        assert isinstance(a, MajorityVoteStrategy)
        assert a is not b

    def test_duplicate_registration_raises_value_error(self):
        # Guard against accidental double registration during dev edits.
        from core.workflow.nodes.ensemble_aggregator.strategies.base import (
            ResponseAggregator,
        )

        class _DummyConfig:
            pass

        class _Dummy(ResponseAggregator):  # type: ignore[misc]
            config_class = _DummyConfig  # type: ignore[assignment]
            i18n_key_prefix = "tests.dummy"
            ui_schema: dict = {}

            def aggregate(self, signals, context, config):  # pragma: no cover
                raise NotImplementedError

        with pytest.raises(ValueError, match="already registered"):
            register("majority_vote")(_Dummy)

        # Sanity: registry entry untouched by the failed call above.
        assert _REGISTRY["majority_vote"] is MajorityVoteStrategy
