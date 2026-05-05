"""ModelRegistry — yaml-backed alias → ``BaseSpec`` table.

Successor to P2.1's ``LocalModelRegistry`` per EXTENSIBILITY_SPEC §4.3.3.
The two important changes vs P2.1:

  1. Per-entry ``model_validate`` is delegated to
     ``BackendRegistry.get_spec_class(entry["backend"])`` instead of a
     hard-coded ``ModelSpec`` — this is what lets a third-party backend
     register a new spec subclass without editing framework code.
  2. ``LocalModelRegistry`` is kept as a backwards-compat alias for one
     release (TASKS.md L267) so already-merged P2.1 imports (P1 tests,
     response_aggregator, anything that did ``from llama_cpp.registry``)
     stay green during the migration window.

R9 (missing yaml is *not* an error) and the ADR-3 ``list_aliases()``
projection (no url, no api_key) are unchanged from P2.1.

Concurrency is the same double-checked-locked singleton as P2.1; the
test hooks ``reset_for_testing`` / ``for_testing(path)`` carry over.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from configs import dify_config

from ..exceptions import (
    RegistryFileError,
    UnknownBackendError,
    UnknownModelAliasError,
)
from ..spi.backend import BackendInfo, BaseSpec
from .backend_registry import BackendRegistry

logger = logging.getLogger(__name__)

API_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_REGISTRY_PATH = str(API_ROOT / "configs" / "model_net.yaml")


# P2.3: ``list_aliases`` now returns the SPI's ``BackendInfo`` shape so
# the console API (P2.4) and the per-backend capability matrix
# (BACKEND_CAPABILITIES.md) stay aligned across one TypedDict.
#
# ⚠️ Import-name compatibility ONLY. The runtime shape changed:
# P2.1 was ``{id, backend, model_name, type}``; P2.3 is
# ``{id, backend, model_name, capabilities, metadata}``. Code that
# read ``info["type"]`` must migrate to ``info["metadata"].get("type")``
# — re-exporting the name avoids a noisy import diff for callers that
# only used the type for annotations, not field access.
AliasInfo = BackendInfo


class ModelRegistry:
    """Process-wide singleton over ``model_net.yaml``.

    Loads at first ``instance()`` call, then never mutates the inner
    dict — a future ``reload()`` would atomically swap the dict, not
    edit it in place, which is why ``get`` / ``list_aliases`` need no
    locks.
    """

    _instance: ModelRegistry | None = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._models: dict[str, BaseSpec] = {}
        self._source_path: str | None = None

    @classmethod
    def instance(cls) -> ModelRegistry:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    inst = cls()
                    inst._load()
                    cls._instance = inst
        return cls._instance

    @classmethod
    def reset_for_testing(cls) -> None:
        """Drop the cached singleton — only call from tests."""
        with cls._instance_lock:
            cls._instance = None

    @classmethod
    def for_testing(cls, path: str) -> ModelRegistry:
        """Build a non-cached registry from an explicit path (tests only)."""
        inst = cls()
        inst._load(path)
        return inst

    def _resolve_path(self) -> str:
        return getattr(dify_config, "MODEL_NET_REGISTRY_PATH", DEFAULT_REGISTRY_PATH)

    def _load(self, path_override: str | None = None) -> None:
        path_str = path_override if path_override is not None else self._resolve_path()
        self._source_path = path_str
        path = Path(path_str)

        if not path.exists():
            # R9: missing yaml must not crash boot.
            logger.warning(
                "Model registry yaml not found at '%s'; ModelRegistry is empty. "
                "Workflow nodes that reference model aliases will fail at run time.",
                path_str,
            )
            self._models = {}
            return

        try:
            with path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            raise RegistryFileError(path_str, str(exc)) from exc

        if raw is None:
            self._models = {}
            return
        if not isinstance(raw, dict):
            raise RegistryFileError(path_str, "top-level yaml must be a mapping")

        entries: Any = raw.get("models", [])
        if not isinstance(entries, list):
            raise RegistryFileError(path_str, "'models' must be a list")

        models: dict[str, BaseSpec] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise RegistryFileError(
                    path_str,
                    f"models[{index}] must be a mapping, got {type(entry).__name__}",
                )

            backend_name = entry.get("backend")
            if not isinstance(backend_name, str) or not backend_name:
                raise RegistryFileError(
                    path_str,
                    f"models[{index}] missing or empty 'backend' field; "
                    f"expected one of {BackendRegistry.known_backends()}",
                )

            try:
                spec_class = BackendRegistry.get_spec_class(backend_name)
            except UnknownBackendError:
                raise RegistryFileError(
                    path_str,
                    f"models[{index}] backend '{backend_name}' is not registered; "
                    f"known backends: {BackendRegistry.known_backends()}. "
                    f"If this is a third-party backend, ensure the package is "
                    f"importable before registry load.",
                ) from None

            try:
                spec = spec_class.model_validate(entry)
            except ValidationError as exc:
                raise RegistryFileError(
                    path_str,
                    f"models[{index}] (backend={backend_name}) invalid: {exc}",
                ) from exc

            if spec.id in models:
                raise RegistryFileError(path_str, f"duplicate model id '{spec.id}'")
            models[spec.id] = spec

        self._models = models

    def get(self, alias: str) -> BaseSpec:
        try:
            return self._models[alias]
        except KeyError as exc:
            raise UnknownModelAliasError(alias) from exc

    def list_aliases(self) -> list[BackendInfo]:
        """Public projection for the console API + frontend dropdown.

        Returns ``BackendInfo`` per spec so the console (P2.4) gets the
        same shape across every backend. URL / api_key / api_key_env
        never leave this method — that is the SSRF / credential
        boundary against the DSL layer (T2). Today only llama.cpp's
        ``type`` (normal / think) flows through ``metadata``; the field
        is the documented hook for any future non-secret extra a
        backend wants the dropdown to render without expanding the
        TypedDict.
        """
        out: list[BackendInfo] = []
        for spec in self._models.values():
            backend_cls = BackendRegistry.get(spec.backend)
            capabilities = sorted(cap.value for cap in backend_cls.capabilities(spec))
            metadata: dict[str, Any] = {}
            spec_type = getattr(spec, "type", None)
            if spec_type is not None:
                metadata["type"] = spec_type
            out.append(
                BackendInfo(
                    id=spec.id,
                    backend=spec.backend,
                    model_name=spec.model_name,
                    capabilities=capabilities,
                    metadata=metadata,
                )
            )
        return out

    def __contains__(self, alias: object) -> bool:
        return isinstance(alias, str) and alias in self._models

    def __len__(self) -> int:
        return len(self._models)

    def __repr__(self) -> str:
        return f"ModelRegistry(source={self._source_path!r}, aliases={list(self._models.keys())})"


# ── Backwards-compat alias ──────────────────────────────────────────────
# Kept for one release per TASKS.md L267 so P2.1-era imports continue to
# work while P2.2 / P2.3 / consumers migrate. New code should import
# ``ModelRegistry`` directly.
LocalModelRegistry = ModelRegistry
