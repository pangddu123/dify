"""Concat response strategy.

v3 upgrade: optional ``order_by_weight`` flag (default off) sorts
fragments by descending ``context.weights`` before joining — useful
when downstream readers want the strongest source first. With the
flag off (default), output order matches the declared ``inputs`` list,
preserving v2.4 behaviour byte-for-byte.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from .base import (
    ResponseAggregationResult,
    ResponseAggregator,
    ResponseSignal,
    SourceAggregationContext,
)
from .registry import register


class _ConcatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    separator: str = "\n\n---\n\n"
    include_source_label: bool = False
    order_by_weight: bool = False
    """Sort fragments by descending ``context.weights`` before joining.
    Stable on input order when weights tie, so DSLs that set this
    flag still get deterministic output."""


@register("concat")
class ConcatStrategy(ResponseAggregator[_ConcatConfig]):
    config_class: ClassVar[type[BaseModel]] = _ConcatConfig
    i18n_key_prefix: ClassVar[str] = "nodes.ensembleAggregator.concat"
    ui_schema: ClassVar[dict] = {
        "separator": {"control": "text_input"},
        "include_source_label": {"control": "switch"},
        "order_by_weight": {"control": "switch"},
    }

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: SourceAggregationContext,
        config: _ConcatConfig,
    ) -> ResponseAggregationResult:
        ordered = list(signals)
        if config.order_by_weight:
            # Stable sort: equal weights keep insertion order. ``-weight``
            # delivers descending without breaking the stable contract.
            weights = context.weights
            ordered.sort(key=lambda s: -weights.get(s["source_id"], 1.0))

        if config.include_source_label:
            parts = [f"[{s['source_id']}]\n{s['text']}" for s in ordered]
        else:
            parts = [s["text"] for s in ordered]

        contributions: dict[str, str] = {s["source_id"]: s["text"] for s in signals}

        return {
            "text": config.separator.join(parts),
            "metadata": {
                "strategy": "concat",
                "separator": config.separator,
                "include_source_label": config.include_source_label,
                "order_by_weight": config.order_by_weight,
                "contributions": contributions,
            },
        }
