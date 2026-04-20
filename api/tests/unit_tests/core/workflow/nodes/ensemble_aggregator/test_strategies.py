"""Strategy-level unit tests for the ensemble aggregator node (P1.4).

Covers MajorityVoteStrategy + ConcatStrategy through their public `aggregate`
contract. Schema-level coverage lives in test_entities.py; end-to-end wiring
through `_run` lives in test_node.py.
"""

import pytest

from core.workflow.nodes.ensemble_aggregator.exceptions import StrategyConfigError
from core.workflow.nodes.ensemble_aggregator.strategies import (
    AggregationInput,
    ConcatStrategy,
    MajorityVoteStrategy,
    get_strategy,
    list_strategies,
)
from core.workflow.nodes.ensemble_aggregator.strategies.registry import _REGISTRY


def _inputs(*pairs: tuple[str, str]) -> list[AggregationInput]:
    return [{"source_id": sid, "text": text} for sid, text in pairs]


class TestMajorityVoteStrategy:
    def test_three_way_majority_wins(self):
        """The canonical plan spec: ['A','A','B'] → 'A'."""
        strategy = MajorityVoteStrategy()
        result = strategy.aggregate(
            _inputs(("s1", "A"), ("s2", "A"), ("s3", "B")),
            config={},
        )

        assert result["text"] == "A"
        assert result["metadata"]["strategy"] == "majority_vote"
        assert result["metadata"]["votes"] == {"A": 2, "B": 1}
        assert result["metadata"]["winner_votes"] == 2
        assert result["metadata"]["tie_break_applied"] is False
        assert result["metadata"]["contributions"] == {
            "s1": "A",
            "s2": "A",
            "s3": "B",
        }

    def test_unanimous_win(self):
        strategy = MajorityVoteStrategy()
        result = strategy.aggregate(
            _inputs(("s1", "only"), ("s2", "only")),
            config={},
        )
        assert result["text"] == "only"
        assert result["metadata"]["tie_break_applied"] is False
        assert result["metadata"]["winner_votes"] == 2

    def test_two_way_tie_breaks_by_source_id_lex_order(self):
        # 'X' is voted by alice (lex-smaller), 'Y' by bob.
        # Tie-break: winner is the text whose earliest (lex-smallest)
        # voter has the lex-smallest source_id → 'X' wins.
        strategy = MajorityVoteStrategy()
        result = strategy.aggregate(
            _inputs(("bob", "Y"), ("alice", "X")),
            config={},
        )
        assert result["text"] == "X"
        assert result["metadata"]["tie_break_applied"] is True
        assert result["metadata"]["winner_votes"] == 1

    def test_tie_break_independent_of_input_order(self):
        """Reversing input order must not change the tie-break winner
        (determinism — v1 plan explicitly calls this out)."""
        strategy = MajorityVoteStrategy()
        forward = strategy.aggregate(
            _inputs(("alice", "X"), ("bob", "Y")), config={}
        )
        reverse = strategy.aggregate(
            _inputs(("bob", "Y"), ("alice", "X")), config={}
        )
        assert forward["text"] == reverse["text"] == "X"

    def test_three_way_tie_picks_lex_smallest_voter_group(self):
        strategy = MajorityVoteStrategy()
        # A/B/C all 1 vote each; earliest voters are a1/b1/c1;
        # a1 < b1 < c1 → 'A' wins.
        result = strategy.aggregate(
            _inputs(("c1", "C"), ("a1", "A"), ("b1", "B")),
            config={},
        )
        assert result["text"] == "A"
        assert result["metadata"]["tie_break_applied"] is True

    def test_unknown_config_field_rejected(self):
        strategy = MajorityVoteStrategy()
        with pytest.raises(StrategyConfigError) as exc:
            strategy.aggregate(
                _inputs(("s1", "A"), ("s2", "A")),
                config={"unexpected": 1},
            )
        assert exc.value.strategy_name == "majority_vote"

    def test_contributions_keyed_by_source_id(self):
        strategy = MajorityVoteStrategy()
        result = strategy.aggregate(
            _inputs(("gpt4", "hi"), ("claude", "hi"), ("llama", "bye")),
            config={},
        )
        assert result["metadata"]["contributions"] == {
            "gpt4": "hi",
            "claude": "hi",
            "llama": "bye",
        }


