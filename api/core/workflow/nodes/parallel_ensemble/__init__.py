"""Parallel ensemble workflow node package (Phase 2 — token-level).

The SPI surface is frozen by P2.1.5; see ``spi/`` and ``registry/``.
P2.9 will turn the side-effect imports below into ``pkgutil.walk_packages``
auto-discovery, but for now the explicit imports keep test/setup ordering
deterministic: ``backends/`` registers ``llama_cpp`` before any code
calls ``ModelRegistry.instance()``.
"""

from __future__ import annotations

PARALLEL_ENSEMBLE_NODE_TYPE = "parallel-ensemble"

# Side-effect imports — populate the three registries so ``ModelRegistry``
# can dispatch ``backend: llama_cpp`` yaml entries without callers
# remembering to import the backend module first. Order matters:
# backends/ must register ``LlamaCppSpec`` before ``ModelRegistry._load``
# can resolve a yaml entry's backend string. Runners / aggregators land
# alongside in P2.5 / P2.6.
#
# These imports run *after* the constant above so ``entities.py`` /
# ``node.py`` (which import the constant at module scope) can be loaded
# from ``parallel_ensemble`` without circular pain. ``aggregators`` /
# ``backends`` / ``runners`` do not depend on the node class, so
# importing them here is purely a registration side-effect.
from . import aggregators as aggregators
from . import backends as backends
from . import runners as runners

__all__ = ["PARALLEL_ENSEMBLE_NODE_TYPE", "aggregators", "backends", "runners"]
