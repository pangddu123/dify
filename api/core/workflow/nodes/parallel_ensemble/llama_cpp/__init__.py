"""llama.cpp model registry + HTTP client for parallel-ensemble node.

This sub-package owns the server-side `LocalModelRegistry` (P2.1) and the
SSRF-proxied `LlamaCppClient` (P2.2). The registry is the single source of
truth for model URLs — workflow nodes only ever reference models by
``alias`` (the registry key); URLs never cross the API/UI boundary
(see ADR-3 / ADR-8 in DEVELOPMENT_PLAN.md).
"""

from .exceptions import (
    LlamaCppNodeError,
    ModelRegistryError,
    RegistryFileError,
    UnknownModelAliasError,
)
from .registry import LocalModelRegistry, ModelSpec

__all__ = [
    "LlamaCppNodeError",
    "LocalModelRegistry",
    "ModelRegistryError",
    "ModelSpec",
    "RegistryFileError",
    "UnknownModelAliasError",
]
