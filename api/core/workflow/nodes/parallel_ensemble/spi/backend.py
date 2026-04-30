"""ModelBackend SPI — EXTENSIBILITY_SPEC §4.

A backend instance is created per ``(alias x workflow run)`` and owns the
pieces a runner is *not* allowed to read directly: the URL, the http
client, the api key. Public properties (``id`` / ``model_name`` /
``weight`` / ``instance_capabilities``) project the parts that runner /
aggregator code legitimately needs.

The base ``BaseSpec`` is the discriminator-bearing parent of every
per-backend pydantic spec. ``ModelRegistry._load`` looks up the
``backend`` string against ``BackendRegistry.get_spec_class`` and
delegates ``model_validate`` to the resulting subclass — there is no
``Annotated[Union[...]]`` because that would freeze the universe at
import time and make third-party backends unreachable.

Why TypedDicts (``ChatMessage`` / ``GenerationParams`` / ...) and not
Pydantic models? They cross the runner ↔ backend boundary as plain
dicts; using TypedDicts keeps the wire shape narrow without forcing
extension authors to import a Pydantic class just to construct a
parameter dict.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..exceptions import CapabilityNotSupportedError
from .capability import Capability

if TYPE_CHECKING:  # only for type hints; avoids runtime cycle
    from .requirements import Requirement, ValidationIssue


class BaseSpec(BaseModel):
    """Base for every backend-specific spec; subclasses add fields + ``backend`` Literal.

    ``extra="forbid"`` rejects yaml typos at boot (same justification as
    the P2.1 ``ModelSpec``). ``frozen=True`` lets the parsed spec be
    shared safely across the worker thread pool a token-step runner
    spins up.

    Concrete subclasses (``LlamaCppSpec``, future ``VllmSpec`` etc.)
    pin ``backend`` to a ``Literal[...]`` so pydantic catches a yaml
    file pointing at the wrong backend.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    weight: float = Field(default=1.0, gt=0.0)
    request_timeout_ms: int = Field(default=30000, gt=0)


class ChatMessage(TypedDict):
    """One chat-style message before template application."""

    role: str
    content: str


class GenerationParams(TypedDict, total=False):
    """Sampling knobs accepted by ``generate`` / ``generate_stream``.

    All fields optional: the backend supplies its own defaults for what
    the caller omits. ``top_k`` is only meaningful for ``TOKEN_STEP``-
    style runners.
    """

    max_tokens: int
    temperature: float
    top_p: float
    top_k: int
    stop: list[str]
    seed: int | None


