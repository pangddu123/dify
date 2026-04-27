"""Backend implementations registered into ``BackendRegistry``.

v0.2 ships only ``llama_cpp``. P2.9 will wire ``pkgutil.walk_packages``
over this directory so importing the package triggers every
``@register_backend`` decorator side-effect; for the P2.1.5 SPI freeze
window we explicitly import the module that owns ``LlamaCppSpec`` so
``ModelRegistry._load`` can dispatch ``backend: llama_cpp`` entries.
"""

from __future__ import annotations

from . import llama_cpp as llama_cpp

__all__: list[str] = ["llama_cpp"]
