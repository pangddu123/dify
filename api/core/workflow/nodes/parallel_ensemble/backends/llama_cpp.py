"""LlamaCppSpec + ``LlamaCppBackend`` placeholder.

P2.1.5 only needs the spec subclass to exist + register so
``ModelRegistry._load`` can dispatch yaml entries with
``backend: llama_cpp`` to the right pydantic schema. The runtime
backend (``generate`` / ``step_token`` / ``apply_template`` /
``capabilities`` / ``validate_requirements``) lands in P2.2 — we just
register a class with abstract methods left abstract so registration
works but instantiation does not (which is exactly what we want until
P2.2 fills the bodies).

Field shape mirrors the P2.1 ``ModelSpec`` exactly so existing
``model_info.json``-style entries upgrade by adding a single
``backend: llama_cpp`` line.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import AnyUrl, Field

from ..registry.backend_registry import register_backend
from ..spi.backend import BaseSpec, ModelBackend


class LlamaCppSpec(BaseSpec):
    """yaml schema for a single self-hosted llama.cpp endpoint.

    ``backend`` is locked to ``"llama_cpp"`` via ``Literal`` so loading
    a yaml entry that mistakenly points at this spec class with a
    different backend string fails at pydantic validation rather than
    silently mis-routing. The pyright ``reportIncompatibleVariableOverride``
    suppression is the documented pydantic pattern for narrowing a tag
    field on a discriminator-bearing subclass — pydantic resolves the
    Literal at validate time even though pyright treats variable
    overrides as invariant.
    """

    backend: Literal["llama_cpp"]  # pyright: ignore[reportIncompatibleVariableOverride]
    model_arch: str = "llama"
    model_url: AnyUrl
    EOS: str = Field(min_length=1)
    type: Literal["normal", "think"] = "normal"
    stop_think: str | None = None


@register_backend("llama_cpp")
class LlamaCppBackend(ModelBackend):
    """Placeholder until P2.2 implements ``LlamaCppBackend`` proper.

    The class is registered so ``BackendRegistry.get_spec_class("llama_cpp")``
    works for the SPI freeze verification. ``capabilities`` /
    ``validate_requirements`` / ``generate`` stay abstract — anyone who
    tries to *instantiate* this class today gets the standard ABC
    "Can't instantiate abstract class" error, which is the correct
    signal that P2.2 is the next dependency.
    """

    spec_class: ClassVar[type[BaseSpec]] = LlamaCppSpec
