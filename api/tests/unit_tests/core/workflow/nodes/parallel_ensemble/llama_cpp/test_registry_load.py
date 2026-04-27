"""P2.3 — registry-side tests for the ``llama_cpp`` backend.

This file is the formal pytest landing of the smoke checks P2.1 ran
inline (TASKS.md L266 "延后到 P2.3"). It exercises ``ModelRegistry``
through the ``backend: llama_cpp`` dispatch path that ``BackendRegistry``
adds in P2.1.5 — the goal is to pin the *llama_cpp slice* of the
generic ``test_model_registry.py`` tests so a regression that only
breaks the llama.cpp loader (e.g. a yaml field rename in ``LlamaCppSpec``)
fails here with a llama.cpp-flavoured stack rather than a generic one.

Network is never touched: every test writes a yaml file under
``tmp_path`` and consumes it via ``ModelRegistry.for_testing``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.workflow.nodes.parallel_ensemble.backends.llama_cpp import (
    LlamaCppSpec,
)
from core.workflow.nodes.parallel_ensemble.llama_cpp.exceptions import (
    RegistryFileError,
    UnknownModelAliasError,
)
from core.workflow.nodes.parallel_ensemble.registry import ModelRegistry
from core.workflow.nodes.parallel_ensemble.spi.capability import Capability


def _entry(**overrides: object) -> dict[str, object]:
    """One canonical llama_cpp yaml entry; overrides win field-by-field.

    Mirrors the seven-entry shape ``docs/ModelNet/model_info.json``
    publishes plus the new ``backend`` discriminator P2.1.5 added.
    """
    base: dict[str, object] = {
        "id": "alpha",
        "backend": "llama_cpp",
        "model_name": "alpha-model",
        "model_url": "http://internal.test:8080",
        "EOS": "<|eos|>",
        "type": "normal",
    }
    base.update(overrides)
    return base


def _write_yaml(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    path = tmp_path / "model_net.yaml"
    path.write_text(yaml.safe_dump({"models": entries}), encoding="utf-8")
    return path


# ── test_registry_load ────────────────────────────────────────────────


def test_registry_load(tmp_path: Path) -> None:
    """yaml entry with ``backend: llama_cpp`` resolves through the
    BackendRegistry to ``LlamaCppSpec`` and lands in the registry."""
    path = _write_yaml(tmp_path, [_entry(id="alpha"), _entry(id="beta", model_name="beta-model")])
    registry = ModelRegistry.for_testing(str(path))
    assert len(registry) == 2
    spec = registry.get("alpha")
    assert isinstance(spec, LlamaCppSpec)
    assert spec.backend == "llama_cpp"
    assert spec.model_name == "alpha-model"


# ── test_extra_forbid ─────────────────────────────────────────────────


def test_extra_forbid(tmp_path: Path) -> None:
    """``LlamaCppSpec`` inherits ``extra="forbid"`` so an unknown yaml
    key — typo or a smuggled ``api_key`` — is wrapped into the
    framework's ``RegistryFileError`` rather than crashing later."""
    path = _write_yaml(tmp_path, [_entry(rogue="should-be-rejected")])
    with pytest.raises(RegistryFileError) as exc_info:
        ModelRegistry.for_testing(str(path))
    # The pydantic ValidationError message must surface the offending
    # key so an operator can fix the yaml without diffing schemas.
    assert "rogue" in exc_info.value.reason


# ── test_unknown_backend ──────────────────────────────────────────────


def test_unknown_backend(tmp_path: Path) -> None:
    """yaml ``backend: my_zmq`` (not registered) → ``RegistryFileError``
    naming the unknown backend and listing the known ones (so a typo
    produces an actionable error message)."""
    entry = _entry(backend="my_zmq")
    path = _write_yaml(tmp_path, [entry])
    with pytest.raises(RegistryFileError) as exc_info:
        ModelRegistry.for_testing(str(path))
    assert "my_zmq" in exc_info.value.reason
    assert "is not registered" in exc_info.value.reason
    # ``llama_cpp`` is registered at import time via the side-effect
    # import in ``parallel_ensemble.__init__``; the error must surface
    # it as a known backend so the operator sees what they could
    # have meant instead.
    assert "llama_cpp" in exc_info.value.reason


# ── test_unknown_alias ────────────────────────────────────────────────


def test_unknown_alias(tmp_path: Path) -> None:
    """``get`` of a missing alias raises the typed
    ``UnknownModelAliasError`` (not a bare ``KeyError``) so node-layer
    ``except`` blocks can match the exception tree."""
    path = _write_yaml(tmp_path, [_entry()])
    registry = ModelRegistry.for_testing(str(path))
    with pytest.raises(UnknownModelAliasError) as exc_info:
        registry.get("nope")
    assert exc_info.value.alias == "nope"


# ── test_list_aliases_returns_backend_info ────────────────────────────


def test_list_aliases_returns_backend_info(tmp_path: Path) -> None:
    """``list_aliases`` returns the SPI's ``BackendInfo`` projection:
    ``id / backend / model_name / capabilities / metadata`` — never
    ``model_url`` / ``api_key`` (T2 SSRF / credential boundary)."""
    path = _write_yaml(
        tmp_path,
        [
            _entry(id="alpha", model_name="alpha-7b"),
            _entry(id="thinker", model_name="thinker-13b", type="think", stop_think="</think>"),
        ],
    )
    registry = ModelRegistry.for_testing(str(path))
    aliases = registry.list_aliases()
    assert {a["id"] for a in aliases} == {"alpha", "thinker"}
    for info in aliases:
        assert set(info.keys()) == {"id", "backend", "model_name", "capabilities", "metadata"}
        # T2: secrets and URLs must never cross the API boundary.
        assert "model_url" not in info
        assert "api_key" not in info
        assert "api_key_env" not in info
        assert info["backend"] == "llama_cpp"
        # llama.cpp's stock capability set lands as plain strings so the
        # frontend dropdown can render them without importing the
        # ``Capability`` enum.
        assert Capability.TOKEN_STEP.value in info["capabilities"]
        assert Capability.TOP_PROBS.value in info["capabilities"]
        assert Capability.LOGITS_RAW.value not in info["capabilities"]
    # Backend-specific extras (``type`` / ``stop_think``) flow through
    # ``metadata`` so the dropdown can label "think"-mode models without
    # the TypedDict needing a llama.cpp-specific field.
    by_id = {a["id"]: a for a in aliases}
    assert by_id["alpha"]["metadata"] == {"type": "normal"}
    assert by_id["thinker"]["metadata"] == {"type": "think"}
