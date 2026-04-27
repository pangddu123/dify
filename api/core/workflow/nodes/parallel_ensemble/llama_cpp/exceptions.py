"""Exceptions for the llama.cpp model registry + client.

Hierarchy mirrors `ensemble_aggregator/exceptions.py`: a single base for
catch-all `except` blocks at the node layer, plus narrow subclasses that
keep semantic fields (alias, path) so callers can build user-facing
messages without re-parsing the message string.
"""

from __future__ import annotations


class LlamaCppNodeError(Exception):
    """Base class for all llama.cpp registry / client errors."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class ModelRegistryError(LlamaCppNodeError):
    """Base class for `LocalModelRegistry` errors."""


class RegistryFileError(ModelRegistryError):
    """Raised when the registry yaml is present but unreadable / malformed.

    Missing-file is *not* an error (R9): we keep the registry empty and log
    a warning so the API process still boots without a `model_net.yaml`.
    """

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to load model registry from '{path}': {reason}")


class UnknownModelAliasError(ModelRegistryError):
    """Raised when a workflow node references an alias not in the registry."""

    def __init__(self, alias: str):
        self.alias = alias
        super().__init__(f"Unknown model alias: '{alias}'")
