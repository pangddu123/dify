"""``LlamaCppBackend`` — P2.2 acceptance.

Covers the four SPI methods (``capabilities`` / ``validate_requirements``
/ ``generate`` / ``generate_stream`` / ``step_token`` / ``apply_template``)
plus the two pure parsers (``parse_top_probs`` / ``parse_sse_chunks``).
HTTP traffic is exercised through a fake ``HttpClientProtocol`` so we
verify the *body* the backend would send to the real ``ssrf_proxy``
without standing up a network — the production wire-up (P2.9) injects
``ssrf_proxy`` directly, so ``HttpClientProtocol`` is the boundary.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.workflow.nodes.parallel_ensemble.backends.llama_cpp import (
    LlamaCppBackend,
    LlamaCppSpec,
    parse_sse_chunks,
    parse_top_probs,
)
from core.workflow.nodes.parallel_ensemble.spi.backend import TokenStepParams
from core.workflow.nodes.parallel_ensemble.spi.capability import Capability

# ── HTTP fakes ─────────────────────────────────────────────────────────


class _FakeResponse:
    """Tiny stand-in for ``httpx.Response`` covering the methods the
    backend actually calls (``raise_for_status`` / ``json`` / ``text``)."""

    def __init__(self, payload: Any = None, text: str = "", status: int = 200) -> None:
        self._payload = payload
        self.text = text
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise AssertionError(f"unexpected status {self.status}")

    def json(self) -> Any:
        return self._payload


class _FakeHttp:
    """``HttpClientProtocol`` shim capturing every ``post`` call.

    The framework wires ``ssrf_proxy`` here in production (see
    ``node_factory.py:300``); the contract is the same shape, so a
    fake that records calls is enough to verify the backend's request
    body matches the llama.cpp / PN.py contract.
    """

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any):  # type: ignore[no-untyped-def]
        self.calls.append({"url": url, **kwargs})
        return self.response


def _spec(**overrides: Any) -> LlamaCppSpec:
    base = {
        "id": "m1",
        "backend": "llama_cpp",
        "model_name": "test-model",
        "model_url": "http://internal.test:8080",
        "EOS": "<|eos|>",
        "type": "normal",
    }
    base.update(overrides)
    return LlamaCppSpec(**base)


# ── capabilities / validate_requirements ──────────────────────────────


class TestCapabilities:
    def test_default_set_matches_backend_capabilities_doc(self) -> None:
        # Mirrors BACKEND_CAPABILITIES.md §4 — change in lock-step.
        caps = LlamaCppBackend.capabilities(_spec())
        assert caps == frozenset(
            {
                Capability.STREAMING,
                Capability.TOKEN_STEP,
                Capability.TOP_PROBS,
                Capability.POST_SAMPLING_PROBS,
                Capability.CHAT_TEMPLATE,
            }
        )

    def test_logits_raw_not_declared(self) -> None:
        # EXTENSIBILITY_SPEC §3.2 trap 1: post_sampling_probs is not raw logits.
        assert Capability.LOGITS_RAW not in LlamaCppBackend.capabilities(_spec())

    def test_function_calling_not_declared(self) -> None:
        assert Capability.FUNCTION_CALLING not in LlamaCppBackend.capabilities(_spec())


class TestValidateRequirements:
    def test_min_top_k_unbounded(self) -> None:
        # llama.cpp has no hard top_k cap (unlike OpenAI 20).
        issues = LlamaCppBackend.validate_requirements(
            _spec(), [{"kind": "min_top_k", "value": 999, "rationale": "stress"}]
        )
        assert issues == []

    def test_needs_function_calling_rejected(self) -> None:
        issues = LlamaCppBackend.validate_requirements(
            _spec(),
            [{"kind": "needs_function_calling", "value": True, "rationale": "tool"}],
        )
        assert len(issues) == 1
        assert issues[0]["severity"] == "error"
        assert "FUNCTION_CALLING" in issues[0]["message"]

    def test_needs_function_calling_false_passes(self) -> None:
        # Explicit False is a valid runner choice and must not raise.
        issues = LlamaCppBackend.validate_requirements(
            _spec(),
            [{"kind": "needs_function_calling", "value": False, "rationale": "n/a"}],
        )
        assert issues == []


# ── parse_top_probs ───────────────────────────────────────────────────


class TestParseTopProbs:
    def test_eos_remapped_to_end_sentinel(self) -> None:
        # PN.py contract: per-model EOS rewritten to a stable "<end>"
        # so aggregator code is EOS-agnostic.
        out = parse_top_probs(
            {
                "completion_probabilities": [
                    {
                        "top_probs": [
                            {"token": "hello", "prob": 0.6},
                            {"token": "<|eos|>", "prob": 0.3},
                            {"token": "", "prob": 0.1},
                        ]
                    }
                ]
            },
            eos="<|eos|>",
        )
        tokens = [c["token"] for c in out]
        assert tokens == ["hello", "<end>", "<end>"]
        assert all(c["logit"] is None for c in out)

    def test_missing_completion_probabilities_returns_end_sentinel(self) -> None:
        # PN.py error fallback: garbled response means treat as <end>.
        out = parse_top_probs({"content": "hi"}, eos="<|eos|>")
        assert out == [{"token": "<end>", "prob": 0.01, "logit": None}]

    def test_empty_top_probs_list(self) -> None:
        out = parse_top_probs({"completion_probabilities": [{"top_probs": []}]}, eos="<|eos|>")
        assert out == [{"token": "<end>", "prob": 0.01, "logit": None}]

    def test_non_dict_items_skipped(self) -> None:
        out = parse_top_probs(
            {"completion_probabilities": [{"top_probs": ["bogus", {"token": "ok", "prob": 0.5}]}]},
            eos="<|eos|>",
        )
        assert [c["token"] for c in out] == ["ok"]


# ── parse_sse_chunks ──────────────────────────────────────────────────


class TestParseSseChunks:
    def test_two_chunks_then_final_flag(self) -> None:
        body = 'data: {"content": "hi"}\ndata: {"content": " there", "stop": true}\n'
        chunks = list(parse_sse_chunks(body))
        assert chunks == [
            {"delta": "hi", "is_final": False},
            {"delta": " there", "is_final": True},
        ]

    def test_emits_synthetic_final_when_stream_ends_uncleanly(self) -> None:
        # Server cut off mid-stream — backend still has to deliver an
        # is_final=True sentinel so the runner can finalise bookkeeping.
        body = 'data: {"content": "hi"}\n'
        chunks = list(parse_sse_chunks(body))
        assert chunks[-1]["is_final"] is True

    def test_skips_unparseable_lines(self) -> None:
        body = 'data: not-json\ndata: {"content": "x", "stop": true}\n'
        chunks = list(parse_sse_chunks(body))
        assert chunks == [{"delta": "x", "is_final": True}]

    def test_ignores_non_data_lines(self) -> None:
        body = ':heartbeat\nevent: ping\ndata: {"content": "x", "stop": true}\n'
        chunks = list(parse_sse_chunks(body))
        assert chunks == [{"delta": "x", "is_final": True}]


# ── generate / step_token / apply_template (HTTP-mocked) ──────────────


class TestGenerate:
    def test_posts_to_completion_endpoint(self) -> None:
        http = _FakeHttp(
            _FakeResponse(
                payload={
                    "content": "hello world",
                    "stop_type": "eos",
                    "generation_settings": {"temp": 0.7},
                }
            )
        )
        backend = LlamaCppBackend(_spec(), http=http)
        result = backend.generate("hi", {"max_tokens": 10, "temperature": 0.7})
        assert result["text"] == "hello world"
        assert result["finish_reason"] == "eos"
        assert result["metadata"]["generation_settings"] == {"temp": 0.7}
        # Request body shape: prompt + filtered params, stream=false.
        call = http.calls[0]
        assert call["url"] == "http://internal.test:8080/completion"
        assert call["json"] == {
            "prompt": "hi",
            "max_tokens": 10,
            "temperature": 0.7,
            "stream": False,
        }
        assert call["headers"] == {"Content-Type": "application/json"}
        # request_timeout_ms (30000) → 30.0s.
        assert call["timeout"] == pytest.approx(30.0)

    def test_finish_reason_limit_normalised_to_length(self) -> None:
        http = _FakeHttp(_FakeResponse(payload={"content": "x", "stop_type": "limit"}))
        backend = LlamaCppBackend(_spec(), http=http)
        assert backend.generate("hi", {})["finish_reason"] == "length"

    def test_trailing_slash_in_model_url_trimmed(self) -> None:
        http = _FakeHttp(_FakeResponse(payload={"content": "ok"}))
        backend = LlamaCppBackend(_spec(model_url="http://internal.test:8080/"), http=http)
        backend.generate("hi", {})
        # No double slash before the path.
        assert http.calls[0]["url"] == "http://internal.test:8080/completion"


class TestStepToken:
    def test_body_matches_pn_py_contract(self) -> None:
        http = _FakeHttp(
            _FakeResponse(
                payload={
                    "completion_probabilities": [
                        {
                            "top_probs": [
                                {"token": "yes", "prob": 0.7},
                                {"token": "no", "prob": 0.3},
                            ]
                        }
                    ]
                }
            )
        )
        backend = LlamaCppBackend(_spec(), http=http)
        candidates = backend.step_token("the answer is", TokenStepParams(top_k=5))
        assert [c["token"] for c in candidates] == ["yes", "no"]
        # Body must carry post_sampling_probs=True so probabilities come
        # back top-k re-normalised (BACKEND_CAPABILITIES §2.1).
        assert http.calls[0]["json"] == {
            "prompt": "the answer is",
            "max_tokens": 1,
            "n_probs": 5,
            "post_sampling_probs": True,
        }

    def test_eos_in_response_collapses_to_end(self) -> None:
        # Cross-cuts parse_top_probs but worth pinning at the SPI
        # boundary too — runners trust step_token's output directly.
        http = _FakeHttp(
            _FakeResponse(payload={"completion_probabilities": [{"top_probs": [{"token": "<|eos|>", "prob": 0.9}]}]})
        )
        backend = LlamaCppBackend(_spec(EOS="<|eos|>"), http=http)
        out = backend.step_token("p", TokenStepParams(top_k=1))
        assert out == [{"token": "<end>", "prob": 0.9, "logit": None}]

    def test_malformed_payload_yields_end_sentinel(self) -> None:
        http = _FakeHttp(_FakeResponse(payload="not a dict"))
        backend = LlamaCppBackend(_spec(), http=http)
        out = backend.step_token("p", TokenStepParams(top_k=3))
        assert out == [{"token": "<end>", "prob": 0.01, "logit": None}]

    def test_per_call_sampling_knobs_propagate(self) -> None:
        """``params.{temperature,top_p,stop,seed,extra}`` reach llama.cpp.

        Pins ADR-v3-14: research code that wants "same model, different
        temperature for self-consistency" relies on the body actually
        carrying the per-call sampling state.
        """
        http = _FakeHttp(
            _FakeResponse(payload={"completion_probabilities": [{"top_probs": [{"token": "x", "prob": 1.0}]}]})
        )
        backend = LlamaCppBackend(_spec(), http=http)
        backend.step_token(
            "p",
            TokenStepParams(
                top_k=3,
                temperature=0.7,
                top_p=0.9,
                stop=("<|stop|>",),
                seed=42,
                extra={"mirostat": 2},
            ),
        )
        body = http.calls[0]["json"]
        assert body["temperature"] == 0.7
        assert body["top_p"] == 0.9
        assert body["stop"] == ["<|stop|>"]
        assert body["seed"] == 42
        assert body["mirostat"] == 2

    def test_extra_is_read_only_and_detached(self) -> None:
        """``TokenStepParams.extra`` must be immutable + detached from
        the caller's dict so a backend that pokes at it cannot mutate
        state shared across the fan-out.

        Regression for the cross-thread aliasing trap: the runner
        submits one ``params`` instance to N concurrent ``step_token``
        calls; a mutable ``extra`` would let backend A leak a key to
        backend B mid-step.
        """
        from types import MappingProxyType

        from core.workflow.nodes.parallel_ensemble.spi.backend import TokenStepParams

        caller_dict = {"mirostat": 2}
        params = TokenStepParams(top_k=3, extra=caller_dict)

        # Detached from caller: mutating their dict afterwards must not
        # bleed into the params instance.
        caller_dict["mirostat"] = 999
        caller_dict["new_key"] = "leak"
        assert params.extra["mirostat"] == 2
        assert "new_key" not in params.extra

        # Read-only: a misbehaving backend that does ``params.extra[k] = v``
        # must fail loud rather than silently mutating shared state.
        assert isinstance(params.extra, MappingProxyType)
        with pytest.raises(TypeError):
            params.extra["leak"] = 1  # type: ignore[index]

    def test_optional_knobs_absent_when_unset(self) -> None:
        """Defaults stay narrow: ``temperature`` / ``top_p`` / ``stop`` /
        ``seed`` are omitted from the request body when the params object
        leaves them unset, so the llama.cpp server applies its own
        defaults instead of receiving a sentinel."""
        http = _FakeHttp(
            _FakeResponse(payload={"completion_probabilities": [{"top_probs": [{"token": "x", "prob": 1.0}]}]})
        )
        backend = LlamaCppBackend(_spec(), http=http)
        backend.step_token("p", TokenStepParams(top_k=3))
        body = http.calls[0]["json"]
        assert "temperature" not in body
        assert "top_p" not in body
        assert "stop" not in body
        assert "seed" not in body


class TestApplyTemplate:
    def test_posts_messages_to_apply_template_endpoint(self) -> None:
        http = _FakeHttp(_FakeResponse(payload={"prompt": "user: hi\nassistant:"}))
        backend = LlamaCppBackend(_spec(), http=http)
        out = backend.apply_template(
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ]
        )
        assert out == "user: hi\nassistant:"
        call = http.calls[0]
        assert call["url"] == "http://internal.test:8080/apply-template"
        assert call["json"] == {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ]
        }

    def test_missing_prompt_field_returns_empty_string(self) -> None:
        http = _FakeHttp(_FakeResponse(payload={"unexpected": True}))
        backend = LlamaCppBackend(_spec(), http=http)
        assert backend.apply_template([{"role": "user", "content": "hi"}]) == ""


class TestGenerateStream:
    def test_yields_chunks_in_order_with_final_flag(self) -> None:
        body = 'data: {"content": "hel"}\ndata: {"content": "lo"}\ndata: {"content": "", "stop": true}\n'
        http = _FakeHttp(_FakeResponse(payload=None, text=body))
        backend = LlamaCppBackend(_spec(), http=http)
        chunks = list(backend.generate_stream("hi", {"max_tokens": 5}))
        assert [c["delta"] for c in chunks] == ["hel", "lo", ""]
        assert chunks[-1]["is_final"] is True
        # stream=True must be in the body.
        assert http.calls[0]["json"]["stream"] is True

    def test_request_uses_completion_endpoint(self) -> None:
        http = _FakeHttp(_FakeResponse(payload=None, text='data: {"stop": true}\n'))
        backend = LlamaCppBackend(_spec(), http=http)
        list(backend.generate_stream("hi", {}))
        assert http.calls[0]["url"] == "http://internal.test:8080/completion"


# ── Production wire-up: ssrf_proxy injection ──────────────────────────


class TestSsrfProxyInjection:
    """The real production path injects ``core.helper.ssrf_proxy.ssrf_proxy``;
    monkeypatch its ``post`` and verify the backend reaches through it."""

    def test_step_token_via_ssrf_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.helper import ssrf_proxy as ssrf_module

        recorded: dict[str, Any] = {}

        def _fake_post(url: str, **kwargs: Any) -> _FakeResponse:
            recorded.update({"url": url, **kwargs})
            return _FakeResponse(payload={"completion_probabilities": [{"top_probs": [{"token": "ok", "prob": 1.0}]}]})

        monkeypatch.setattr(ssrf_module.ssrf_proxy, "post", _fake_post)
        backend = LlamaCppBackend(_spec(), http=ssrf_module.ssrf_proxy)
        result = backend.step_token("p", TokenStepParams(top_k=3))
        assert result == [{"token": "ok", "prob": 1.0, "logit": None}]
        assert recorded["url"] == "http://internal.test:8080/completion"
        assert recorded["json"]["post_sampling_probs"] is True
