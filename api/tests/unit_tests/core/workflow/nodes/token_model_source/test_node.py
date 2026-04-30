"""Behavioural tests for the ``TokenModelSourceNode`` (P3.B.1).

Coverage:
* ``_run`` happy path — yields a single ``StreamCompletedEvent`` with the
  rendered prompt and the documented spec shape (ADR-v3-10).
* Prompt rendering normalises segment.text for non-string upstreams
  (mirrors ``ensemble_aggregator`` segment-text contract).
* Missing upstream variable → ``PromptRenderError`` → FAILED event
  with structured ``error_type`` for the panel.
* Constant prompt (no placeholders) skips the variable pool entirely.
* ``extra_variable_selector_to_variable_mapping`` exposes every
  ``{{#node.field#}}`` reference with the framework's ``{node_id}.{var}``
  key shape (Rv3-3 / draft-variable preload contract).
* ``node_data.extra`` round-trips into ``spec.extra`` (vLLM-style
  research knobs survive without forking the schema).
"""

from core.workflow.nodes.token_model_source import (
    TOKEN_MODEL_SOURCE_NODE_TYPE,
    TokenModelSourceNode,
)
from graphon.enums import WorkflowNodeExecutionStatus
from graphon.node_events.node import StreamCompletedEvent
from graphon.runtime.variable_pool import VariablePool


def _make_node(pool: VariablePool, payload: dict) -> TokenModelSourceNode:
    """Build a node bypassing ``Node.__init__`` (which needs full
    ``graph_init_params``). Tests only exercise ``_run`` /
    ``_render_prompt``, which read ``_node_data``, ``_node_id``, and
    ``graph_runtime_state.variable_pool``.
    """
    node = TokenModelSourceNode.__new__(TokenModelSourceNode)
    node._node_id = "src_1"

    class _RS:
        pass

    rs = _RS()
    rs.variable_pool = pool
    node.graph_runtime_state = rs
    node._node_data = TokenModelSourceNode._node_data_type.model_validate(payload)
    return node


def _payload(
    *,
    model_alias: str = "qwen3-4b",
    prompt_template: str = "Answer: {{#start.q#}}",
    sampling_params: dict | None = None,
    extra: dict | None = None,
) -> dict:
    payload: dict = {
        "title": "src",
        "model_alias": model_alias,
        "prompt_template": prompt_template,
    }
    if sampling_params is not None:
        payload["sampling_params"] = sampling_params
    if extra is not None:
        payload["extra"] = extra
    return payload


class TestRunHappyPath:
    def test_single_completed_event_with_spec_shape(self):
        pool = VariablePool()
        pool.add(["start", "q"], "what is 2+2")
        node = _make_node(pool, _payload())

        events = list(node._run())

        assert len(events) == 1
        assert isinstance(events[0], StreamCompletedEvent)
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        assert nrr.error == ""
        assert nrr.inputs == {"model_alias": "qwen3-4b"}

        # Spec shape mirrors ADR-v3-10 / DEVELOPMENT_PLAN_v3 §4.3.
        spec = nrr.outputs["spec"]
        assert spec["model_alias"] == "qwen3-4b"
        assert spec["prompt"] == "Answer: what is 2+2"
        assert spec["sampling_params"] == {
            "top_k": 10,
            "temperature": 0.7,
            "max_tokens": 1024,
            "top_p": None,
            "seed": None,
            "stop": [],
        }
        assert spec["extra"] == {}
        # ``model_alias`` surfaced top-level too for panels that only
        # want the alias without unpacking the spec dict.
        assert nrr.outputs["model_alias"] == "qwen3-4b"

    def test_constant_prompt_no_pool_lookup(self):
        # No placeholder → ``_render_prompt`` short-circuits without
        # touching the pool, so an empty pool is fine.
        pool = VariablePool()
        node = _make_node(
            pool,
            _payload(prompt_template="Plain instruction with no vars."),
        )

        events = list(node._run())
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.SUCCEEDED
        assert nrr.outputs["spec"]["prompt"] == "Plain instruction with no vars."

    def test_overridden_sampling_params_round_trip_into_spec(self):
        pool = VariablePool()
        pool.add(["start", "q"], "x")
        node = _make_node(
            pool,
            _payload(
                sampling_params={
                    "top_k": 5,
                    "temperature": 0.0,
                    "max_tokens": 64,
                    "top_p": 0.9,
                    "seed": 42,
                    "stop": ["\n\n"],
                }
            ),
        )

        spec = list(node._run())[0].node_run_result.outputs["spec"]
        assert spec["sampling_params"] == {
            "top_k": 5,
            "temperature": 0.0,
            "max_tokens": 64,
            "top_p": 0.9,
            "seed": 42,
            "stop": ["\n\n"],
        }

    def test_extra_dict_round_trips(self):
        # ``extra`` is the documented extension point for backend-private
        # knobs (vLLM ``repetition_penalty``, research_tag, ...). It must
        # arrive in ``spec.extra`` byte-for-byte.
        pool = VariablePool()
        pool.add(["start", "q"], "x")
        node = _make_node(
            pool,
            _payload(extra={"repetition_penalty": 1.1, "research_tag": "exp_42"}),
        )

        spec = list(node._run())[0].node_run_result.outputs["spec"]
        assert spec["extra"] == {"repetition_penalty": 1.1, "research_tag": "exp_42"}

    def test_spec_extra_decoupled_from_node_data(self):
        # ``node_data.extra`` must not be aliased into ``spec.extra``;
        # downstream mutation of one must not bleed into the other
        # (matters for the parallel-ensemble executor which may inject
        # per-call backend keys).
        pool = VariablePool()
        pool.add(["start", "q"], "x")
        node = _make_node(pool, _payload(extra={"k": "v"}))

        spec = list(node._run())[0].node_run_result.outputs["spec"]
        spec["extra"]["mutated"] = True
        assert "mutated" not in node._node_data.extra


