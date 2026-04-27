"""Response-scope ``concat`` aggregator (P2.5 v2.4 migration).

Joins every backend's response with a configurable separator. Same
behaviour as the P1 ConcatStrategy; metadata keys preserved.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ...registry.aggregator_registry import register_aggregator
from ...spi.aggregator import (
    AggregationContext,
    ResponseAggregationResult,
    ResponseAggregator,
    ResponseSignal,
)


class ConcatConfig(BaseModel):
    """Two knobs: separator string and whether to prefix each chunk
    with its ``source_id`` for downstream provenance display."""

    model_config = ConfigDict(extra="forbid")

    separator: str = "\n\n---\n\n"
    include_source_label: bool = False


@register_aggregator("concat", scope="response")
class ConcatAggregator(ResponseAggregator[ConcatConfig]):
    """Concatenate every backend's response in input order."""

    config_class: ClassVar[type[BaseModel]] = ConcatConfig
    i18n_key_prefix: ClassVar[str] = "parallelEnsemble.aggregators.concat"
    ui_schema: ClassVar[dict] = {
        "separator": {"control": "text_input"},
        "include_source_label": {"control": "switch"},
    }

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: AggregationContext,
        config: ConcatConfig,
    ) -> ResponseAggregationResult:
        valid = [s for s in signals if s.get("error") is None]

        if config.include_source_label:
            parts = [f"[{s['source_id']}]\n{s['text']}" for s in valid]
        else:
            parts = [s["text"] for s in valid]

        contributions: dict[str, str] = {s["source_id"]: s["text"] for s in valid}

        return {
            "text": config.separator.join(parts),
            "metadata": {
                "strategy": "concat",
                "separator": config.separator,
                "include_source_label": config.include_source_label,
                "contributions": contributions,
            },
        }
