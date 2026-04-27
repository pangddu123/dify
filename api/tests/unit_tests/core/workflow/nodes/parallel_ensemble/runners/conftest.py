"""Shared fixtures for P2.6 runner tests.

Builds a fake :class:`ModelBackend` whose ``step_token`` / ``generate``
return scripted candidate lists, so the test exercises the runner's
fan-out + aggregation path without standing up an HTTP layer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from core.workflow.nodes.parallel_ensemble.spi.backend import (
    GenerationParams,
    GenerationResult,
    ModelBackend,
    TokenCandidate,
)
from core.workflow.nodes.parallel_ensemble.spi.capability import Capability


class _FakeSpec:
    """Stand-in for ``BaseSpec`` carrying just the fields the runner reads.

    A real ``LlamaCppSpec`` would over-constrain the test (URL field is
    required + validated); the runner only touches ``id`` / ``model_name``
    / ``weight`` / ``type`` / ``stop_think`` indirectly, so a duck-typed
    object keeps the fixture small.
    """

    def __init__(
        self,
        id: str,
        weight: float = 1.0,
        type: str = "normal",
        stop_think: str | None = None,
    ) -> None:
        self.id = id
        self.model_name = id
        self.weight = weight
        self.type = type
        self.stop_think = stop_think


class FakeBackend(ModelBackend):
    """Backend whose ``step_token`` pulls from a queue of scripted candidate lists."""

    name = "fake"

    def __init__(
        self,
        alias: str,
        scripted_steps: Iterable[list[TokenCandidate]] | None = None,
        scripted_generate: GenerationResult | None = None,
        capabilities: frozenset[Capability] = frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS}),
        weight: float = 1.0,
        spec_type: str = "normal",
        stop_think: str | None = None,
        step_raises: list[Exception | None] | None = None,
        always_emit: list[TokenCandidate] | None = None,
    ) -> None:
        spec = _FakeSpec(id=alias, weight=weight, type=spec_type, stop_think=stop_think)
        super().__init__(spec=spec, http=None)  # type: ignore[arg-type]
        self._scripted_steps = list(scripted_steps or [])
        self._scripted_generate = scripted_generate or GenerationResult(text="", finish_reason="stop", metadata={})
        self._caps = capabilities
        self._step_raises = list(step_raises or [])
        self._always_emit = always_emit
        self._step_idx = 0
        self.step_calls: list[tuple[str, int]] = []
        self.generate_calls: list[tuple[str, GenerationParams]] = []
        self.template_calls: list[list[dict[str, str]]] = []

    @classmethod
    def capabilities(cls, spec: Any) -> frozenset[Capability]:
        return frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS})

    @property
    def instance_capabilities(self) -> frozenset[Capability]:  # type: ignore[override]
        return self._caps

    @classmethod
    def validate_requirements(cls, spec: Any, requirements: Any) -> list:
        return []

    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult:
        self.generate_calls.append((prompt, params))
        return self._scripted_generate

    def step_token(self, prompt: str, top_k: int) -> list[TokenCandidate]:
        self.step_calls.append((prompt, top_k))
        if self._always_emit is not None:
            return list(self._always_emit)
        if self._step_idx < len(self._step_raises):
            exc = self._step_raises[self._step_idx]
            if exc is not None:
                self._step_idx += 1
                raise exc
        if self._step_idx >= len(self._scripted_steps):
            # Default tail: emit <end> so the runner terminates cleanly
            # if the test under-supplies steps.
            return [TokenCandidate(token="<end>", prob=1.0, logit=None)]
        out = self._scripted_steps[self._step_idx]
        self._step_idx += 1
        return out

    def apply_template(self, messages: list) -> str:  # type: ignore[override]
        self.template_calls.append([dict(m) for m in messages])
        # Minimal template: join role/content for determinism in tests.
        return "\n\n".join(f"{m['role']}: {m['content']}" for m in messages)


@pytest.fixture
def cand() -> Callable[..., TokenCandidate]:
    def _build(token: str, prob: float, logit: float | None = None) -> TokenCandidate:
        return TokenCandidate(token=token, prob=prob, logit=logit)

    return _build


@pytest.fixture
def executor():
    """Single-thread executor — concurrency irrelevant for unit tests, and
    serial execution makes failures reproducible."""
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        yield pool
    finally:
        pool.shutdown(wait=True)
