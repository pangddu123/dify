from collections.abc import Callable

from ..exceptions import StrategyNotFoundError
from .base import AggregationStrategy

_REGISTRY: dict[str, type[AggregationStrategy]] = {}


def register(
    name: str,
) -> Callable[[type[AggregationStrategy]], type[AggregationStrategy]]:
    def deco(cls: type[AggregationStrategy]) -> type[AggregationStrategy]:
        existing = _REGISTRY.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"Strategy '{name}' already registered by {existing.__name__}"
            )
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return deco


def get_strategy(name: str) -> AggregationStrategy:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise StrategyNotFoundError(name)
    return cls()


def list_strategies() -> list[dict[str, object]]:
    return [
        {"name": cls.name, "config_schema": cls.config_schema}
        for cls in _REGISTRY.values()
    ]
