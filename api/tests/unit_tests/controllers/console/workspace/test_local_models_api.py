"""Unit tests for the three parallel-ensemble console APIs.

P2.4 brings up three sibling endpoints; we co-locate their tests in
one file because each has the same shape (one ``GET``, no params,
projects a registry into JSON) and each depends on the same SPI
imports — splitting the file would just multiply the boilerplate.

The tests bypass auth decorators with ``unwrap`` (matches the
``test_agent_providers.py`` pattern in this directory). What we
actually want to verify here is the projection contract — secrets
omitted, ui_schema controls inside the v0.2 allowlist, scope tag
intact — not the auth wraps, which are exhaustively covered elsewhere.
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import patch

import pytest
from pydantic import AnyUrl, BaseModel, ConfigDict

from controllers.console.workspace.aggregators import AggregatorsApi
from controllers.console.workspace.local_models import LocalModelsApi
from controllers.console.workspace.runners import RunnersApi
from core.workflow.nodes.parallel_ensemble.backends.llama_cpp import LlamaCppBackend, LlamaCppSpec
from core.workflow.nodes.parallel_ensemble.registry import (
    AggregatorRegistry,
    BackendRegistry,
    ModelRegistry,
    RunnerRegistry,
)
from core.workflow.nodes.parallel_ensemble.spi.aggregator import (
    ResponseAggregationResult,
    ResponseAggregator,
    ResponseSignal,
    SourceAggregationContext,
)
from core.workflow.nodes.parallel_ensemble.spi.capability import Capability
from core.workflow.nodes.parallel_ensemble.spi.requirements import Requirement
from core.workflow.nodes.parallel_ensemble.spi.runner import (
    UI_CONTROL_ALLOWLIST,
    DoneEvent,
    EnsembleRunner,
)


def _unwrap(func):
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func


# ── Test runner / aggregator (no real impl; just metadata + ui_schema) ──
class _FakeRunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_len: int = 64
    enable_think: bool = False


class _FakeAggregatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skip_empty_voters: bool = True


class _FakeRunner(EnsembleRunner[_FakeRunnerConfig]):
    name: ClassVar[str] = "fake_runner"
    config_class: ClassVar[type[BaseModel]] = _FakeRunnerConfig
    aggregator_scope: ClassVar[str] = "response"
    required_capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.STREAMING})
    optional_capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.CHAT_TEMPLATE})
    i18n_key_prefix: ClassVar[str] = "workflow.nodes.parallel_ensemble.runners.fake_runner"
    ui_schema: ClassVar[dict] = {
        "max_len": {"control": "number_input", "min": 1, "max": 1024, "step": 1},
        "enable_think": {"control": "switch"},
    }

    @classmethod
    def requirements(cls, config: _FakeRunnerConfig) -> list[Requirement]:
        return []

    def run(self, sources, backends, aggregator, config, trace):  # pragma: no cover - not exercised
        yield DoneEvent(kind="done", text="", metadata={})


class _FakeAggregator(ResponseAggregator[_FakeAggregatorConfig]):
    name: ClassVar[str] = "fake_aggregator"
    config_class: ClassVar[type[BaseModel]] = _FakeAggregatorConfig
    i18n_key_prefix: ClassVar[str] = "workflow.nodes.parallel_ensemble.aggregators.fake_aggregator"
    ui_schema: ClassVar[dict] = {
        "skip_empty_voters": {"control": "switch"},
    }

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: SourceAggregationContext,
        config: _FakeAggregatorConfig,
    ) -> ResponseAggregationResult:  # pragma: no cover - not exercised
        return ResponseAggregationResult(text="", metadata={})


@pytest.fixture(autouse=True)
def _ensure_llama_cpp_backend_registered():
    """Re-register llama_cpp if a sibling test wiped the BackendRegistry.

    ``test_spi_freeze.py`` and ``test_llama_cpp_backend.py`` call
    ``BackendRegistry.reset_for_testing()`` between tests, so when this
    file runs after them the ``LlamaCppBackend`` registration is gone
    and ``list_aliases()`` fails to resolve the backend class. Re-
    registering here keeps the projection logic exercisable in
    isolation; we restore on teardown so we never accumulate registrations.
    """
    pre_existing = "llama_cpp" in BackendRegistry._backends
    if not pre_existing:
        BackendRegistry.register("llama_cpp", LlamaCppBackend)
    try:
        yield
    finally:
        if not pre_existing:
            BackendRegistry._backends.pop("llama_cpp", None)


@pytest.fixture
def registered_fake_runner():
    """Register a fake runner for one test, then remove it.

    The registry is process-wide; reset_for_testing wipes everything,
    but other unit tests in the same pytest session may have registered
    real runners (P2.6 future). We surgically pop just our key so the
    test stays cooperative with parallel test layouts.
    """
    RunnerRegistry.register("fake_runner", _FakeRunner)
    try:
        yield _FakeRunner
    finally:
        RunnerRegistry._runners.pop("fake_runner", None)


@pytest.fixture
def registered_fake_aggregator():
    AggregatorRegistry.register("fake_aggregator", _FakeAggregator)
    try:
        yield _FakeAggregator
    finally:
        AggregatorRegistry._aggregators.pop("fake_aggregator", None)


def _build_seeded_registry() -> ModelRegistry:
    reg = ModelRegistry()
    reg._models = {
        "qwen3-think": LlamaCppSpec(
            id="qwen3-think",
            backend="llama_cpp",
            model_name="qwen3",
            model_url=AnyUrl("http://127.0.0.1:8081"),
            EOS="<|im_end|>",
            type="think",
            stop_think="</think>",
        ),
        "llama3-normal": LlamaCppSpec(
            id="llama3-normal",
            backend="llama_cpp",
            model_name="llama3",
            model_url=AnyUrl("http://127.0.0.1:8082"),
            EOS="<|eot_id|>",
            type="normal",
        ),
    }
    return reg


# ── LocalModelsApi ────────────────────────────────────────────────────


class TestLocalModelsApi:
    """``GET /workspaces/current/local-models`` returns BackendInfo-shape entries."""

    def test_returns_models_without_url_or_api_key(self, app):
        api = LocalModelsApi()
        method = _unwrap(api.get)

        seeded = _build_seeded_registry()
        with (
            app.test_request_context("/"),
            patch.object(ModelRegistry, "instance", return_value=seeded),
        ):
            result = method(api)

        assert "models" in result
        models = result["models"]
        assert len(models) == 2

        ids = {m["id"] for m in models}
        assert ids == {"qwen3-think", "llama3-normal"}

        # T2 SSRF / credential boundary: never expose URL nor API keys.
        for entry in models:
            for forbidden in ("model_url", "url", "api_key", "api_key_env", "endpoint"):
                assert forbidden not in entry, f"{forbidden!r} leaked into list_aliases output"
            assert set(entry.keys()) == {"id", "backend", "model_name", "capabilities", "metadata"}

        # llama.cpp's per-spec ``type`` flows through ``metadata`` (P2.3 contract).
        by_id = {m["id"]: m for m in models}
        assert by_id["qwen3-think"]["metadata"]["type"] == "think"
        assert by_id["llama3-normal"]["metadata"]["type"] == "normal"

    def test_returns_empty_list_when_registry_empty(self, app):
        api = LocalModelsApi()
        method = _unwrap(api.get)

        empty = ModelRegistry()
        with (
            app.test_request_context("/"),
            patch.object(ModelRegistry, "instance", return_value=empty),
        ):
            result = method(api)

        assert result == {"models": []}


# ── RunnersApi ────────────────────────────────────────────────────────


class TestRunnersApi:
    """``GET /workspaces/current/runners`` projects EnsembleRunner subclasses."""

    def test_returns_runner_descriptor(self, app, registered_fake_runner):
        api = RunnersApi()
        method = _unwrap(api.get)

        with app.test_request_context("/"):
            result = method(api)

        assert "runners" in result
        names = [r["name"] for r in result["runners"]]
        assert "fake_runner" in names

        runner = next(r for r in result["runners"] if r["name"] == "fake_runner")
        # Frontend reflective form needs all of these — see P2.11.
        assert set(runner.keys()) == {
            "name",
            "i18n_key_prefix",
            "ui_schema",
            "config_schema",
            "aggregator_scope",
            "required_capabilities",
            "optional_capabilities",
        }
        assert runner["aggregator_scope"] == "response"
        assert runner["i18n_key_prefix"].startswith("workflow.nodes.parallel_ensemble.")
        # Capabilities surface as plain strings (the Capability enum's value),
        # so the frontend doesn't need to import the Python enum.
        assert runner["required_capabilities"] == ["streaming"]
        assert runner["optional_capabilities"] == ["chat_template"]

    def test_ui_schema_controls_within_v02_allowlist(self, app, registered_fake_runner):
        """P2.4 acceptance bar: ``runners[].ui_schema`` controls all in v0.2 allowlist."""
        api = RunnersApi()
        method = _unwrap(api.get)

        with app.test_request_context("/"):
            result = method(api)

        for runner in result["runners"]:
            for field, decl in runner["ui_schema"].items():
                control = decl.get("control")
                assert control in UI_CONTROL_ALLOWLIST, (
                    f"runner {runner['name']!r} ui_schema[{field!r}].control={control!r} "
                    f"is outside the v0.2 allowlist {sorted(UI_CONTROL_ALLOWLIST)}"
                )

    def test_config_schema_is_pydantic_json_schema(self, app, registered_fake_runner):
        api = RunnersApi()
        method = _unwrap(api.get)

        with app.test_request_context("/"):
            result = method(api)

        runner = next(r for r in result["runners"] if r["name"] == "fake_runner")
        schema = runner["config_schema"]
        # Pydantic v2's model_json_schema sets these top-level keys.
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "max_len" in schema["properties"]
        assert "enable_think" in schema["properties"]


# ── AggregatorsApi ────────────────────────────────────────────────────


class TestAggregatorsApi:
    """``GET /workspaces/current/aggregators`` projects Aggregator subclasses."""

    def test_returns_aggregator_descriptor(self, app, registered_fake_aggregator):
        api = AggregatorsApi()
        method = _unwrap(api.get)

        with app.test_request_context("/"):
            result = method(api)

        assert "aggregators" in result
        names = [a["name"] for a in result["aggregators"]]
        assert "fake_aggregator" in names

        agg = next(a for a in result["aggregators"] if a["name"] == "fake_aggregator")
        assert set(agg.keys()) == {"name", "i18n_key_prefix", "ui_schema", "config_schema", "scope"}
        # Pairing with a runner happens by string match between
        # runner.aggregator_scope and aggregator.scope; the frontend uses
        # this field to filter the dropdown after a runner is chosen.
        assert agg["scope"] == "response"

    def test_ui_schema_controls_within_v02_allowlist(self, app, registered_fake_aggregator):
        api = AggregatorsApi()
        method = _unwrap(api.get)

        with app.test_request_context("/"):
            result = method(api)

        for agg in result["aggregators"]:
            for field, decl in agg["ui_schema"].items():
                control = decl.get("control")
                assert control in UI_CONTROL_ALLOWLIST, (
                    f"aggregator {agg['name']!r} ui_schema[{field!r}].control={control!r} "
                    f"is outside the v0.2 allowlist {sorted(UI_CONTROL_ALLOWLIST)}"
                )


# ── Auth-bypass smoke (so a future decorator change can't silently 401) ─


def test_decorators_chain_intact():
    """Each Resource exposes a ``__wrapped__`` chain that ``unwrap`` can peel.

    If a future refactor drops the ``functools.wraps`` plumbing on any of
    the four decorators, the unwrap-based unit tests above silently
    invoke the still-decorated method and the auth bypass breaks.
    Asserting the chain depth keeps that failure mode loud.
    """
    for api_cls in (LocalModelsApi, RunnersApi, AggregatorsApi):
        depth = 0
        node = api_cls.get
        while hasattr(node, "__wrapped__"):
            node = node.__wrapped__
            depth += 1
        # 3 decorators: setup_required, login_required, account_initialization_required
        assert depth >= 3, f"{api_cls.__name__}.get only has {depth} wrappers; expected ≥3"


# ── Sanity: the Resource gets discovered via console/__init__ ─────────


def test_resources_registered_in_console_blueprint():
    """Importing ``controllers.console`` must surface our three modules.

    The console blueprint enumerates workspace controllers explicitly;
    forgetting to add a new module silently drops the route from the
    OpenAPI spec at startup. We assert the module is visible from the
    console namespace import to catch that at unit test time.
    """
    from controllers.console import (
        aggregators as aggregators_mod,
    )
    from controllers.console import (
        local_models as local_models_mod,
    )
    from controllers.console import (
        runners as runners_mod,
    )

    assert local_models_mod is not None
    assert runners_mod is not None
    assert aggregators_mod is not None
