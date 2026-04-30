"""Exceptions for the parallel-ensemble SPI surface.

Lives one level above ``spi/`` so that backend / runner / aggregator
modules can raise these without importing each other. Mirrors the
two-tier shape of `ensemble_aggregator/exceptions.py` and
`llama_cpp/exceptions.py`: a single ``ParallelEnsembleError`` root for
node-layer ``except`` blocks plus narrow subclasses that retain
semantic fields (capability name, registry key, structured issues) so
callers do not have to re-parse the error string.

Registry lookup misses (``UnknownBackendError`` / ``UnknownRunnerError``
/ ``UnknownAggregatorError``) all share a small ``_UnknownEntryError``
mixin to keep the ``key`` attribute uniform.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid runtime cycle through spi.requirements
    from .spi.requirements import ValidationIssue


class ParallelEnsembleError(Exception):
    """Base for every SPI-side error raised inside the parallel-ensemble node."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class CapabilityNotSupportedError(ParallelEnsembleError):
    """A runner asked a backend to do something it has not declared.

    Raised by the SPI default implementations of ``generate_stream`` /
    ``step_token`` so a backend that forgot to override these methods
    fails loudly with a useful message instead of silently returning
    nothing.
    """

    def __init__(self, backend_name: str, capability_name: str):
        self.backend_name = backend_name
        self.capability_name = capability_name
        super().__init__(f"Backend '{backend_name}' does not support capability '{capability_name}'")


# Legacy alias — EXTENSIBILITY_SPEC v0.2 documents the class as
# ``CapabilityNotSupported``; ruff N818 wants the ``Error`` suffix.
# Keep the old name working for one release so docs / extension authors
# do not break mid-cycle.
CapabilityNotSupported = CapabilityNotSupportedError


class MissingSpecError(ParallelEnsembleError):
    """Raised when a ``token_source.spec_selector`` does not resolve in the variable pool.

    The upstream ``token-model-source`` node failed (or was never wired)
    so no :class:`ModelInvocationSpec` is available for this source. The
    node FAILs fast — there is no graceful-degrade because the joint
    voting loop has no defined behaviour for a missing voter.
    """

    def __init__(self, source_id: str, spec_selector: list[str]):
        self.source_id = source_id
        self.spec_selector = spec_selector
        super().__init__(
            f"Upstream ModelInvocationSpec for source '{source_id}' "
            f"(spec_selector={spec_selector}) is not present in the variable pool"
        )


class InvalidSpecError(ParallelEnsembleError):
    """Raised when an upstream variable resolves but is not a valid ``ModelInvocationSpec``.

    The variable pool returned a value but its shape does not match
    :class:`~core.workflow.nodes.token_model_source.entities.ModelInvocationSpec`
    (missing ``model_alias`` / ``prompt`` / ``sampling_params`` keys, or
    wrong types). Surfaces as a §9 startup error so the panel can point
    the user at the offending source instead of failing mid-loop.
    """

    def __init__(self, source_id: str, reason: str):
        self.source_id = source_id
        self.reason = reason
        super().__init__(
            f"Source '{source_id}' did not resolve to a valid ModelInvocationSpec: {reason}"
        )


class WeightResolutionError(ParallelEnsembleError):
    """Raised when a dynamic ``TokenSourceRef.weight`` selector cannot resolve.

    Mirrors ``ensemble_aggregator``'s same-named exception — the node
    FAILs by default (ADR-v3-15 fail-fast); only when the user opts
    into a numeric ``fallback_weight`` does the node trace-warn and use
    the fallback instead.
    """

    def __init__(self, source_id: str, selector: list[str], reason: str):
        self.source_id = source_id
        self.selector = selector
        self.reason = reason
        super().__init__(
            f"Failed to resolve weight for source '{source_id}' "
            f"(selector={selector}): {reason}"
        )


class StructuredValidationError(ParallelEnsembleError):
    """Raised by the §9 validation pipeline when ``ValidationIssue``s reach severity=error.

    Carries the full list so the panel can show every issue at once
    rather than fixing one and discovering the next on rerun.
    """

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        first = issues[0]["message"] if issues else "unknown"
        suffix = f" (+{len(issues) - 1} more)" if len(issues) > 1 else ""
        super().__init__(f"Validation failed: {first}{suffix}")


class _UnknownEntryError(ParallelEnsembleError):
    """Shared parent for ``UnknownBackendError`` / runner / aggregator misses."""

    _kind: str = "entry"  # subclass overrides

    def __init__(self, key: str, known: list[str] | None = None):
        self.key = key
        self.known = known or []
        suffix = f"; known: {sorted(self.known)}" if self.known else ""
        super().__init__(f"Unknown {self._kind} '{key}'{suffix}")


class UnknownBackendError(_UnknownEntryError):
    """Backend name not registered in ``BackendRegistry``."""

    _kind = "backend"


class UnknownRunnerError(_UnknownEntryError):
    """Runner name not registered in ``RunnerRegistry``."""

    _kind = "runner"


class UnknownAggregatorError(_UnknownEntryError):
    """Aggregator name not registered in ``AggregatorRegistry``."""

    _kind = "aggregator"


class DuplicateRegistrationError(ParallelEnsembleError):
    """Two ``@register_*`` decorators reused the same key."""

    def __init__(self, kind: str, key: str):
        self.kind = kind
        self.key = key
        super().__init__(f"{kind} '{key}' is already registered")


# ── Legacy P2.1 exception hierarchy ───────────────────────────────────
# Originally lived in ``llama_cpp/exceptions.py``; pulled up to the
# package-level module so ``ModelRegistry`` (now in ``registry/``) can
# raise them without re-introducing the parallel_ensemble → llama_cpp
# import cycle that the P2.1.5 shim would otherwise create. The
# ``llama_cpp.exceptions`` module re-exports these names for one
# release per TASKS.md L267.


class LlamaCppNodeError(Exception):
    """Base class for all llama.cpp registry / client errors."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class ModelRegistryError(LlamaCppNodeError):
    """Base class for ``ModelRegistry`` errors (legacy ``LocalModelRegistry``)."""


class RegistryFileError(ModelRegistryError):
    """Raised when the registry yaml is present but unreadable / malformed.

    Missing-file is *not* an error (R9): ``ModelRegistry`` keeps the
    registry empty and logs a warning so the API process still boots
    without a ``model_net.yaml``.
    """

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to load model registry from '{path}': {reason}")


class UnknownModelAliasError(ModelRegistryError):
    """Raised when a workflow node references an alias not in the registry."""

    def __init__(self, alias: str):
        self.alias = alias
        super().__init__(f"Unknown model alias: '{alias}'")
