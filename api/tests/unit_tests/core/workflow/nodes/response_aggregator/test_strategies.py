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

from core.workflow.nodes.response_aggregator.strategies import (
    ConcatStrategy,
    ResponseSignal,
    SourceAggregationContext,
    get_strategy,
    list_strategies,
)
from core.workflow.nodes.response_aggregator.strategies.concat import _ConcatConfig
from core.workflow.nodes.response_aggregator.strategies.registry import (
    _REGISTRY,
    register,
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

    def test_concat_runs_without_backend_fields(self):
        # Concretely: build a SourceAggregationContext (no backends /
        # capabilities / runner_name / trace) and run the bundled
        # strategy through it — proves it doesn't reach into the
        # token-layer fields.
        signals = _signals(("s1", "A"), ("s2", "B"))
        ctx = _ctx(["s1", "s2"], weights={"s1": 2.0, "s2": 1.0})
        result = ConcatStrategy().aggregate(signals, ctx, _ConcatConfig())
        assert "text" in result
        assert "metadata" in result


class TestRegistry:
    def test_concat_is_only_registered_strategy(self):
        names = {entry["name"] for entry in list_strategies()}
        assert names == {"concat"}

    def test_list_strategies_carries_ui_schema(self):
        # ConcatStrategy publishes ui_schema entries; the panel
        # depends on this surface for v3 ui_schema reflection.
        entries = {e["name"]: e for e in list_strategies()}
        assert entries["concat"]["ui_schema"]["separator"]["control"] == "text_input"

    def test_get_strategy_returns_fresh_instance(self):
        a = get_strategy("concat")
        b = get_strategy("concat")
        assert isinstance(a, ConcatStrategy)
        assert a is not b

    def test_duplicate_registration_raises_value_error(self):
        # Guard against accidental double registration during dev edits.
        from core.workflow.nodes.response_aggregator.strategies.base import (
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
            register("concat")(_Dummy)

        # Sanity: registry entry untouched by the failed call above.
        assert _REGISTRY["concat"] is ConcatStrategy
