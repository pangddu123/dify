"""Domain-specific exceptions for the ``token-model-source`` node.

The node only does two things at runtime — render a prompt template
and package a spec — so the failure surface is small. ``PromptRenderError``
covers both ``VariableTemplateParser`` parse failures and
"selector resolved to nothing in the variable pool" cases; keeping
both under one type lets ``node._run`` surface a single
``error_type=PromptRenderError`` to the panel without callers having
to switch on sub-classes.
"""

from __future__ import annotations


class TokenModelSourceNodeError(Exception):
    """Base for every runtime error this node raises.

    Mirrors the two-tier exception layout the v2.4 nodes use
    (``ResponseAggregatorNodeError`` etc.) so a higher-level handler
    can trap the whole family with one ``except``.
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class PromptRenderError(TokenModelSourceNodeError):
    """Raised when the prompt template cannot be rendered into a string.

    ``missing_var`` is the offending ``{{#node.field#}}`` key when the
    failure is "variable not present in pool" (the common case);
    ``None`` when the failure is structural (template malformed,
    selector value not text-renderable, etc.). ``reason`` carries the
    underlying cause in human-readable form so the panel surfaces a
    useful single-step debug message.
    """

    def __init__(self, *, template: str, missing_var: str | None, reason: str):
        self.template = template
        self.missing_var = missing_var
        self.reason = reason
        if missing_var is not None:
            msg = (
                f"Failed to render prompt template: variable {missing_var!r} "
                f"not available in the variable pool ({reason})"
            )
        else:
            msg = f"Failed to render prompt template: {reason}"
        super().__init__(msg)
