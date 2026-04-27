"""Aggregator registry — sibling of ``RunnerRegistry``.

The decorator takes ``scope`` so that registration enforces what the
class also declares (catches the easy mistake of pasting a runner-style
decorator on an aggregator and forgetting to pass scope).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..exceptions import DuplicateRegistrationError, UnknownAggregatorError

if TYPE_CHECKING:
    from ..spi.aggregator import Aggregator


class AggregatorRegistry:
    """Process-wide map ``aggregator_name → Aggregator subclass``."""

    _aggregators: dict[str, type[Aggregator]] = {}

    @classmethod
    def register(cls, name: str, agg_cls: type[Aggregator]) -> None:
        if name in cls._aggregators:
            raise DuplicateRegistrationError("aggregator", name)
        cls._aggregators[name] = agg_cls

    @classmethod
    def get(cls, name: str) -> type[Aggregator]:
        try:
            return cls._aggregators[name]
        except KeyError as exc:
            raise UnknownAggregatorError(name, list(cls._aggregators)) from exc

    @classmethod
    def by_scope(cls, scope: str) -> list[type[Aggregator]]:
        """Aggregators paired with a given runner scope (used by UI dropdown)."""
        return [a for a in cls._aggregators.values() if a.scope == scope]

    @classmethod
    def known_aggregators(cls) -> list[str]:
        return sorted(cls._aggregators)

    @classmethod
    def reset_for_testing(cls) -> None:
        cls._aggregators = {}


def register_aggregator(name: str, *, scope: str):
    """Decorator form of :meth:`AggregatorRegistry.register`.

    Asserts the class declares the same scope it is registered under so
    the decorator and the class can't drift.
    """

    def deco(agg_cls: type[Aggregator]) -> type[Aggregator]:
        declared = getattr(agg_cls, "scope", None)
        if declared != scope:
            raise ValueError(
                f"aggregator '{name}' decorator scope={scope!r} disagrees "
                f"with class scope={declared!r}"
            )
        agg_cls.name = name
        AggregatorRegistry.register(name, agg_cls)
        return agg_cls

    return deco
