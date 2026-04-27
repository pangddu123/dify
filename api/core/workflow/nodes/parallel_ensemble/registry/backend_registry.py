"""Backend registry — EXTENSIBILITY_SPEC §4.3.2.

A simple class-level dict guarded by ``@register_backend`` for duplicate
detection and ``spec_class`` invariants. ``ModelRegistry._load`` calls
``get_spec_class(backend_name)`` per yaml entry to drive dynamic
discriminated-union parsing without freezing the union at import time.

⚠️ Backends must be imported *before* ``ModelRegistry._load`` runs,
otherwise yaml entries with their ``backend`` string won't resolve.
P2.9 will wire ``pkgutil.walk_packages`` over the ``backends/``
subpackage to make this automatic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..exceptions import DuplicateRegistrationError, UnknownBackendError

if TYPE_CHECKING:  # avoid registry → spi → registry cycle at runtime
    from ..spi.backend import BaseSpec, ModelBackend


class BackendRegistry:
    """Process-wide map ``backend_name → ModelBackend subclass``.

    Stored as a plain class attribute (not an instance) — there is no
    natural per-process state, and a singleton would just be ceremony.
    Tests that need a clean slate use :meth:`reset_for_testing`.
    """

    _backends: dict[str, type[ModelBackend]] = {}

    @classmethod
    def register(cls, name: str, backend_cls: type[ModelBackend]) -> None:
        """Add ``backend_cls`` under ``name``.

        Raises:
          DuplicateRegistrationError: if ``name`` is already taken.
          TypeError: if ``backend_cls.spec_class`` is not a ``BaseSpec`` subclass.
        """
        # local import to keep registry.* importable before spi.* loads
        from ..spi.backend import BaseSpec

        if name in cls._backends:
            raise DuplicateRegistrationError("backend", name)
        spec_class = getattr(backend_cls, "spec_class", None)
        if spec_class is None or not isinstance(spec_class, type) or not issubclass(spec_class, BaseSpec):
            raise TypeError(f"backend '{name}' spec_class must be a BaseSpec subclass, got {spec_class!r}")
        cls._backends[name] = backend_cls

    @classmethod
    def get(cls, name: str) -> type[ModelBackend]:
        try:
            return cls._backends[name]
        except KeyError as exc:
            raise UnknownBackendError(name, list(cls._backends)) from exc

    @classmethod
    def get_spec_class(cls, name: str) -> type[BaseSpec]:
        return cls.get(name).spec_class

    @classmethod
    def known_backends(cls) -> list[str]:
        return sorted(cls._backends)

    @classmethod
    def reset_for_testing(cls) -> None:
        """Drop every registration — only call from tests."""
        cls._backends = {}


def register_backend(name: str):
    """Decorator form of :meth:`BackendRegistry.register`.

    Sets ``cls.name = name`` so the backend instance can echo its
    registry key without a separate attribute.
    """

    def deco(backend_cls: type[ModelBackend]) -> type[ModelBackend]:
        backend_cls.name = name
        BackendRegistry.register(name, backend_cls)
        return backend_cls

    return deco
