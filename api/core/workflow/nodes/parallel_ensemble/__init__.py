"""Parallel ensemble workflow node package (Phase 2 — token-level).

The SPI surface is frozen by P2.1.5; see ``spi/`` and ``registry/``.
P2.9 will turn the side-effect imports below into ``pkgutil.walk_packages``
auto-discovery, but for now the explicit imports keep test/setup ordering
deterministic: ``backends/`` registers ``llama_cpp`` before any code
calls ``ModelRegistry.instance()``.
"""

from __future__ import annotations

# Side-effect imports — populate the three registries so ``ModelRegistry``
# can dispatch ``backend: llama_cpp`` yaml entries without callers
# remembering to import the backend module first. Order matters:
# backends/ must register ``LlamaCppSpec`` before ``ModelRegistry._load``
# can resolve a yaml entry's backend string. Runners / aggregators land
# alongside in P2.5 / P2.6 — wire them here too at that point.
from . import aggregators as aggregators
from . import backends as backends
from . import runners as runners

PARALLEL_ENSEMBLE_NODE_TYPE = "parallel-ensemble"

__all__ = ["PARALLEL_ENSEMBLE_NODE_TYPE", "aggregators", "backends", "runners"]
