"""Backwards-compat shim for the P2.1 exception names.

The class definitions moved up to ``parallel_ensemble.exceptions`` in
P2.1.5 to break the ``ModelRegistry`` → ``llama_cpp`` import cycle the
shim layout would otherwise create. Re-exports preserve every P2.1
import path (``from ...llama_cpp.exceptions import RegistryFileError``)
for one release per TASKS.md L267.
"""

from __future__ import annotations

from ..exceptions import (
    LlamaCppNodeError,
    ModelRegistryError,
    RegistryFileError,
    UnknownModelAliasError,
)

__all__ = [
    "LlamaCppNodeError",
    "ModelRegistryError",
    "RegistryFileError",
    "UnknownModelAliasError",
]
