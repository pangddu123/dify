"""Pre-pass that lets ``type=think`` models finish their chain-of-thought
before the joint token loop starts (PN.py ``process_think_task``).

A reasoning model (e.g. DeepSeek-R1, Qwen-thinking variants) emits a
hidden chain-of-thought delimited by a ``stop_think`` marker. If the
joint runner started picking joint tokens straight away, the
think-prefixed output of the reasoning model would be ranked against
the *non*-think prefix of the regular models — which is meaningless,
since they're at completely different points in the generation.

This runner therefore:

1. Inspects every backend's spec; only ``type=think`` aliases (those
   declaring a ``stop_think`` marker on ``LlamaCppSpec``) are dispatched.
2. Calls ``backend.generate(prompt, stop=[stop_think], max_tokens=8196)``
   so the model races to its end-of-thought marker.
3. Returns ``{alias: think_text + stop_think}`` so the caller can splice
   the suffix onto the alias's running prompt before the joint loop
   begins.

Failures degrade gracefully: a backend that raises during the think
phase contributes ``""`` (no suffix) and the error is recorded so the
runner can either continue without that contestant's chain-of-thought
or surface the issue via the trace.

Concurrency: callers pass a shared ``ThreadPoolExecutor`` so the think
phase fans out alongside the token loop's executor pool, matching
PN.py ``self.executor`` behaviour. ``trace.record_think`` is invoked
only from the driver thread (after each future resolves) because the
``TraceCollector`` lists are not thread-safe.

Why a separate class instead of inlining in ``TokenStepRunner.run``?
Two reasons:

* The think pre-pass has its own trace shape (``ThinkTraceEntry``) and
  its own gating flag (``DiagnosticsConfig.include_think_trace``); a
  separate object keeps that responsibility from leaking into the token
  loop.
* Future extensions can swap the think strategy (e.g. cap by elapsed
  time, summarise long traces, route to a single shared think model)
  without surgery on the joint loop.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..spi.backend import GenerationParams, ModelBackend
from ..spi.trace import ThinkTraceEntry, TraceCollector

logger = logging.getLogger(__name__)


class _ThinkResult:
    """Internal carrier so the driver can record_think on the main thread."""

    __slots__ = ("alias", "suffix", "think_text", "elapsed_ms")

    def __init__(self, alias: str, suffix: str, think_text: str, elapsed_ms: int) -> None:
        self.alias = alias
        self.suffix = suffix
        self.think_text = think_text
        self.elapsed_ms = elapsed_ms


class ThinkPhaseRunner:
    """One-shot pre-pass that resolves chain-of-thought for ``type=think`` aliases."""

    def __init__(self, executor: ThreadPoolExecutor) -> None:
        self._executor = executor

    def run(
        self,
        prompts: dict[str, str],
        backends: dict[str, ModelBackend],
        trace: TraceCollector,
    ) -> dict[str, str]:
        """Return ``{alias: suffix}`` for every think-capable backend.

        ``prompts`` is the templated prompt for each alias; the suffix
        the caller appends is ``think_text + stop_think`` so the joint
        loop resumes after the reasoning marker.

        Aliases without ``type=think`` are silently skipped — they need
        no pre-pass.
        """
        targets: dict[str, str] = {}
        for alias, backend in backends.items():
            stop_think = self._stop_think_for(backend)
            if stop_think is None:
                continue
            targets[alias] = stop_think

        if not targets:
            return {}

        futures: dict[Future[_ThinkResult], str] = {}
        for alias, stop_think in targets.items():
            prompt = prompts.get(alias, "")
            future = self._executor.submit(
                self._think_one,
                alias,
                backends[alias],
                prompt,
                stop_think,
            )
            futures[future] = alias

        suffixes: dict[str, str] = {}
        for future in futures:
            alias = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # backend-level failure
                logger.warning("think phase failed for %s: %s", alias, exc)
                trace.record_think(ThinkTraceEntry(source_id=alias, think_text="", elapsed_ms=0))
                suffixes[alias] = ""
                continue
            trace.record_think(
                ThinkTraceEntry(
                    source_id=alias,
                    think_text=result.think_text,
                    elapsed_ms=result.elapsed_ms,
                )
            )
            suffixes[alias] = result.suffix
        return suffixes

    @staticmethod
    def _stop_think_for(backend: ModelBackend) -> str | None:
        """Pull a non-empty ``stop_think`` off the backend spec, if any.

        We read ``backend._spec`` directly because the public ``ModelBackend``
        surface intentionally hides spec fields (the SPI doesn't promise a
        ``stop_think`` accessor — it's a llama.cpp-specific concept). A
        ``None`` here means "this alias is not a think model"; we skip it.
        """
        spec: Any = getattr(backend, "_spec", None)
        if spec is None:
            return None
        if getattr(spec, "type", None) != "think":
            return None
        stop_think = getattr(spec, "stop_think", None)
        if not isinstance(stop_think, str) or not stop_think:
            return None
        return stop_think

    @staticmethod
    def _think_one(
        alias: str,
        backend: ModelBackend,
        prompt: str,
        stop_think: str,
    ) -> _ThinkResult:
        start = time.perf_counter()
        params: GenerationParams = {
            "stop": [stop_think],
            "max_tokens": 8196,
        }
        result = backend.generate(prompt, params)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        think_text = result.get("text", "")
        # PN.py contract: re-attach the stop marker so the joint loop
        # continues *past* it. Without this the model would emit the
        # stop token again on the next step.
        suffix = think_text + stop_think
        return _ThinkResult(alias=alias, suffix=suffix, think_text=think_text, elapsed_ms=elapsed_ms)
