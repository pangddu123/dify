class EnsembleAggregatorNodeError(Exception):
    """Base exception for all ensemble aggregator node errors."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class StrategyNotFoundError(EnsembleAggregatorNodeError):
    """Raised when the specified aggregation strategy is not registered."""

    def __init__(self, strategy_name: str):
        self.strategy_name = strategy_name
        super().__init__(f"Aggregation strategy '{strategy_name}' is not registered")


class MissingInputError(EnsembleAggregatorNodeError):
    """Raised when a referenced upstream variable is not present in the variable pool."""

    def __init__(self, source_id: str, variable_selector: list[str]):
        self.source_id = source_id
        self.variable_selector = variable_selector
        super().__init__(
            f"Upstream variable for source '{source_id}' "
            f"(selector={variable_selector}) not available in variable pool"
        )


class StrategyConfigError(EnsembleAggregatorNodeError):
    """Raised when strategy_config fails validation for a given strategy."""

    def __init__(self, strategy_name: str, message: str):
        self.strategy_name = strategy_name
        super().__init__(f"Invalid config for strategy '{strategy_name}': {message}")


class WeightResolutionError(EnsembleAggregatorNodeError):
    """Raised when a dynamic ``weight`` selector cannot be resolved.

    The node FAILs by default (ADR-v3-15 fail-fast); only when the
    user opts into a numeric ``fallback_weight`` does the node skip
    raising and trace-warn instead. ``input_id`` is the offending
    source_id, ``selector`` is the unresolved selector, ``reason``
    captures the underlying cause (missing variable / non-numeric value).
    """

    def __init__(self, input_id: str, selector: list[str], reason: str):
        self.input_id = input_id
        self.selector = selector
        self.reason = reason
        super().__init__(
            f"Failed to resolve weight for source '{input_id}' "
            f"(selector={selector}): {reason}"
        )
