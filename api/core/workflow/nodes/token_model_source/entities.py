"""Pydantic schema for the ``token-model-source`` DSL surface (P3.B.1).

Two layers, on purpose — same split the parallel-ensemble node uses:

* :class:`SamplingParams` is the strongly-typed knob block. ``extra="forbid"``
  rejects yaml typos (``temprature: 0.7``) at boot rather than letting them
  silently no-op at run time. The fields are the cross-backend intersection
  surfaced through ``TokenStepParams`` (P3.B.0); backend-specific knobs
  (vLLM ``repetition_penalty`` etc.) ride on ``extra`` on the parent
  :class:`TokenModelSourceNodeData`.

* :class:`ModelInvocationSpec` is a :class:`TypedDict`, **not** a pydantic
  model. The wire shape crosses graph nodes via ``VariablePool``
  serialization (this node yields it as ``outputs.spec``, the
  parallel-ensemble node reads it back); using a TypedDict keeps the
  payload narrow and avoids forcing extension authors to import a
  Pydantic class just to consume the spec (mirrors the SPI's
  ``ChatMessage`` / ``GenerationParams`` choice — see
  ``parallel_ensemble/spi/backend.py``).

* :class:`TokenModelSourceNodeData` inherits ``BaseNodeData(extra="allow")``
  so legacy graph extras (``selected``, ``params``, ``paramSchemas``,
  ``datasource_label``, …) survive validation. Unlike the
  ``parallel-ensemble`` node, this node carries no SSRF / credential
  attack surface (no URL / api_key fields anywhere on it), so the
  forbidden-key validator from ``parallel_ensemble.entities`` is not
  duplicated here.
"""

from __future__ import annotations

from typing import Any, ClassVar, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType

from . import TOKEN_MODEL_SOURCE_NODE_TYPE


class SamplingParams(BaseModel):
    """Per-source sampling knobs the user types in the panel.

    Defaults match DEVELOPMENT_PLAN_v3 §4.3 (``top_k=10``,
    ``temperature=0.7``, ``max_tokens=1024``); the optional fields
    (``top_p`` / ``seed`` / ``stop``) default to "let the backend
    decide". The runtime aggregator merges these with
    ``TokenSourceRef.top_k_override`` (ADR-v3-6) and constructs the
    actual ``TokenStepParams`` per call (P3.B.3) — this layer is
    deliberately the user-facing form, not the
    ``MappingProxyType``-frozen runtime form.
    """

    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(default=10, gt=0)
    temperature: float = Field(default=0.7, ge=0.0)
    max_tokens: int = Field(default=1024, gt=0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    seed: int | None = None
    stop: list[str] = Field(default_factory=list)


class ModelInvocationSpec(TypedDict):
    """Cross-node payload yielded by ``token-model-source.outputs.spec``.

    Consumed by the ``parallel-ensemble`` node (P3.B.3) which reads N
    of these from the variable pool, instantiates one backend per
    ``model_alias`` via ``LocalModelRegistry``, and feeds each
    backend its own ``prompt`` + ``sampling_params`` per call. The
    TypedDict shape is the contract between the two nodes — kept
    narrow on purpose so a third-party token strategy can extend the
    payload via ``extra`` without forking the schema (Rv3-5).
    """

    model_alias: str
    prompt: str
    sampling_params: dict[str, Any]
    extra: dict[str, Any]


class TokenModelSourceNodeData(BaseNodeData):
    """DSL payload for the ``token-model-source`` node.

    ``model_alias`` is the registry key the parallel-ensemble node
    will resolve against ``LocalModelRegistry`` at run start;
    validating it here against the registry would couple this schema
    to a runtime singleton — defer to the parallel-ensemble node's
    §9 startup validation, which already owns alias resolution.

    ``prompt_template`` accepts ``{{#node.field#}}`` placeholders
    parsed by ``VariableTemplateParser`` at run time; an empty
    template is allowed (``Field(default="")`` would also be valid)
    so the user can wire a single-variable pass-through without
    typing ``"{{#start.user_input#}}"`` literally.
    """

    type: NodeType = TOKEN_MODEL_SOURCE_NODE_TYPE

    model_alias: str = Field(..., min_length=1)
    prompt_template: str = ""
    sampling_params: SamplingParams = Field(default_factory=SamplingParams)
    extra: dict[str, Any] = Field(default_factory=dict)

    NODE_TYPE: ClassVar[str] = TOKEN_MODEL_SOURCE_NODE_TYPE

    @field_validator("model_alias")
    @classmethod
    def _model_alias_not_blank(cls, v: str) -> str:
        # ``min_length=1`` rejects only the empty string; trim and
        # re-check so ``"   "`` is rejected too. Matches the
        # ``AggregationInputRef.source_id`` normalization rule.
        stripped = v.strip()
        if not stripped:
            raise ValueError("model_alias must not be blank")
        return stripped
