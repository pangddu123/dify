"""Registry subpackage — central index of all SPI extension points.

Four sibling registries live here:

  - ``model_registry.ModelRegistry``  — the yaml-backed alias → spec table.
    Successor to P2.1's ``LocalModelRegistry`` (legacy alias re-exported
    for one release per TASKS.md L267).
  - ``backend_registry.BackendRegistry`` + ``@register_backend``
  - ``runner_registry.RunnerRegistry`` + ``@register_runner``
  - ``aggregator_registry.AggregatorRegistry`` + ``@register_aggregator``

Loading order (P2.9 will wire this in ``node_factory``):
  1. import ``backends/`` → ``@register_backend`` populates BackendRegistry
  2. import ``runners/`` → ``@register_runner``
  3. import ``aggregators/`` → ``@register_aggregator``
  4. ``ModelRegistry.instance()._load()`` — needs ``BackendRegistry`` filled
     so it can dispatch ``backend: <name>`` to the right ``spec_class``.
"""

from __future__ import annotations

from .aggregator_registry import AggregatorRegistry, register_aggregator
from .backend_registry import BackendRegistry, register_backend
from .model_registry import (
    AliasInfo,
    LocalModelRegistry,
    ModelRegistry,
)
from .runner_registry import RunnerRegistry, register_runner

__all__ = [
    "AggregatorRegistry",
    "AliasInfo",
    "BackendRegistry",
    "LocalModelRegistry",
    "ModelRegistry",
    "RunnerRegistry",
    "register_aggregator",
    "register_backend",
    "register_runner",
]
