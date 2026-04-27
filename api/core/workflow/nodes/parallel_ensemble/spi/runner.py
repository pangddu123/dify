"""EnsembleRunner SPI — EXTENSIBILITY_SPEC §5.

A runner owns the question → answer recipe: how to fan out across
backends, how often to step, what to feed the aggregator. Backend
instances arrive as a ``dict[alias, ModelBackend]`` (v0.2.2: dict, not
list, so the runner uses alias as a stable key without reflecting on
``backend._spec.id``).

UI metadata (``i18n_key_prefix`` + ``ui_schema``) is part of the SPI on
purpose: pydantic JSON schema alone cannot express i18n keys, control
types, or tooltips, and forcing those into the frontend would defeat
the "drop-in third-party runner" goal.

``UI_CONTROL_ALLOWLIST`` is the v0.2 frozen set of controls the
frontend actually renders. A runner that lists a control outside this
set will be rejected by the schema validator at startup — the
allowlist is enforced both server-side (this module) and client-side
(P2.11).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import TYPE_CHECKING, ClassVar, Literal, TypedDict, TypeVar

from pydantic import BaseModel

from .aggregator import Aggregator
from .backend import ModelBackend
from .capability import Capability
from .requirements import Requirement, ValidationIssue
from .trace import TraceCollector

if TYPE_CHECKING:  # avoid registry ↔ spi import cycle at runtime
    from ..registry.model_registry import ModelRegistry


UI_CONTROL_ALLOWLIST: frozenset[str] = frozenset(
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
"""v0.2 frozen control set; anything outside is rejected at startup.

Adding a new control is intentionally a framework-level change — both
backend (this module) and frontend (P2.11) must learn about it
together, otherwise the panel renders nothing for that field.
"""


ConfigT = TypeVar("ConfigT", bound=BaseModel)


class TokenEvent(TypedDict):
    """Incremental delta from a streaming runner."""

    kind: Literal["token"]
    delta: str


class FullResponseEvent(TypedDict):
    """One full per-backend response (e.g. judge runner mid-flight).

    Used when the runner wants the panel / downstream nodes to see
    individual contestants before the runner picks the winner.
    """

    kind: Literal["full_response"]
    source_id: str
    text: str


class DoneEvent(TypedDict):
    """Final event from a runner.

    ``text`` is the canonical answer that lands in
    ``outputs.text``; ``metadata`` rides into ``NodeRunResult.metadata``.
    Trace data goes through ``TraceCollector`` instead, not here.
    """

    kind: Literal["done"]
    text: str
    metadata: dict


RunnerEvent = TokenEvent | FullResponseEvent | DoneEvent


class EnsembleRunner[ConfigT: BaseModel](ABC):
    """Base for every runner registered via ``@register_runner``.

    Subclasses declare:
      - ``name`` (registry key, set by the decorator),
      - ``config_class`` (pydantic schema for ``runner_config``),
      - ``aggregator_scope`` (matching string for the paired aggregator),
      - ``required_capabilities`` (used in §9 capability filter + UI),
      - ``optional_capabilities`` (UI only — informational),
      - ``i18n_key_prefix`` + ``ui_schema``,
      - ``requirements(config)`` and ``run(...)``;
      - optionally override ``validate_selection`` for cross-field rules
        the runner config alone can't express (e.g. ``judge_alias`` must
        be in ``model_aliases``).
    """

    name: ClassVar[str]
    config_class: ClassVar[type[BaseModel]]
    aggregator_scope: ClassVar[str]
    required_capabilities: ClassVar[frozenset[Capability]]
    optional_capabilities: ClassVar[frozenset[Capability]] = frozenset()

    i18n_key_prefix: ClassVar[str]
    """Frontend looks up ``<prefix>.name`` / ``<prefix>.description`` /
    ``<prefix>.fields.<fieldName>.{label,tooltip}``. Both en-US and
    zh-Hans locale files must define every key the runner uses, or P2.12
    fails."""

    ui_schema: ClassVar[dict]
    """Per-field control declarations. Each value's ``"control"`` must be
    in :data:`UI_CONTROL_ALLOWLIST`; numeric controls accept
    ``min``/``max``/``step``; ``select``/``multi_select`` accept ``options``;
    ``model_alias_select`` is the special control that pulls from
    ``ModelRegistry.list_aliases()``."""

    @classmethod
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        schema = getattr(cls, "ui_schema", None)
        if schema is None:
            return
        # We can't be sure subclasses set ui_schema before __init_subclass__
        # for ABC intermediates; only validate concrete classes that have
        # both name and ui_schema.
        if not hasattr(cls, "name"):
            return
        for field, decl in schema.items():
            if not isinstance(decl, dict):
                raise TypeError(
                    f"runner '{cls.__name__}' ui_schema['{field}'] must be a dict, "
                    f"got {type(decl).__name__}"
                )
            control = decl.get("control")
            if control not in UI_CONTROL_ALLOWLIST:
                raise ValueError(
                    f"runner '{cls.__name__}' ui_schema['{field}'].control="
                    f"{control!r} is not in the v0.2 allowlist {sorted(UI_CONTROL_ALLOWLIST)}"
                )

    @classmethod
    def config_schema_json(cls) -> dict:
        """JSON schema dump for the frontend's fallback validator."""
        return cls.config_class.model_json_schema()

    @classmethod
    @abstractmethod
    def requirements(cls, config: ConfigT) -> list[Requirement]:
        """Derive backend requirements from a runner config; see §3.4."""

    @classmethod
    def validate_selection(
        cls,
        config: ConfigT,
        model_aliases: list[str],
        registry: ModelRegistry,
    ) -> list[ValidationIssue]:
        """Cross-field rules between runner config and the chosen aliases.

        Default: no extra rules (returns empty list). Runners with
        constraints like "judge_alias must be in model_aliases" or
        "needs ≥2 models" override this.
        """
        return []

    @abstractmethod
    def run(
        self,
        question: str,
        backends: dict[str, ModelBackend],
        aggregator: Aggregator,
        config: ConfigT,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        """Run the ensemble, yielding events the node translates into graphon stream events.

        Contracts:
          - ``backends`` keys are aliases; values are already capability-
            and requirements-validated.
          - Streaming runners interleave ``TokenEvent`` and end with
            exactly one ``DoneEvent``.
          - Non-streaming runners may yield only the ``DoneEvent``.
          - Judge-style runners can interleave ``FullResponseEvent``s
            before the final ``DoneEvent``.
          - Trace writes go through ``trace.record_*``; never inline
            into ``DoneEvent.metadata``.
        """
