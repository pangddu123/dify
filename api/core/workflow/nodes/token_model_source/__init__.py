"""Token-model-source workflow node package (P3.B.1, ADR-v3-4 / v3-10).

Configuration-holder node for token-mode ensembles: renders a prompt
template against the variable pool, packages the rendered prompt with
its target ``model_alias`` and per-source sampling knobs into a
``ModelInvocationSpec``, and pushes the spec into the variable pool.
The downstream ``parallel-ensemble`` node (P3.B.3) is the executor —
this node does **not** call any model itself (ADR-v3-10).
"""

TOKEN_MODEL_SOURCE_NODE_TYPE = "token-model-source"

from .node import TokenModelSourceNode

__all__ = ["TOKEN_MODEL_SOURCE_NODE_TYPE", "TokenModelSourceNode"]
