"""Runner ↔ backend precision-validation layer — EXTENSIBILITY_SPEC §3.4.

Capability (`spi.capability.Capability`) answers "could this backend
possibly satisfy this runner". Requirements answer "given *this* runner
config and *this* spec, will it actually work". They run as a second
pass after capability filtering.

Both types are TypedDicts (not Pydantic models) on purpose:
  - They cross the SPI boundary between runner code (produces) and
    backend code (consumes); a TypedDict keeps the shape narrow without
    forcing third-party authors to import a Pydantic model class.
  - The ``value: object`` widening in ``Requirement`` is the price for
    an open ``kind`` set; backends that handle a kind they recognise
    narrow with ``isinstance`` / ``cast``.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class Requirement(TypedDict, total=False):
    """One concrete demand a runner places on a backend, given its config.

    A runner produces a ``list[Requirement]`` from its config via
    ``EnsembleRunner.requirements(config)``; the backend's
    ``validate_requirements`` walks that list and produces zero or more
    ``ValidationIssue`` per item.

    ``kind`` strings are deliberately open (TypedDict total=False) so a
    third-party backend / runner pair can introduce a new kind without
    editing the framework. Built-in kinds:

    - ``min_top_k`` (value: int) — the number of top candidates the runner
      will request per token step.
    - ``needs_logprobs`` (value: bool) — caller wants candidate probs,
      not just ranks.
    - ``min_context_tokens`` (value: int) — caller's prompt envelope.
    - ``needs_function_calling`` (value: bool) — caller will emit / parse
      tool calls.
    - ``needs_chat_template`` (value: bool) — caller will not pre-format
      messages itself.
    - ``min_backend_version`` (value: str) — semver lower bound.
    - ``model_allowlist`` (value: list[str]) — model_name must match one
      of these (used when known-bad SKUs need to be excluded).

    ``rationale`` is mandatory — surfaces in tooltips / panel errors so
    the user understands why the validator rejected a backend.
    """

    kind: Literal[
        "min_top_k",
        "needs_logprobs",
        "min_context_tokens",
        "needs_function_calling",
        "needs_chat_template",
        "min_backend_version",
        "model_allowlist",
    ]
    value: object
    rationale: str


class ValidationIssue(TypedDict):
    """One structured problem returned by ``validate_requirements``.

    All four fields are required (TypedDict default total=True): an
    issue without a message would be useless for the panel and an issue
    without a severity would be impossible for the framework to triage.

    ``i18n_key`` may be ``None`` for issues whose message is already
    localised or domain-specific enough that a key is not worth defining;
    the frontend falls back to ``message`` in that case.
    """

    severity: Literal["error", "warning"]
    requirement: Requirement
    message: str
    i18n_key: str | None
