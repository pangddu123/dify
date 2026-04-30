"""P2.1.5 SPI freeze acceptance — ABC subclassing across the three axes.

Spawns minimal in-test ``Echo`` backend / ``Noop`` runner / ``First``
aggregator, registers each through the matching ``@register_*``
decorator, and verifies the framework can:

  - look the registration back up,
  - reject duplicate registrations,
  - enforce the ``ui_schema`` control allowlist,
  - dispatch ``ModelRegistry._load`` through ``BackendRegistry`` for
    the per-backend pydantic spec class (P2.1 backwards-compat path).

The fakes are deliberately tiny (single-method overrides) — we are
testing the SPI shape, not the implementations that land in P2.2 / P2.5
/ P2.6.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar, Literal

import pytest
from pydantic import BaseModel

from core.workflow.nodes.parallel_ensemble.exceptions import (
    DuplicateRegistrationError,
    UnknownAggregatorError,
    UnknownBackendError,
    UnknownRunnerError,
)
from core.workflow.nodes.parallel_ensemble.registry import (
    AggregatorRegistry,
    BackendRegistry,
    RunnerRegistry,
    register_aggregator,
    register_backend,
    register_runner,
)
from core.workflow.nodes.parallel_ensemble.spi import (
    UI_CONTROL_ALLOWLIST,
    BaseSpec,
    Capability,
    DiagnosticsConfig,
    DoneEvent,
    EnsembleRunner,
    GenerationParams,
    GenerationResult,
    ModelBackend,
    Requirement,
    ResponseAggregationResult,
    ResponseAggregator,
    ResponseSignal,
    RunnerEvent,
    SourceAggregationContext,
    TokenStepParams,
    TraceCollector,
    ValidationIssue,
)

# ── Echo backend ───────────────────────────────────────────────────────


class EchoSpec(BaseSpec):
    backend: Literal["echo_test"]
    suffix: str = "!"


class EchoBackend(ModelBackend):
    spec_class: ClassVar[type[BaseSpec]] = EchoSpec

    @classmethod
    def capabilities(cls, spec: BaseSpec) -> frozenset[Capability]:
        return frozenset({Capability.STREAMING})

    @classmethod
    def validate_requirements(cls, spec: BaseSpec, requirements: list[Requirement]) -> list[ValidationIssue]:
        return []

    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult:
        assert isinstance(self._spec, EchoSpec)
        return GenerationResult(
            text=prompt + self._spec.suffix,
            finish_reason="stop",
            metadata={},
        )


# ── Noop runner ────────────────────────────────────────────────────────


class NoopConfig(BaseModel):
    """Trivial pydantic config so ``config_class`` has something concrete."""

    note: str = "noop"


class NoopRunner(EnsembleRunner[NoopConfig]):
    config_class: ClassVar[type[BaseModel]] = NoopConfig
    aggregator_scope: ClassVar[str] = "response"
    required_capabilities: ClassVar[frozenset[Capability]] = frozenset()
    i18n_key_prefix: ClassVar[str] = "tests.parallel_ensemble.runners.noop"
    ui_schema: ClassVar[dict] = {
        "note": {"control": "text_input"},
    }

    @classmethod
    def requirements(cls, config: NoopConfig) -> list[Requirement]:
        return []

    def run(
        self,
        sources,
        backends: dict[str, ModelBackend],
        aggregator,
        config: NoopConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        del sources
        yield DoneEvent(kind="done", text="", metadata={"note": config.note})


# ── First aggregator ───────────────────────────────────────────────────


class FirstConfig(BaseModel):
    pass


class FirstAggregator(ResponseAggregator[FirstConfig]):
    config_class: ClassVar[type[BaseModel]] = FirstConfig
    i18n_key_prefix: ClassVar[str] = "tests.parallel_ensemble.aggregators.first"
    ui_schema: ClassVar[dict] = {}

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: SourceAggregationContext,
        config: FirstConfig,
    ) -> ResponseAggregationResult:
        first = signals[0]
        return ResponseAggregationResult(text=first["text"], metadata={"picked": first["source_id"]})


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def fresh_registries():
    """Snapshot → empty → yield → restore.

    The registries are process-wide class state and modules register
    themselves through ``@register_*`` decorators that fire only at
    *first* import. A naïve ``reset_for_testing()`` teardown leaves the
    process empty, breaking any later test that expects the production
    registrations from side-effect imports (e.g. P2.6 runner tests
    asserting ``RunnerRegistry.get("token_step")``). Snapshot/restore
    keeps SPI-freeze tests starting from a known-empty slate while
    leaving the rest of the suite to see the real registry contents.
    """
    backends_snapshot = dict(BackendRegistry._backends)
    runners_snapshot = dict(RunnerRegistry._runners)
    aggregators_snapshot = dict(AggregatorRegistry._aggregators)
    BackendRegistry.reset_for_testing()
    RunnerRegistry.reset_for_testing()
    AggregatorRegistry.reset_for_testing()
    try:
        yield
    finally:
        BackendRegistry._backends = backends_snapshot
        RunnerRegistry._runners = runners_snapshot
        AggregatorRegistry._aggregators = aggregators_snapshot


# ── Tests ──────────────────────────────────────────────────────────────


class TestBackendRegistration:
    def test_register_and_lookup(self, fresh_registries):
        register_backend("echo_test")(EchoBackend)
        assert BackendRegistry.get("echo_test") is EchoBackend
        assert BackendRegistry.get_spec_class("echo_test") is EchoSpec
        assert "echo_test" in BackendRegistry.known_backends()

    def test_decorator_sets_class_name(self, fresh_registries):
        register_backend("echo_test")(EchoBackend)
        assert EchoBackend.name == "echo_test"

    def test_duplicate_registration_rejected(self, fresh_registries):
        register_backend("echo_test")(EchoBackend)
        with pytest.raises(DuplicateRegistrationError):
            register_backend("echo_test")(EchoBackend)

    def test_unknown_lookup_raises(self, fresh_registries):
        with pytest.raises(UnknownBackendError) as exc_info:
            BackendRegistry.get("nope")
        assert exc_info.value.key == "nope"

    def test_spec_class_must_subclass_base_spec(self, fresh_registries):
        class BadBackend(ModelBackend):
            spec_class = int  # type: ignore[assignment]

            @classmethod
            def capabilities(cls, spec):
                return frozenset()

            @classmethod
            def validate_requirements(cls, spec, requirements):
                return []

            def generate(self, prompt, params):
                return GenerationResult(text="", finish_reason="stop", metadata={})

        with pytest.raises(TypeError):
            BackendRegistry.register("bad", BadBackend)


class TestRunnerRegistration:
    def test_register_and_lookup(self, fresh_registries):
        register_runner("noop_test")(NoopRunner)
        assert RunnerRegistry.get("noop_test") is NoopRunner
        assert "noop_test" in RunnerRegistry.known_runners()

    def test_unknown_lookup_raises(self, fresh_registries):
        with pytest.raises(UnknownRunnerError):
            RunnerRegistry.get("nope")

    def test_duplicate_rejected(self, fresh_registries):
        register_runner("noop_test")(NoopRunner)
        with pytest.raises(DuplicateRegistrationError):
            register_runner("noop_test")(NoopRunner)

    def test_ui_schema_control_in_allowlist(self):
        for field, decl in NoopRunner.ui_schema.items():
            assert decl["control"] in UI_CONTROL_ALLOWLIST, field

    def test_ui_schema_rejects_off_allowlist_control(self):
        with pytest.raises(ValueError, match="not in the v0.2 allowlist"):

            class BadRunner(EnsembleRunner[NoopConfig]):
                name = "bad"
                config_class = NoopConfig
                aggregator_scope = "response"
                required_capabilities = frozenset()
                i18n_key_prefix = "tests.bad"
                ui_schema = {"x": {"control": "rich_html_editor"}}

                @classmethod
                def requirements(cls, config):
                    return []

                def run(self, sources, backends, aggregator, config, trace):
                    yield DoneEvent(kind="done", text="", metadata={})


class TestAggregatorRegistration:
    def test_register_and_lookup(self, fresh_registries):
        register_aggregator("first_test", scope="response")(FirstAggregator)
        assert AggregatorRegistry.get("first_test") is FirstAggregator
        assert FirstAggregator in AggregatorRegistry.by_scope("response")

    def test_unknown_lookup_raises(self, fresh_registries):
        with pytest.raises(UnknownAggregatorError):
            AggregatorRegistry.get("nope")

    def test_decorator_scope_must_match_class(self, fresh_registries):
        with pytest.raises(ValueError, match="disagrees"):
            register_aggregator("first_test", scope="token")(FirstAggregator)


class TestSpiTypeInvariants:
    """Type-shape sanity that catches regressions from someone editing
    the SPI without realising they are part of the v0.2 freeze."""

    def test_capability_enum_membership(self):
        # Spec §3.1 fixes this set; new entries are an SPI bump.
        assert {c.value for c in Capability} == {
            "streaming",
            "token_step",
            "top_probs",
            "post_sampling_probs",
            "logits_raw",
            "chat_template",
            "function_calling",
            "kv_cache_reuse",
        }

    def test_ui_control_allowlist_is_frozen_set(self):
        assert isinstance(UI_CONTROL_ALLOWLIST, frozenset)
        assert (
            frozenset(
                {
                    "number_input",
                    "text_input",
                    "textarea",
                    "switch",
                    "select",
                    "multi_select",
                    "model_alias_select",
                }
            )
            == UI_CONTROL_ALLOWLIST
        )

    def test_diagnostics_config_extra_forbid(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DiagnosticsConfig(unknown_flag=True)  # type: ignore[call-arg]

    def test_trace_collector_truncation(self):
        cfg = DiagnosticsConfig(max_trace_tokens=3, include_token_candidates=False)
        collector = TraceCollector(cfg)
        for i in range(7):
            collector.record_token_step(
                {
                    "step": i,
                    "selected_token": str(i),
                    "selected_score": 0.0,
                    "elapsed_ms": 0,
                }
            )
        trace = collector.finalize(
            runner_name="noop",
            runner_config={},
            aggregator_name="first",
            aggregator_config={},
            backends=[],
        )
        # last-N retention: keep 3 most recent (4, 5, 6)
        assert [e["selected_token"] for e in trace["token_trace"]] == ["4", "5", "6"]
        assert trace["summary"]["truncated"] is True
        assert trace["summary"]["truncated_token_steps"] == 4


class TestEchoBackendInstance:
    """Smoke-test instance shape: properties, capability projection, generate."""

    def test_instance_properties(self, fresh_registries):
        register_backend("echo_test")(EchoBackend)
        spec = EchoSpec(
            id="e1",
            backend="echo_test",
            model_name="echo",
            suffix="!!",
        )
        backend = EchoBackend(spec, http=None)
        assert backend.id == "e1"
        assert backend.model_name == "echo"
        assert backend.weight == 1.0
        assert backend.instance_capabilities == frozenset({Capability.STREAMING})

    def test_generate_uses_spec(self, fresh_registries):
        register_backend("echo_test")(EchoBackend)
        spec = EchoSpec(id="e1", backend="echo_test", model_name="echo", suffix="?")
        backend = EchoBackend(spec, http=None)
        result = backend.generate("hi", GenerationParams())
        assert result["text"] == "hi?"
        assert result["finish_reason"] == "stop"

    def test_default_step_token_raises_capability_error(self, fresh_registries):
        from core.workflow.nodes.parallel_ensemble.exceptions import CapabilityNotSupportedError

        register_backend("echo_test")(EchoBackend)
        spec = EchoSpec(id="e1", backend="echo_test", model_name="echo", suffix="!")
        backend = EchoBackend(spec, http=None)
        with pytest.raises(CapabilityNotSupportedError) as exc_info:
            backend.step_token("hi", TokenStepParams(top_k=1))
        assert exc_info.value.backend_name == "echo_test"
        assert exc_info.value.capability_name == Capability.TOKEN_STEP.value
