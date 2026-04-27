"""ModelSpec + LocalModelRegistry — P2.1 / DEVELOPMENT_PLAN.md §6.0.

The registry is the *only* place in the codebase that knows model URLs.
Workflow nodes get a `LocalModelRegistry` instance via DifyNodeFactory
injection (P2.9) and look up models by their ``alias`` (the yaml ``id``
field). The URL never crosses into node configuration or the console
API (ADR-3) — `list_aliases()` deliberately omits it.

Field names are aligned 1:1 with `docs/ModelNet/model_info.json` so PN.py
users can copy their existing config (`EOS` uppercase, `stop_think`
underscore, `type` literal).

`extra="forbid"` here is **load-bearing for the *yaml* loader, not for the
DSL boundary**: it means a typo or rogue field in `model_net.yaml`
(server-controlled, ops-only) fails fast at boot instead of being
silently ignored. ModelSpec is *never* instantiated from workflow DSL —
the DSL only carries an `id` (alias) and looks up the spec via the
registry, so a hostile DSL trying to smuggle `model_url` cannot reach
this validator at all. The DSL-side SSRF defense is a separate guard on
`ParallelEnsembleNodeData` (P2.8 + P2.10's `test_extra_forbid_dsl`),
which must independently set `extra="forbid"` on its own model_config.
See SPIKE_GRAPHON.md §4.3–4.4 for the layering.

Risk R9: a missing `model_net.yaml` must not crash app startup. The
registry boots empty and logs a warning; nodes that try to resolve an
alias then fail with `UnknownModelAliasError` at run time.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml
from pydantic import AnyUrl, BaseModel, ConfigDict, Field, ValidationError

from configs import dify_config

from .exceptions import RegistryFileError, UnknownModelAliasError

logger = logging.getLogger(__name__)

API_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_REGISTRY_PATH = str(API_ROOT / "configs" / "model_net.yaml")


class ModelSpec(BaseModel):
    """Server-side description of one llama.cpp endpoint.

    Aligned with `docs/ModelNet/model_info.json`. Validated with
    ``extra="forbid"`` so unknown yaml keys (typos, attempted SSRF
    smuggling) fail fast instead of being silently dropped.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_arch: str = "llama"
    model_url: AnyUrl
    EOS: str = Field(min_length=1)
    type: Literal["normal", "think"] = "normal"
    stop_think: str | None = None
    weight: float = Field(default=1.0, gt=0.0)
    request_timeout_ms: int = Field(default=30000, gt=0)


class AliasInfo(TypedDict):
    """Public projection of `ModelSpec` returned by `list_aliases()`.

    URL deliberately absent — see ADR-3.
    """

    id: str
    model_name: str
    type: Literal["normal", "think"]


class LocalModelRegistry:
    """Process-wide singleton holding the parsed `model_net.yaml`.

    Concurrency: `instance()` is double-checked-locked so the first request
    after boot wins; `get` / `list_aliases` are read-only against a dict
    that is never mutated post-load (a future `reload()` would swap the
    dict atomically, not edit it in place).
    """

    _instance: LocalModelRegistry | None = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._models: dict[str, ModelSpec] = {}
        self._source_path: str | None = None

    @classmethod
    def instance(cls) -> LocalModelRegistry:
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
    def for_testing(cls, path: str) -> LocalModelRegistry:
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
            # R9: missing yaml must not crash boot. Empty registry, warn loudly.
            logger.warning(
                "Model registry yaml not found at '%s'; LocalModelRegistry is empty. "
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

        models: dict[str, ModelSpec] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise RegistryFileError(
                    path_str, f"models[{index}] must be a mapping, got {type(entry).__name__}"
                )
            try:
                spec = ModelSpec.model_validate(entry)
            except ValidationError as exc:
                raise RegistryFileError(path_str, f"models[{index}] invalid: {exc}") from exc
            if spec.id in models:
                raise RegistryFileError(path_str, f"duplicate model id '{spec.id}'")
            models[spec.id] = spec

        self._models = models

    def get(self, alias: str) -> ModelSpec:
        try:
            return self._models[alias]
        except KeyError as exc:
            raise UnknownModelAliasError(alias) from exc

    def list_aliases(self) -> list[AliasInfo]:
        """Public projection for the console API + frontend dropdown.

        URL is deliberately omitted (ADR-3 / SSRF defense). Order matches
        yaml insertion order so the UI is stable across reloads.
        """
        return [
            AliasInfo(id=m.id, model_name=m.model_name, type=m.type)
            for m in self._models.values()
        ]

    def __contains__(self, alias: object) -> bool:
        return isinstance(alias, str) and alias in self._models

    def __len__(self) -> int:
        return len(self._models)

    def __repr__(self) -> str:
        return (
            f"LocalModelRegistry(source={self._source_path!r}, "
            f"aliases={list(self._models.keys())})"
        )