class TestRenderPromptSegmentText:
    """The renderer must use ``Segment.text`` (graphon canonical),
    matching ``ensemble_aggregator/node.py`` so non-string upstreams
    render the same way across every workflow node."""

    def test_object_segment_renders_as_json(self):
        pool = VariablePool()
        pool.add(["upstream", "answer"], {"city": "Paris", "score": 0.9})
        node = _make_node(
            pool,
            _payload(prompt_template="Look at: {{#upstream.answer#}}"),
        )

        spec = list(node._run())[0].node_run_result.outputs["spec"]
        # JSON form uses double quotes; Python repr uses single quotes.
        assert '"city": "Paris"' in spec["prompt"]
        assert "'city': 'Paris'" not in spec["prompt"]

    def test_array_string_segment_renders_as_json(self):
        pool = VariablePool()
        pool.add(["upstream", "tags"], ["alpha", "beta"])
        node = _make_node(
            pool,
            _payload(prompt_template="Tags: {{#upstream.tags#}}"),
        )
        spec = list(node._run())[0].node_run_result.outputs["spec"]
        assert '["alpha", "beta"]' in spec["prompt"]

    def test_none_segment_renders_as_empty_string(self):
        pool = VariablePool()
        pool.add(["upstream", "maybe"], None)
        node = _make_node(
            pool,
            _payload(prompt_template="Value: <{{#upstream.maybe#}}>"),
        )
        spec = list(node._run())[0].node_run_result.outputs["spec"]
        assert spec["prompt"] == "Value: <>"


class TestRunFailurePaths:
    def test_missing_upstream_variable_becomes_failed_event(self):
        pool = VariablePool()
        # ``start.q`` deliberately not added.
        node = _make_node(pool, _payload())

        events = list(node._run())

        assert len(events) == 1
        nrr = events[0].node_run_result
        assert nrr.status == WorkflowNodeExecutionStatus.FAILED
        assert nrr.error_type == "PromptRenderError"
        # Surface the offending variable in the message so the panel
        # tells the user *which* upstream is unwired.
        assert "#start.q#" in nrr.error
        # ``inputs`` keeps the alias for observability of the failed run.
        assert nrr.inputs == {"model_alias": "qwen3-4b"}
        # Outputs stay empty on failure.
        assert nrr.outputs == {}


class TestExtractVariableSelectorMapping:
    """Mapping must expose every ``{{#upstream.field#}}`` reference so
    the draft-variable preload pipeline materialises the upstream
    value ahead of ``_run``. Key shape follows the framework
    convention: ``{node_id}.{variable_key}``."""

    def _config(self, node_id: str, prompt_template: str) -> dict:
        return {
            "id": node_id,
            "data": {
                "title": "src",
                "type": TOKEN_MODEL_SOURCE_NODE_TYPE,
                "model_alias": "qwen3-4b",
                "prompt_template": prompt_template,
            },
        }

    def test_single_placeholder_exposed(self):
        config = self._config("src_1", "Answer: {{#start.q#}}")
        mapping = TokenModelSourceNode.extract_variable_selector_to_variable_mapping(
            graph_config={}, config=config
        )
        assert dict(mapping) == {"src_1.#start.q#": ["start", "q"]}

    def test_multiple_placeholders_each_exposed(self):
        config = self._config(
            "src_1",
            "{{#start.q#}} for {{#ctx.user#}} in {{#ctx.lang#}}",
        )
        mapping = TokenModelSourceNode.extract_variable_selector_to_variable_mapping(
            graph_config={}, config=config
        )
        assert dict(mapping) == {
            "src_1.#start.q#": ["start", "q"],
            "src_1.#ctx.user#": ["ctx", "user"],
            "src_1.#ctx.lang#": ["ctx", "lang"],
        }

    def test_empty_template_returns_empty_mapping(self):
        config = self._config("src_1", "Plain text, no placeholders.")
        mapping = TokenModelSourceNode.extract_variable_selector_to_variable_mapping(
            graph_config={}, config=config
        )
        assert dict(mapping) == {}

    def test_deep_path_selector_preserved(self):
        # Nested object paths like ``upstream.structured_output.city``
        # must survive verbatim — ``VariableTemplateParser`` already
        # supports up to 11 segments; the mapping must not truncate.
        config = self._config(
            "src_1",
            "City: {{#upstream.structured_output.city#}}",
        )
        mapping = TokenModelSourceNode.extract_variable_selector_to_variable_mapping(
            graph_config={}, config=config
        )
        assert dict(mapping) == {
            "src_1.#upstream.structured_output.city#": [
                "upstream",
                "structured_output",
                "city",
            ],
        }


class TestNodeRegistration:
    """Smoke test the node registers itself under the canonical type
    string so ``DifyNodeFactory.create_node`` can resolve it via
    ``Node._registry``."""

    def test_node_type_attribute_matches_constant(self):
        assert TokenModelSourceNode.node_type == TOKEN_MODEL_SOURCE_NODE_TYPE
        assert TokenModelSourceNode.node_type == "token-model-source"

    def test_version_returns_one(self):
        # P3.B.1 introduces v1 of the node; pin it so a future
        # accidental version bump in the wrong PR is loud.
        assert TokenModelSourceNode.version() == "1"
