"""Backwards-compat shim — P2.1 names redirected onto the P2.1.5 SPI.

P2.1 originally owned ``ModelSpec`` + ``LocalModelRegistry`` here. The
P2.1.5 SPI freeze (TASKS.md L267) moves those into:

  - ``parallel_ensemble.spi.backend.BaseSpec`` (root with the discriminator)
  - ``parallel_ensemble.backends.llama_cpp.LlamaCppSpec`` (per-backend subclass)
  - ``parallel_ensemble.registry.model_registry.ModelRegistry`` (dispatcher)

This module is kept for one release so any P2.1-era importer
(``from ...llama_cpp.registry import LocalModelRegistry``) keeps
working while callers migrate. New code should import directly from
``parallel_ensemble.registry`` / ``parallel_ensemble.backends.llama_cpp``.

⚠️ Schema change visible through the alias: the legacy ``ModelSpec``
required no ``backend`` field; ``LlamaCppSpec`` requires
``backend: "llama_cpp"`` so ``ModelRegistry._load`` can dispatch by
backend string. Existing yaml files need exactly one new line per
entry — see ``docs/ModelNet/EXTENSIBILITY_SPEC.md`` §4.3.3.
"""

from __future__ import annotations

# Re-export the new types under their P2.1 names. Importing
# ``LlamaCppSpec`` here also pulls in the ``@register_backend("llama_cpp")``
# side effect, so a caller that did ``from ...llama_cpp import ModelSpec``
# in P2.1 still ends up with the backend registered.
from ..backends.llama_cpp import LlamaCppSpec as ModelSpec
from ..registry.model_registry import (
    DEFAULT_REGISTRY_PATH,
    AliasInfo,
)
from ..registry.model_registry import (
    ModelRegistry as LocalModelRegistry,
)

__all__ = [
    "DEFAULT_REGISTRY_PATH",
    "AliasInfo",
    "LocalModelRegistry",
    "ModelSpec",
]
