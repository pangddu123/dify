"""Schema-level tests for the ``token-model-source`` node entities (P3.B.1).

Coverage:
* :class:`SamplingParams` defaults match DEVELOPMENT_PLAN_v3 §4.3.
* :class:`SamplingParams` ``extra="forbid"`` rejects yaml typos.
* Range guards on ``top_k`` / ``temperature`` / ``max_tokens`` /
  ``top_p`` reject out-of-range values pydantic would otherwise accept.
* :class:`TokenModelSourceNodeData` accepts the documented happy-path
  payload and normalises ``model_alias`` whitespace.
* The node-data ``type`` field is pinned to ``"token-model-source"``.
"""

import pytest
from pydantic import ValidationError

from core.workflow.nodes.token_model_source import TOKEN_MODEL_SOURCE_NODE_TYPE
from core.workflow.nodes.token_model_source.entities import (
    SamplingParams,
    TokenModelSourceNodeData,
)


class TestSamplingParamsDefaults:
    def test_default_values_match_v3_plan(self):
        sp = SamplingParams()
        assert sp.top_k == 10
        assert sp.temperature == 0.7
        assert sp.max_tokens == 1024
        assert sp.top_p is None
        assert sp.seed is None
        assert sp.stop == []

    def test_overrides_apply(self):
        sp = SamplingParams(
            top_k=5,
            temperature=0.0,
            max_tokens=128,
            top_p=0.95,
            seed=42,
            stop=["\n\n", "</s>"],
        )
        assert sp.top_k == 5
        assert sp.temperature == 0.0
        assert sp.max_tokens == 128
        assert sp.top_p == 0.95
        assert sp.seed == 42
        assert sp.stop == ["\n\n", "</s>"]


class TestSamplingParamsExtraForbid:
    """``extra="forbid"`` is the SPI's seat-belt against yaml typos —
    ``temprature: 0.7`` must hard-fail at schema validation, not silently
    no-op at runtime."""

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            SamplingParams.model_validate(
                {"top_k": 10, "temprature": 0.7}  # typo
            )

    def test_repetition_penalty_rejected_at_this_layer(self):
        # vLLM-specific knobs ride on ``TokenModelSourceNodeData.extra``
        # (the parent), not inside SamplingParams. Closing this surface
        # here is what keeps the cross-backend intersection clean.
        with pytest.raises(ValidationError):
            SamplingParams.model_validate(
                {"top_k": 10, "repetition_penalty": 1.1}
            )


class TestSamplingParamsRangeGuards:
    """Pydantic ``Field(gt=...)`` / ``ge=...`` / ``le=...`` already
    encode these bounds; tests pin them so a future "loosen
    temperature" change can't silently drop the negative-value reject."""

    def test_top_k_zero_rejected(self):
        with pytest.raises(ValidationError):
            SamplingParams(top_k=0)

    def test_top_k_negative_rejected(self):
        with pytest.raises(ValidationError):
            SamplingParams(top_k=-1)

    def test_temperature_zero_accepted_for_greedy(self):
        # Greedy decoding (``temperature=0``) is a legal mode for
        # research code that wants deterministic argmax sampling —
        # ``ge=0`` (not ``gt``) is intentional, pin it.
        sp = SamplingParams(temperature=0.0)
        assert sp.temperature == 0.0

    def test_temperature_negative_rejected(self):
        with pytest.raises(ValidationError):
            SamplingParams(temperature=-0.1)

    def test_max_tokens_zero_rejected(self):
        with pytest.raises(ValidationError):
            SamplingParams(max_tokens=0)

    def test_top_p_zero_rejected(self):
        # ``gt=0`` (not ``ge``) — ``top_p=0`` is meaningless (would
        # mask every candidate). pydantic must reject it.
        with pytest.raises(ValidationError):
            SamplingParams(top_p=0.0)

    def test_top_p_above_one_rejected(self):
        with pytest.raises(ValidationError):
            SamplingParams(top_p=1.01)

    def test_top_p_at_one_accepted(self):
        sp = SamplingParams(top_p=1.0)
        assert sp.top_p == 1.0


class TestTokenModelSourceNodeData:
    def test_minimal_happy_path(self):
        nd = TokenModelSourceNodeData(
            title="src",
            model_alias="qwen3-4b",
            prompt_template="Answer: {{#start.q#}}",
        )
        assert nd.type == TOKEN_MODEL_SOURCE_NODE_TYPE
        assert nd.model_alias == "qwen3-4b"
        assert nd.prompt_template == "Answer: {{#start.q#}}"
        # Defaults flow through.
        assert nd.sampling_params.top_k == 10
        assert nd.extra == {}

    def test_blank_model_alias_rejected(self):
        with pytest.raises(ValidationError):
            TokenModelSourceNodeData(
                title="src", model_alias="   ", prompt_template="hi"
            )

    def test_empty_model_alias_rejected(self):
        with pytest.raises(ValidationError):
            TokenModelSourceNodeData(
                title="src", model_alias="", prompt_template="hi"
            )

    def test_model_alias_whitespace_normalised(self):
        # Same rationale as ``AggregationInputRef.source_id``: the
        # frontend dedup compares trimmed values, so persist the
        # trimmed form to keep DSL rewrites idempotent.
        nd = TokenModelSourceNodeData(
            title="src",
            model_alias="  qwen3-4b  ",
            prompt_template="",
        )
        assert nd.model_alias == "qwen3-4b"

    def test_empty_prompt_template_allowed(self):
        # Constant prompts are a valid use case — the user wires the
        # full prompt without referencing any upstream variable.
        nd = TokenModelSourceNodeData(
            title="src",
            model_alias="qwen3-4b",
            prompt_template="",
        )
        assert nd.prompt_template == ""

    def test_extra_is_passthrough(self):
        nd = TokenModelSourceNodeData(
            title="src",
            model_alias="qwen3-4b",
            prompt_template="hi",
            extra={"repetition_penalty": 1.1, "research_tag": "exp_42"},
        )
        assert nd.extra == {"repetition_penalty": 1.1, "research_tag": "exp_42"}

    def test_sampling_params_typo_propagates(self):
        # ``SamplingParams.extra="forbid"`` must surface through the
        # parent node-data validation as a ``ValidationError``,
        # otherwise the seat-belt is unreachable from the DSL layer.
        with pytest.raises(ValidationError):
            TokenModelSourceNodeData.model_validate(
                {
                    "title": "src",
                    "type": TOKEN_MODEL_SOURCE_NODE_TYPE,
                    "model_alias": "qwen3-4b",
                    "prompt_template": "hi",
                    "sampling_params": {"top_k": 10, "temprature": 0.7},
                }
            )
