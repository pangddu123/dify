"""Built-in runners for the parallel-ensemble node (v3 P3.B.0).

After ADR-v3-9 the parallel-ensemble node is the *token-mode* node; the
response-level path moved back under ``ensemble_aggregator``. Only one
runner ships in the box now:

* ``token_step`` — PN.py-style per-step voting (``TokenStepRunner``,
  paired with ``aggregator_scope = "token"``). Optional ``enable_think``
  triggers a one-shot ``ThinkPhaseRunner`` pre-pass for ``type=think``
  models so chain-of-thought completes before the joint token loop
  starts.

The submodule import below runs the ``@register_runner`` decorator as a
side effect, populating ``RunnerRegistry`` before any node-level
``runner_registry.get(...)`` lookup happens. Importing this package is
therefore enough to make the built-in discoverable.
"""

from __future__ import annotations

from . import token_step as token_step

__all__ = ["token_step"]
