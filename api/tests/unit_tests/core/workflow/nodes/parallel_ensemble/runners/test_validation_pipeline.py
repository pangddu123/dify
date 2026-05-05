"""P2.7 §9 validation pipeline contract — capability filter +
requirements validation + cross-field model selection.

The §9 capability / requirements pipeline runs at startup inside
``ParallelEnsembleNode._run`` (P2.8). Here we exercise the SPI in
isolation so the pieces the node will compose are pinned independently
of the not-yet-landed node code:

* a runner whose ``required_capabilities`` are not satisfied by a
  backend's capability set produces a non-empty diff that the framework
  wraps in :class:`StructuredValidationError`;
* a backend's ``validate_requirements`` returns structured issues for
  requirement kinds it cannot fulfil (``LlamaCppBackend`` already has
  per-method coverage in ``test_llama_cpp_backend.py``; this file pins
  the pairing with a runner-derived requirement list);
* a runner that overrides ``validate_selection`` for cross-field rules
  (judge-style: ``judge_alias`` must be in ``model_aliases``) emits a
  :class:`ValidationIssue` when the rule is violated.

Why a synthetic ``_JudgeRunner`` instead of waiting for the real one
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The judge-style runner is documented in EXTENSIBILITY_SPEC §5.3 as the
canonical example of cross-field validation but is not shipped in the
box (post-ADR-v3-9 only ``token_step`` is registered; the response-mode
runner moved out to the standalone response-aggregator node). The
TASKS.md P2.7 list still requires the pattern to be tested so
third-party authors know the SPI hook works as advertised; the
synthetic runner here captures that contract without front-running the
v0.3 judge runner's exact config shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from core.workflow.nodes.parallel_ensemble.backends.llama_cpp import (
    LlamaCppBackend,
    LlamaCppSpec,
)
from core.workflow.nodes.parallel_ensemble.exceptions import (
    StructuredValidationError,
)
from core.workflow.nodes.parallel_ensemble.runners.token_step import (
    TokenStepConfig,
    TokenStepRunner,
)
from core.workflow.nodes.parallel_ensemble.spi.aggregator import Aggregator
from core.workflow.nodes.parallel_ensemble.spi.backend import (
    GenerationParams,
    GenerationResult,
    ModelBackend,
)
from core.workflow.nodes.parallel_ensemble.spi.capability import Capability
from core.workflow.nodes.parallel_ensemble.spi.requirements import (
    Requirement,
    ValidationIssue,
)
from core.workflow.nodes.parallel_ensemble.spi.runner import (
    DoneEvent,
    EnsembleRunner,
    RunnerEvent,
    SourceInput,
)
from core.workflow.nodes.parallel_ensemble.spi.trace import TraceCollector

# ── Fixtures ──────────────────────────────────────────────────────────


def _make_llama_spec() -> LlamaCppSpec:
    """Minimal valid llama.cpp spec for capability / requirements probes."""
    return LlamaCppSpec.model_validate(
        {
            "id": "llama_test",
            "backend": "llama_cpp",
            "model_name": "test",
            "model_url": "http://internal.test:8080",
            "EOS": "<|eot|>",
        }
    )


class _LimitedBackend(ModelBackend):
    """Backend declaring only ``STREAMING`` — missing TOKEN_STEP / TOP_PROBS.

    Defined here (not in conftest) because every other backend in the
    test suite declares a richer capability set; this one exists solely
    to drive the §9 capability filter into the missing-cap branch.
    """

    name = "_limited"
    spec_class: ClassVar[type[LlamaCppSpec]] = LlamaCppSpec  # type: ignore[assignment]

    @classmethod
    def capabilities(cls, spec: Any) -> frozenset[Capability]:
        del spec
        return frozenset({Capability.STREAMING})

    @classmethod
    def validate_requirements(cls, spec: Any, requirements: list[Requirement]) -> list[ValidationIssue]:
        del spec, requirements
        return []

    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult:
        del prompt, params
        return GenerationResult(text="", finish_reason="stop", metadata={})


# ── §9 capability filter ──────────────────────────────────────────────


def test_capability_filter():
    """Runner.required - backend.capabilities ≠ ∅ → StructuredValidationError.

    Pins the §9 set-diff contract: the framework computes ``required -
    declared`` per-backend; a non-empty result means the backend cannot
    fulfil the runner and must be rejected before the run starts. The
    error wrapper preserves the issue list so the panel can show every
    rejection at once rather than one at a time on rerun.
    """
    spec = _make_llama_spec()
    backend_caps = _LimitedBackend.capabilities(spec)
    required = TokenStepRunner.required_capabilities

    missing = required - backend_caps
    # token_step needs TOKEN_STEP + TOP_PROBS; _LimitedBackend declares
    # STREAMING only, so both are absent.
    assert missing == frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS})

    issues: list[ValidationIssue] = [
        {
            "severity": "error",
            "requirement": {
                "kind": "needs_logprobs",
                "value": True,
                "rationale": f"runner needs {cap.value} but backend lacks it",
            },
            "message": f"backend missing capability {cap.value}",
            "i18n_key": None,
        }
        for cap in sorted(missing, key=lambda c: c.value)
    ]
    err = StructuredValidationError(issues)
    assert err.issues == issues
    # Aggregated message format: first issue + "(+N more)" suffix for the rest.
    assert "missing capability" in err.message
    assert "(+1 more)" in err.message


def test_capability_filter_passes_when_backend_satisfies_runner():
    """Sanity flip-side: a backend that declares the full required set
    leaves an empty diff, which the framework treats as a pass."""
    spec = _make_llama_spec()
    declared = LlamaCppBackend.capabilities(spec)
    missing = TokenStepRunner.required_capabilities - declared
    assert missing == frozenset()


# ── Backend.validate_requirements ─────────────────────────────────────


def test_requirements_validation_returns_validation_issue_list():
    """``backend.validate_requirements(spec, [...])`` returns a structured
    ``ValidationIssue`` list — empty when every requirement is satisfied.

    Two probes:
      * a hand-rolled ``min_top_k=25`` requirement (the TASKS.md P2.7
        wording) — llama.cpp has no top-k cap so this returns ``[]``;
      * a runner-derived list from ``TokenStepRunner.requirements`` so
        the test pins the runner ↔ backend wiring (``min_top_k`` /
        ``needs_logprobs``) the §9 pipeline will compose at startup.

    The rejection half (``needs_function_calling=True`` → error) is
    covered by ``test_llama_cpp_backend.py::test_needs_function_calling_rejected``;
    here we pin the no-false-positive half end-to-end.
    """
    spec = _make_llama_spec()

    handrolled: list[Requirement] = [{"kind": "min_top_k", "value": 25, "rationale": "test"}]
    issues = LlamaCppBackend.validate_requirements(spec, handrolled)
    assert isinstance(issues, list)
    assert issues == []

    runner_derived = TokenStepRunner.requirements(TokenStepConfig(max_len=10))
    issues = LlamaCppBackend.validate_requirements(spec, runner_derived)
    assert isinstance(issues, list)
    assert all(isinstance(i, dict) and "severity" in i for i in issues)
    assert issues == []


# ── Cross-field selection rule (judge-style runner pattern) ───────────


class _JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judge_alias: str


class _JudgeRunner(EnsembleRunner[_JudgeConfig]):
    """Test-only judge-style runner; pins the cross-field validation
    pattern third-party runners can use without front-running v0.3."""

    name = "_judge"
    config_class: ClassVar[type[BaseModel]] = _JudgeConfig
    aggregator_scope: ClassVar[str] = "response"
    required_capabilities: ClassVar[frozenset[Capability]] = frozenset()

    i18n_key_prefix: ClassVar[str] = "test.judge"
    ui_schema: ClassVar[dict] = {"judge_alias": {"control": "model_alias_select"}}

    @classmethod
    def requirements(cls, config: _JudgeConfig) -> list[Requirement]:
        del config
        return []

    @classmethod
    def validate_selection(
        cls,
        config: _JudgeConfig,
        model_aliases: list[str],
        registry: Any,
    ) -> list[ValidationIssue]:
        del registry
        if config.judge_alias in model_aliases:
            return []
        return [
            {
                "severity": "error",
                "requirement": {
                    "kind": "model_allowlist",
                    "value": list(model_aliases),
                    "rationale": "judge_alias must be one of the selected models",
                },
                "message": (f"judge_alias '{config.judge_alias}' is not in model_aliases {model_aliases}"),
                "i18n_key": "parallelEnsemble.errors.judgeAliasNotInModels",
            }
        ]

    def run(
        self,
        sources: dict[str, SourceInput],
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: _JudgeConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        del sources, backends, aggregator, config, trace
        yield DoneEvent(kind="done", text="", metadata={})


def test_validate_selection_judge_alias_not_in_models():
    """Judge runner: ``judge_alias`` must reference one of the selected aliases."""
    issues = _JudgeRunner.validate_selection(
        _JudgeConfig(judge_alias="oracle"),
        ["a", "b"],
        registry=None,
    )
    assert len(issues) == 1
    issue = issues[0]
    assert issue["severity"] == "error"
    assert "oracle" in issue["message"]
    assert issue["i18n_key"] == "parallelEnsemble.errors.judgeAliasNotInModels"
    assert issue["requirement"]["kind"] == "model_allowlist"


def test_validate_selection_judge_alias_in_models_passes():
    """Happy path: judge_alias references a selected alias → no issues."""
    issues = _JudgeRunner.validate_selection(
        _JudgeConfig(judge_alias="b"),
        ["a", "b"],
        registry=None,
    )
    assert issues == []
