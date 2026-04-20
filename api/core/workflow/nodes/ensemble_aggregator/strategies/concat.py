from pydantic import BaseModel, ConfigDict, ValidationError

from ..exceptions import StrategyConfigError
from .base import AggregationInput, AggregationResult, AggregationStrategy
from .registry import register


class _ConcatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    separator: str = "\n\n---\n\n"
    include_source_label: bool = False


@register("concat")
class ConcatStrategy(AggregationStrategy):
    config_schema = {
        "type": "object",
        "properties": {
            "separator": {
                "type": "string",
                "default": "\n\n---\n\n",
            },
            "include_source_label": {
                "type": "boolean",
                "default": False,
            },
        },
        "additionalProperties": False,
    }

    def aggregate(
        self,
        inputs: list[AggregationInput],
        config: dict[str, object],
    ) -> AggregationResult:
        try:
            parsed = _ConcatConfig.model_validate(config)
        except ValidationError as e:
            raise StrategyConfigError("concat", str(e)) from e

        if parsed.include_source_label:
            parts = [f"[{item['source_id']}]\n{item['text']}" for item in inputs]
        else:
            parts = [item["text"] for item in inputs]

        contributions: dict[str, str] = {
            item["source_id"]: item["text"] for item in inputs
        }

        return {
            "text": parsed.separator.join(parts),
            "metadata": {
                "strategy": "concat",
                "separator": parsed.separator,
                "include_source_label": parsed.include_source_label,
                "contributions": contributions,
            },
        }