class TestConcatStrategy:
    def test_default_separator_joins_with_horizontal_rule(self):
        strategy = ConcatStrategy()
        result = strategy.aggregate(
            _inputs(("s1", "A"), ("s2", "B")),
            config={},
        )
        assert result["text"] == "A\n\n---\n\nB"
        assert result["metadata"]["strategy"] == "concat"
        assert result["metadata"]["separator"] == "\n\n---\n\n"
        assert result["metadata"]["include_source_label"] is False
        assert result["metadata"]["contributions"] == {"s1": "A", "s2": "B"}

    def test_custom_separator(self):
        strategy = ConcatStrategy()
        result = strategy.aggregate(
            _inputs(("s1", "A"), ("s2", "B"), ("s3", "C")),
            config={"separator": " | "},
        )
        assert result["text"] == "A | B | C"
        assert result["metadata"]["separator"] == " | "

    def test_include_source_label_adds_bracketed_prefix(self):
        strategy = ConcatStrategy()
        result = strategy.aggregate(
            _inputs(("gpt4", "hello"), ("claude", "world")),
            config={"include_source_label": True},
        )
        assert result["text"] == "[gpt4]\nhello\n\n---\n\n[claude]\nworld"
        assert result["metadata"]["include_source_label"] is True

    def test_include_source_label_with_custom_separator(self):
        strategy = ConcatStrategy()
        result = strategy.aggregate(
            _inputs(("a", "x"), ("b", "y")),
            config={"include_source_label": True, "separator": " || "},
        )
        assert result["text"] == "[a]\nx || [b]\ny"

    def test_input_order_preserved(self):
        """Concat must keep the declared input order — tests depend on this."""
        strategy = ConcatStrategy()
        result = strategy.aggregate(
            _inputs(("s3", "third"), ("s1", "first"), ("s2", "second")),
            config={"separator": "|"},
        )
        assert result["text"] == "third|first|second"

    def test_unknown_config_field_rejected(self):
        strategy = ConcatStrategy()
        with pytest.raises(StrategyConfigError) as exc:
            strategy.aggregate(
                _inputs(("s1", "A"), ("s2", "B")),
                config={"separator": "|", "bogus": True},
            )
        assert exc.value.strategy_name == "concat"

    def test_wrong_type_rejected(self):
        strategy = ConcatStrategy()
        with pytest.raises(StrategyConfigError):
            strategy.aggregate(
                _inputs(("s1", "A"), ("s2", "B")),
                config={"separator": 123},
            )


class TestRegistry:
    def test_both_builtin_strategies_registered(self):
        names = {entry["name"] for entry in list_strategies()}
        assert "majority_vote" in names
        assert "concat" in names

    def test_get_strategy_returns_fresh_instance(self):
        a = get_strategy("majority_vote")
        b = get_strategy("majority_vote")
        assert isinstance(a, MajorityVoteStrategy)
        assert a is not b

    def test_duplicate_registration_raises_value_error(self):
        # Guard against accidental double registration during dev edits.
        from core.workflow.nodes.ensemble_aggregator.strategies.base import (
            AggregationStrategy,
        )
        from core.workflow.nodes.ensemble_aggregator.strategies.registry import (
            register,
        )

        class _Dummy(AggregationStrategy):
            def aggregate(self, inputs, config):  # pragma: no cover
                raise NotImplementedError

        with pytest.raises(ValueError, match="already registered"):
            register("majority_vote")(_Dummy)

        # Sanity: registry entry untouched by the failed call above.
        assert _REGISTRY["majority_vote"] is MajorityVoteStrategy
