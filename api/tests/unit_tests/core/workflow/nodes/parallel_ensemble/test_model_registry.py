"""ModelRegistry — P2.1.5 acceptance: P2.1 yaml still loads after the
SPI swap from monolithic ``ModelSpec`` to ``BackendRegistry``-dispatched
``LlamaCppSpec``.

Covers the 10 P2.1 inline smoke checks that landed in
``docs/ModelNet/P2.1_LANDING.md`` §90 (now formal pytest per the P2.3
plan), plus the new dispatch path (yaml entry's ``backend`` field drives
the spec class lookup).

The yaml entries are written into ``tmp_path`` and consumed via
``ModelRegistry.for_testing(path)`` so we never poke at the singleton
or rely on ``configs/model_net.yaml`` existing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml

from core.workflow.nodes.parallel_ensemble.backends.llama_cpp import (  # noqa: F401
    LlamaCppSpec,
)
from core.workflow.nodes.parallel_ensemble.exceptions import UnknownBackendError
from core.workflow.nodes.parallel_ensemble.llama_cpp.exceptions import (
    LlamaCppNodeError,
    ModelRegistryError,
    RegistryFileError,
    UnknownModelAliasError,
)
from core.workflow.nodes.parallel_ensemble.registry import (
    BackendRegistry,
    LocalModelRegistry,
    ModelRegistry,
)


REPO_ROOT = Path(__file__).resolve().parents[7]
MODEL_INFO_JSON = REPO_ROOT / "docs" / "ModelNet" / "model_info.json"


def _entries_from_model_info() -> list[dict]:
    """Load the 7-entry PN.py canonical config and add the new ``backend`` field."""
    raw = json.loads(MODEL_INFO_JSON.read_text(encoding="utf-8"))
    return [{**entry, "backend": "llama_cpp"} for entry in raw]


def _write_yaml(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "model_net.yaml"
    path.write_text(yaml.safe_dump({"models": entries}), encoding="utf-8")
    return path


# ── Smoke 1: full PN.py model_info.json loads ─────────────────────────


def test_loads_pn_model_info_seven_entries(tmp_path):
    """Smoke 1: ``LlamaCppSpec`` swallows every PN.py entry once a
    ``backend`` field is added — the only field shape change v0.2 introduces."""
    entries = _entries_from_model_info()
    assert len(entries) == 7
    path = _write_yaml(tmp_path, entries)
    registry = ModelRegistry.for_testing(str(path))
    assert len(registry) == 7
    assert all(isinstance(spec, LlamaCppSpec) for spec in registry._models.values())


# ── Smoke 2: extra-forbid rejects unknown keys ────────────────────────


def test_extra_forbid_rejects_unknown_yaml_key(tmp_path):
    entries = [
        {
            "id": "x",
            "backend": "llama_cpp",
            "model_name": "m",
            "model_url": "http://h:1",
            "EOS": "<eos>",
            "rogue": "smuggle",
        }
    ]
    path = _write_yaml(tmp_path, entries)
    with pytest.raises(RegistryFileError) as exc_info:
        ModelRegistry.for_testing(str(path))
    assert "rogue" in exc_info.value.reason


# ── Smoke 3: list_aliases hides url ───────────────────────────────────


def test_list_aliases_omits_url(tmp_path):
    entries = _entries_from_model_info()
    path = _write_yaml(tmp_path, entries)
    registry = ModelRegistry.for_testing(str(path))
    aliases = registry.list_aliases()
    assert len(aliases) == 7
    for info in aliases:
        # Critical SSRF defense (T2): URL must never cross the API boundary.
        assert "model_url" not in info
        assert "url" not in info
        assert set(info.keys()) == {"id", "backend", "model_name", "type"}


# ── Smoke 4: unknown alias raises typed error ─────────────────────────


def test_get_unknown_alias_raises_typed_error(tmp_path):
    path = _write_yaml(tmp_path, [])
    registry = ModelRegistry.for_testing(str(path))
    with pytest.raises(UnknownModelAliasError) as exc_info:
        registry.get("nope")
    assert exc_info.value.alias == "nope"
    # Hierarchy preserved from P2.1 so existing `except` blocks still match.
    assert isinstance(exc_info.value, ModelRegistryError)
    assert isinstance(exc_info.value, LlamaCppNodeError)


# ── Smoke 5: missing yaml → empty + warning, no crash (R9) ────────────


def test_missing_yaml_logs_warning_and_keeps_registry_empty(tmp_path, caplog):
    missing = tmp_path / "does_not_exist.yaml"
    with caplog.at_level(logging.WARNING):
        registry = ModelRegistry.for_testing(str(missing))
    assert len(registry) == 0
    assert any("not found" in rec.message for rec in caplog.records)


# ── Smoke 6: duplicate id rejected ────────────────────────────────────


def test_duplicate_id_rejected(tmp_path):
    entry = {
        "id": "dup",
        "backend": "llama_cpp",
        "model_name": "m",
        "model_url": "http://h:1",
        "EOS": "<eos>",
    }
    path = _write_yaml(tmp_path, [entry, dict(entry)])
    with pytest.raises(RegistryFileError, match="duplicate model id 'dup'"):
        ModelRegistry.for_testing(str(path))


# ── Smoke 7: rogue field is the same as Smoke 2 by construction; keep ─
#  Smoke 8: malformed yaml → RegistryFileError ────────────────────────


def test_malformed_yaml_wraps_yaml_error(tmp_path):
    path = tmp_path / "model_net.yaml"
    path.write_text("models: [\nthis is not yaml", encoding="utf-8")
    with pytest.raises(RegistryFileError) as exc_info:
        ModelRegistry.for_testing(str(path))
    assert exc_info.value.path == str(path)


# ── Smoke 9: empty yaml file → empty registry, no error ───────────────


def test_empty_yaml_file_yields_empty_registry(tmp_path):
    path = tmp_path / "model_net.yaml"
    path.write_text("", encoding="utf-8")
    registry = ModelRegistry.for_testing(str(path))
    assert len(registry) == 0


# ── Smoke 10: instance() singleton identity ───────────────────────────


def test_instance_returns_singleton():
    """Two ``instance()`` calls return the same object identity.

    Default ``_resolve_path`` lands on ``api/configs/model_net.yaml`` which
    is intentionally absent in dev (R9 — empty registry + warning), so
    the singleton initialises deterministically without any monkeypatching.
    """
    ModelRegistry.reset_for_testing()
    try:
        first = ModelRegistry.instance()
        second = ModelRegistry.instance()
        assert first is second
    finally:
        ModelRegistry.reset_for_testing()


# ── New: BackendRegistry dispatch ─────────────────────────────────────


def test_unknown_backend_in_yaml_rejected(tmp_path):
    """SPI gain: yaml ``backend`` field must reference a registered backend."""
    entries = [
        {
            "id": "x",
            "backend": "totally_made_up",
            "model_name": "m",
            "model_url": "http://h:1",
            "EOS": "<eos>",
        }
    ]
    path = _write_yaml(tmp_path, entries)
    with pytest.raises(RegistryFileError) as exc_info:
        ModelRegistry.for_testing(str(path))
    assert "totally_made_up" in exc_info.value.reason
    assert "is not registered" in exc_info.value.reason


def test_missing_backend_field_rejected(tmp_path):
    entries = [
        {
            "id": "x",
            "model_name": "m",
            "model_url": "http://h:1",
            "EOS": "<eos>",
        }
    ]
    path = _write_yaml(tmp_path, entries)
    with pytest.raises(RegistryFileError, match="missing or empty 'backend' field"):
        ModelRegistry.for_testing(str(path))


def test_backend_registry_resolves_llama_cpp_spec_class():
    """BackendRegistry.get_spec_class returns the same class import sees."""
    assert BackendRegistry.get_spec_class("llama_cpp") is LlamaCppSpec


def test_unknown_backend_lookup_raises_typed_error():
    with pytest.raises(UnknownBackendError) as exc_info:
        BackendRegistry.get_spec_class("nope_nope_nope")
    assert exc_info.value.key == "nope_nope_nope"


def test_local_model_registry_alias_preserved():
    """Backwards-compat alias kept for one release per TASKS.md L267."""
    assert LocalModelRegistry is ModelRegistry
