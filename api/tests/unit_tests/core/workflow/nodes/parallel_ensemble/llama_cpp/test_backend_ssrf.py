"""P2.3 — adapter-level wiring smoke for ``LlamaCppBackend`` × ``ssrf_proxy``.

Where ``parallel_ensemble/test_llama_cpp_backend.py`` drives the
backend with a hand-rolled ``_FakeHttp`` (clean unit shape, but bypasses
the ssrf_proxy injection seam), this file pins the *adapter-level*
contract: hand the backend a real ``SSRFProxy`` instance and confirm
``step_token`` / ``generate`` / ``apply_template`` reach for
``ssrf_proxy.post`` (not ``httpx.post``) with the right body, headers,
URL and timeout.

⚠️ Scope: this is *not* end-to-end. We construct the backend manually
with ``http=ssrf_module.ssrf_proxy``; the framework path that goes
node_factory → backend constructor lands in P2.9 and is what proves
the runtime *always* injects the proxy. Until then, this file is the
adapter's half of that contract — a regression that swaps
``self._http`` for a bare ``httpx.post`` fails here.

Capability declaration + ``validate_requirements`` defaults are pinned
in this same file so anyone adding a new requirement kind sees the
default-pass / default-fail behaviour while editing the SSRF tests
that ride on top of them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.helper import ssrf_proxy as ssrf_module
from core.workflow.nodes.parallel_ensemble.backends.llama_cpp import (
    LlamaCppBackend,
    LlamaCppSpec,
)
from core.workflow.nodes.parallel_ensemble.spi.backend import TokenStepParams
from core.workflow.nodes.parallel_ensemble.spi.capability import Capability


def _spec(**overrides: Any) -> LlamaCppSpec:
    base: dict[str, Any] = {
        "id": "alpha",
        "backend": "llama_cpp",
        "model_name": "alpha-model",
        "model_url": "http://internal.test:8080",
        "EOS": "<|eos|>",
        "type": "normal",
    }
    base.update(overrides)
    return LlamaCppSpec(**base)


class _FakeResponse:
    """Minimal stand-in matching the graphon ``HttpResponse`` surface
    (``raise_for_status`` / ``text``). Re-defined here (instead of
    imported from the sibling test file) so this directory remains
    self-contained — P2.3's brief is "tests under ``llama_cpp/``",
    and pulling fixtures across directories would tie the new pytest
    layout to a sibling we may rename. ``text`` is auto-derived from
    ``payload`` so callers can keep the ``payload=`` shorthand."""

    def __init__(self, payload: Any = None, text: str | None = None) -> None:
        self._payload = payload
        if text is None:
            self.text = "" if payload is None else json.dumps(payload)
        else:
            self.text = text

    def raise_for_status(self) -> None:
        return None


# ── test_capability_declaration ───────────────────────────────────────


def test_capability_declaration() -> None:
    """``LlamaCppBackend.capabilities`` returns exactly the set
    BACKEND_CAPABILITIES.md §4 declares — and nothing more."""
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
    # Trap 1 from EXTENSIBILITY_SPEC §3.2: post-sampling probs are top-k
    # re-normalised, NOT raw logits — a fork that flips this needs to
    # subclass and override ``capabilities``.
    assert Capability.LOGITS_RAW not in caps


# ── test_validate_requirements_default ────────────────────────────────


def test_validate_requirements_default_passes_for_unknown_kind() -> None:
    """``validate_requirements`` is the framework's "deny-list-style"
    hook: anything we have not coded an explicit issue for is allowed.
    A future requirement kind a runner declares without backend
    awareness must therefore default-pass instead of default-fail."""
    issues = LlamaCppBackend.validate_requirements(
        _spec(),
        [{"kind": "future_requirement", "value": True, "rationale": "n/a"}],
    )
    assert issues == []


def test_validate_requirements_default_min_top_k_unbounded() -> None:
    """llama.cpp has no hard ``top_k`` ceiling (unlike OpenAI 20 or
    vLLM per-deployment); ``min_top_k`` requirements always pass."""
    issues = LlamaCppBackend.validate_requirements(
        _spec(), [{"kind": "min_top_k", "value": 999, "rationale": "stress"}]
    )
    assert issues == []


def test_validate_requirements_default_function_calling_blocks() -> None:
    """``needs_function_calling=True`` is the one requirement llama.cpp
    can statically reject (the stock build does not surface tool
    choice). ``False`` keeps the path quiet so a runner that explicitly
    declares "I don't need it" still binds."""
    blocking = LlamaCppBackend.validate_requirements(
        _spec(),
        [{"kind": "needs_function_calling", "value": True, "rationale": "tool"}],
    )
    assert len(blocking) == 1
    assert blocking[0]["severity"] == "error"

    quiet = LlamaCppBackend.validate_requirements(
        _spec(),
        [{"kind": "needs_function_calling", "value": False, "rationale": "n/a"}],
    )
    assert quiet == []


# ── test_step_token_uses_ssrf_proxy ───────────────────────────────────


def test_step_token_uses_ssrf_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """``step_token`` must reach the llama.cpp endpoint through
    ``ssrf_proxy`` with the PN.py-contract body
    (``max_tokens=1, n_probs=k, post_sampling_probs=true``) and the
    spec's ``request_timeout_ms`` converted to seconds."""
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured.update({"url": url, **kwargs})
        return _FakeResponse(
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

    monkeypatch.setattr(ssrf_module.ssrf_proxy, "post", _fake_post)
    backend = LlamaCppBackend(_spec(request_timeout_ms=15000), http=ssrf_module.ssrf_proxy)

    candidates = backend.step_token("the answer is", TokenStepParams(top_k=5))
    assert [c["token"] for c in candidates] == ["yes", "no"]

    assert captured["url"] == "http://internal.test:8080/completion"
    assert captured["json"] == {
        "prompt": "the answer is",
        "max_tokens": 1,
        "n_probs": 5,
        "post_sampling_probs": True,
    }
    assert captured["headers"] == {"Content-Type": "application/json"}
    # 15000ms → 15.0s; the millisecond-to-second conversion is the
    # backend's responsibility (httpx wants seconds).
    assert captured["timeout"] == pytest.approx(15.0)


# ── test_generate_uses_ssrf_proxy ─────────────────────────────────────


def test_generate_uses_ssrf_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """``generate`` posts to ``/completion`` through ``ssrf_proxy``
    with ``stream=False`` and the caller's sampling params merged
    into the request body."""
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured.update({"url": url, **kwargs})
        return _FakeResponse(
            payload={
                "content": "hello world",
                "stop_type": "eos",
                "generation_settings": {"temp": 0.7},
            }
        )

    monkeypatch.setattr(ssrf_module.ssrf_proxy, "post", _fake_post)
    backend = LlamaCppBackend(_spec(), http=ssrf_module.ssrf_proxy)

    result = backend.generate("hi", {"max_tokens": 32, "temperature": 0.7})
    assert result["text"] == "hello world"
    assert result["finish_reason"] == "eos"
    assert result["metadata"]["generation_settings"] == {"temp": 0.7}

    assert captured["url"] == "http://internal.test:8080/completion"
    assert captured["json"] == {
        "prompt": "hi",
        "max_tokens": 32,
        "temperature": 0.7,
        "stream": False,
    }
    assert captured["headers"] == {"Content-Type": "application/json"}
    # default request_timeout_ms 30000 → 30.0s.
    assert captured["timeout"] == pytest.approx(30.0)


# ── test_apply_template_uses_ssrf_proxy ───────────────────────────────


def test_apply_template_uses_ssrf_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """``apply_template`` posts the chat messages to ``/apply-template``
    through ``ssrf_proxy`` and returns the server's rendered prompt
    verbatim."""
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured.update({"url": url, **kwargs})
        return _FakeResponse(payload={"prompt": "<|user|>hi<|assistant|>"})

    monkeypatch.setattr(ssrf_module.ssrf_proxy, "post", _fake_post)
    backend = LlamaCppBackend(_spec(), http=ssrf_module.ssrf_proxy)

    out = backend.apply_template(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
    )
    assert out == "<|user|>hi<|assistant|>"

    assert captured["url"] == "http://internal.test:8080/apply-template"
    assert captured["json"] == {
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
    }
    assert captured["headers"] == {"Content-Type": "application/json"}
