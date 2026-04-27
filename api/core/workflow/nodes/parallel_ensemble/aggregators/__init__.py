"""Built-in aggregators for the parallel-ensemble node (P2.5).

Two scopes ship with v0.2:

* ``response/`` — response-level aggregators (smoothly migrated from
  P1's ``ensemble_aggregator`` strategies). Pair with ``ResponseLevelRunner``.
* ``token/`` — PN.py-style per-step aggregators (sum / max). Pair with
  ``TokenStepRunner``.

The submodule imports below run the ``@register_aggregator`` decorators
as a side effect, populating ``AggregatorRegistry`` before the node-level
``aggregator_registry.get(...)`` lookup happens. Importing this package
is therefore enough to make the four built-ins discoverable.
"""

from __future__ import annotations

from . import response as response
from . import token as token

__all__ = ["response", "token"]
