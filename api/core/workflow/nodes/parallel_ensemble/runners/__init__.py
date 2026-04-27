"""Built-in runners for the parallel-ensemble node (P2.6).

Two runners ship with v0.2:

* ``token_step`` — PN.py-style per-step voting (``TokenStepRunner``,
  paired with ``aggregator_scope = "token"``). Optional ``enable_think``
  triggers a one-shot ``ThinkPhaseRunner`` pre-pass for ``type=think``
  models so chain-of-thought completes before the joint token loop
  starts.
* ``response_level`` — single-shot full-response aggregation (lands in
  P2.6.5; this package owns the import slot so the registry-side import
  ordering stays stable across the two patches).

Submodule imports below run the ``@register_runner`` decorators as a
side effect, populating ``RunnerRegistry`` before any node-level
``runner_registry.get(...)`` lookup happens. Importing this package is
therefore enough to make the built-ins discoverable.
"""

from __future__ import annotations

from . import token_step as token_step

__all__ = ["token_step"]