class TokenStepParams(BaseModel):
    """Per-call sampling knobs handed to ``ModelBackend.step_token`` (P3.B.0 / ADR-v3-14).

    PN.py-style joint runners advance one token at a time; the backend
    needs sampling state *per call* (not per-instance) so research code
    that wants "same model, different temperatures for self-consistency"
    can vary knobs across the same backend instance without rebuilding
    it. ``frozen=True`` so a misbehaving backend cannot mutate the
    runner's params dict; ``extra="forbid"`` rejects yaml typos that
    would otherwise silently no-op.

    Fields are deliberately the cross-backend intersection (top_k /
    temperature / top_p / max_tokens / stop / seed). Backend-specific
    knobs (e.g. mirostat, repetition penalty) ride on ``extra``; the
    backend reads what it understands and ignores the rest. ``max_tokens``
    defaults to 1 because step_token is by definition a single-token
    advance — think_phase passes a much larger value for the chain-of-
    thought pre-pass via ``backend.generate``.

    ``extra`` is wrapped in ``MappingProxyType`` after validation so a
    backend that pokes at the dict in-place cannot leak state to a
    sibling backend running the same params instance — the runner
    fan-out submits one params reference to N concurrent
    ``step_token`` calls, so a mutable dict there would be a
    cross-thread aliasing trap. ``stop`` is already a frozen tuple by
    construction; the rest are immutable scalars.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    top_k: int = Field(default=5, gt=0)
    temperature: float | None = Field(default=None, ge=0.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    max_tokens: int = Field(default=1, gt=0)
    stop: tuple[str, ...] = Field(default=())
    seed: int | None = None
    extra: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("extra", mode="after")
    @classmethod
    def _freeze_extra(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        # Defensive copy detaches the proxy from any caller-held dict
        # that could be mutated after construction; ``MappingProxyType``
        # then makes the read-only contract enforceable at runtime, not
        # just at the type-checker layer.
        if isinstance(value, MappingProxyType):
            return value
        return MappingProxyType(dict(value))


class TokenCandidate(TypedDict):
    """One top-k candidate at a single token step.

    ``prob`` is post-sampling-normalised when the backend declares
    ``POST_SAMPLING_PROBS``; otherwise the adapter must convert before
    populating (vLLM log-softmax → exp + renormalise, etc., see
    EXTENSIBILITY_SPEC §3.2 trap 3). ``logit`` is filled iff the
    backend declares ``LOGITS_RAW``; ``None`` otherwise.
    """

    token: str
    prob: float
    logit: float | None


class GenerationResult(TypedDict):
    """Final response-level result from ``generate``."""

    text: str
    finish_reason: str
    metadata: dict


class StreamChunk(TypedDict):
    """One incremental chunk yielded by ``generate_stream``."""

    delta: str
    is_final: bool


class BackendInfo(TypedDict):
    """Public projection of a backend instance for the console / runner.

    URL / api_key / api_key_env are intentionally absent — that is the
    primary SSRF / credential boundary against the DSL/frontend layer
    (see EXTENSIBILITY_SPEC §4.4 T1 / T2).
    """

    id: str
    backend: str
    model_name: str
    capabilities: list[str]
    metadata: dict


class ModelBackend(ABC):
    """One instance per ``(alias × workflow run)``.

    Subclasses must:
      - set ``spec_class`` to the concrete ``BaseSpec`` subclass they parse;
      - implement ``capabilities`` (classmethod, may switch on the spec);
      - implement ``generate``;
      - implement ``validate_requirements`` (classmethod);
      - override ``generate_stream`` if they declare ``STREAMING``;
      - override ``step_token`` if they declare ``TOKEN_STEP``;
      - override ``apply_template`` if they declare ``CHAT_TEMPLATE``.

    Subclasses must NOT poke at ``self._spec`` from outside the backend
    package; the public ``id`` / ``model_name`` / ``weight`` /
    ``instance_capabilities`` properties exist precisely so runner /
    aggregator code does not need ``_``-prefixed access.
    """

    name: ClassVar[str]
    """Registry key, e.g. ``"llama_cpp"``. Set by the ``@register_backend`` decorator."""

    spec_class: ClassVar[type[BaseSpec]]
    """The pydantic spec subclass that ``ModelRegistry`` will route yaml entries to."""

    def __init__(self, spec: BaseSpec, http: object) -> None:
        self._spec = spec
        self._http = http

    @property
    def id(self) -> str:
        return self._spec.id

    @property
    def model_name(self) -> str:
        return self._spec.model_name

    @property
    def weight(self) -> float:
        return self._spec.weight

    @property
    def instance_capabilities(self) -> frozenset[Capability]:
        """Cached projection of ``capabilities(spec)`` for the spec this instance owns."""
        return type(self).capabilities(self._spec)

    @classmethod
    @abstractmethod
    def capabilities(cls, spec: BaseSpec) -> frozenset[Capability]:
        """Capability set for *this specific* spec.

        Same backend class can return different sets for different specs
        (e.g. an OpenAI backend that knows ``gpt-3.5-turbo-0301`` lacks
        logprobs). Pure function of ``spec`` so the result can be cached.
        """

    @classmethod
    @abstractmethod
    def validate_requirements(
        cls,
        spec: BaseSpec,
        requirements: list[Requirement],
    ) -> list[ValidationIssue]:
        """Walk runner-supplied requirements, return structured issues.

        Empty list = all requirements pass. The framework folds these
        into ``StructuredValidationError`` if any have severity=error.
        """

    @abstractmethod
    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult:
        """Non-streaming completion. Mandatory for every backend."""

    def generate_stream(
        self,
        prompt: str,
        params: GenerationParams,
    ) -> Iterator[StreamChunk]:
        """Streaming completion. Override iff ``STREAMING`` is declared.

        Default raises ``CapabilityNotSupportedError`` so a runner that asks
        for streaming against a non-streaming backend fails with a
        useful message (the §9 capability filter should prevent this
        from being reached in normal flow).
        """
        raise CapabilityNotSupportedError(self.name, Capability.STREAMING.value)

    def step_token(self, prompt: str, params: TokenStepParams) -> list[TokenCandidate]:
        """Single-token advance with top-k candidates. Override iff ``TOKEN_STEP`` declared.

        ``params`` carries every sampling knob *per call* — top_k /
        temperature / top_p / stop / seed / max_tokens — so the runner
        loop can vary configuration across steps (or across same-model
        instances doing self-consistency) without rebuilding the
        backend. Backends apply the subset they understand and pass
        ``params.extra`` through to backend-specific keys.

        Contract: returned list has length ``<= params.top_k``; ``prob``
        values are usable iff ``TOP_PROBS`` is declared; ``logit`` is
        non-None iff ``LOGITS_RAW`` is declared.
        """
        raise CapabilityNotSupportedError(self.name, Capability.TOKEN_STEP.value)

    def apply_template(self, messages: list[ChatMessage]) -> str:
        """Format chat messages into a prompt string.

        Default is a deliberately naive fallback for backends that do
        not surface a server-side template (cloud APIs that swallow it
        internally). Override iff ``CHAT_TEMPLATE`` is declared so the
        server's actual template wins.
        """
        return "\n\n".join(f"{m['role']}: {m['content']}" for m in messages)
