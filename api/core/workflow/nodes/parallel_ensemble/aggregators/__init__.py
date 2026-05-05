"""Built-in aggregators for the parallel-ensemble node (v3 P3.B.0).

ADR-v3-9 retired the response-mode runner + ``aggregators/response/*``;
response strategies live under ``response_aggregator`` instead. The
parallel-ensemble node now ships only token-scope aggregators:

* ``token/`` — PN.py-style per-step aggregators (``sum_score`` /
  ``max_score``). Pair with ``TokenStepRunner``.

Importing this package executes the ``@register_aggregator``
side-effects, populating ``AggregatorRegistry`` before any node-level
``aggregator_registry.get(...)`` lookup happens.
"""

from __future__ import annotations

from . import token as token

__all__ = ["token"]
