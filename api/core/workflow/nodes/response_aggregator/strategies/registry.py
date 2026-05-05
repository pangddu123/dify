"""Local response-strategy registry — kept response_aggregator-private.

Distinct from the cross-cutting ``parallel_ensemble.AggregatorRegistry``:
strategies registered here are *only* visible to response_aggregator's
node, so an additional strategy doesn't pollute the parallel_ensemble
panel's aggregator dropdown. They share the same ``ResponseAggregator``
base type as the parallel_ensemble response aggregators (one SPI, two
registries).
"""

from collections.abc import Callable

from ..exceptions import StrategyNotFoundError
from .base import ResponseAggregator

_REGISTRY: dict[str, type[ResponseAggregator]] = {}


def register(
    name: str,
) -> Callable[[type[ResponseAggregator]], type[ResponseAggregator]]:
    def deco(cls: type[ResponseAggregator]) -> type[ResponseAggregator]:
        existing = _REGISTRY.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"Strategy '{name}' already registered by {existing.__name__}"
            )
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return deco


def get_strategy(name: str) -> ResponseAggregator:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise StrategyNotFoundError(name)
    return cls()


def list_strategies() -> list[dict[str, object]]:
    """Per-strategy summary for the frontend dropdown.

    ``config_schema`` is the pydantic JSON schema export so the panel
    can fall back to schema-driven rendering when ``ui_schema`` is
    empty. Strategies that ship explicit ``ui_schema`` entries (e.g.
    ``concat``) get them surfaced verbatim.
    """
    return [
        {
            "name": cls.name,
            "config_schema": cls.config_class.model_json_schema(),
            "ui_schema": cls.ui_schema,
        }
        for cls in _REGISTRY.values()
    ]
