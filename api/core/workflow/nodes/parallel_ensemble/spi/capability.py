"""Capability matrix — EXTENSIBILITY_SPEC §3.1.

Capability is the *coarse* alignment knob between runner and backend:
"could this backend possibly satisfy this runner?". Numeric / config-
dependent constraints (e.g. ``top_k <= 20`` for OpenAI) live one layer
deeper in `requirements.Requirement`.

⚠️ Three semantic traps this enum encodes (see spec §3.2):
  - ``POST_SAMPLING_PROBS`` is *not* ``LOGITS_RAW``: llama.cpp's
    ``post_sampling_probs=true`` returns top-k post-softmax probs
    re-normalised to sum to 1, not raw logits.
  - vLLM ``logprobs`` are log-softmax values; adapters must ``exp()``
    + renormalise before exposing as ``TOP_PROBS`` to keep semantics
    aligned across backends.
  - ``KV_CACHE_REUSE`` exists to reserve the slot for PN.py's
    ``clear_slot_kv_cache`` style optimisation; v0.2 explicitly does
    not implement it (see spec §1.2 non-goals), but extensions that
    do can declare it without breaking the SPI.
"""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    """Backend capability declarations consumed by runners + UI filters."""

    STREAMING = "streaming"
    """``generate_stream()`` available; yields chunks/tokens incrementally."""

    TOKEN_STEP = "token_step"
    """``step_token(prompt, top_k) -> [TokenCandidate]`` available.

    Required for PN.py-style token-level voting. Implies the backend can
    advance one token at a time and report the top-k candidates for that
    step (probability values not yet required — see ``TOP_PROBS``).
    """

    TOP_PROBS = "top_probs"
    """Top-k candidates carry usable ``prob: float`` values, not just rank.

    Without this, a runner can still vote by rank but cannot do weighted
    probability mixing.
    """

    POST_SAMPLING_PROBS = "post_sampling_probs"
    """Probabilities are post-softmax + top-k re-normalised, not raw logits.

    llama.cpp ``post_sampling_probs=true`` and OpenAI ``logprobs``-derived
    probs land here. A runner doing strict logit averaging must NOT rely
    on this capability — it should require ``LOGITS_RAW``.
    """

    LOGITS_RAW = "logits_raw"
    """Raw, pre-softmax, full-vocab logits (vLLM ``return_logits`` etc.).

    Closed cloud APIs do not expose this; only patched/forked self-hosted
    backends typically can.
    """

    CHAT_TEMPLATE = "chat_template"
    """Server-side chat template can be applied via ``apply_template()``.

    llama.cpp ``/apply-template`` exposes this; vLLM applies it implicitly
    so cannot expose it; OpenAI / Anthropic do not surface the template.
    """

    FUNCTION_CALLING = "function_calling"
    """OpenAI-style structured tool / function calling outputs."""

    KV_CACHE_REUSE = "kv_cache_reuse"
    """PN.py-style ``clear_slot_kv_cache`` optimisation across token steps.

    ⚠️ v0.2 reserves the slot but does not implement it; declaring it
    today is allowed but no built-in runner depends on it yet.
    """
