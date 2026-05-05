import type { UiSchema } from '@/app/components/workflow/nodes/parallel-ensemble/types'
import type { CommonNodeType, ValueSelector } from '@/app/components/workflow/types'

export const RESPONSE_AGGREGATOR_NODE_TYPE = 'response-aggregator' as const

export type ResponseStrategyName = 'concat'

export const RESPONSE_STRATEGY_NAMES: ResponseStrategyName[] = [
  'concat',
]

// Mirrors backend ``concat._ConcatConfig`` (api/core/workflow/nodes/
// response_aggregator/strategies/concat.py). v3 added ``order_by_weight``
// — when on, the strategy sorts fragments by descending source weight
// before joining.
export type ConcatConfig = {
  separator?: string
  include_source_label?: boolean
  order_by_weight?: boolean
}

export const DEFAULT_CONCAT_SEPARATOR = '\n\n---\n\n'

// Matches backend ``dict[str, object]``. Strategy-specific shapes
// (e.g. ``ConcatConfig``) are narrowed inside the strategy selector.
export type ResponseStrategyConfig = Record<string, unknown>

// Per-strategy ui_schema mirror. Mirrors what backend
// ``list_strategies()`` exposes (api/core/workflow/nodes/
// response_aggregator/strategies/registry.py:list_strategies); ships
// statically here because the strategy set is closed and built into
// this node — the backend's ``extra="forbid"`` on each strategy's
// config_class catches drift if a key is added on one side only.
//
// ``i18n_key_prefix`` matches the backend ``i18n_key_prefix`` ClassVar
// so dynamic-config-form looks up
// ``<prefix>.fields.<field>.{label,tooltip}`` consistently with how
// parallel-ensemble drives runner / aggregator forms.
export type ResponseStrategyMeta = {
  name: ResponseStrategyName
  i18n_key_prefix: string
  ui_schema: UiSchema
}

export const RESPONSE_STRATEGY_META: Record<
  ResponseStrategyName,
  ResponseStrategyMeta
> = {
  concat: {
    name: 'concat',
    i18n_key_prefix: 'nodes.responseAggregator.concat',
    ui_schema: {
      separator: { control: 'text_input' },
      include_source_label: { control: 'switch' },
      order_by_weight: { control: 'switch' },
    },
  },
}

// Static + dynamic weight surfaces mirror backend ``AggregationInputRef.
// weight`` (Pydantic ``float | list[str]``). The ``list[str]`` branch is a
// ``VariableSelector`` resolved at runtime against the variable pool —
// same shape as ``variable_selector`` so the runtime resolver doesn't
// have to special-case malformed input. ADR-v3-15.
export type AggregationInputRef = {
  source_id: string
  variable_selector: ValueSelector
  weight: number | ValueSelector
  // Numeric fallback when a dynamic weight selector fails to resolve.
  // ``null`` (default) = fail-fast: the backend raises
  // ``WeightResolutionError`` and the node FAILs.
  fallback_weight: number | null
  // Per-source pass-through metadata. Surfaced to strategies via
  // ``SourceAggregationContext.source_meta`` server-side; UI keeps the
  // dict shape so authors of custom strategies can ride extra context
  // (e.g. ``{"confidence_tier": "high"}``) without DSL edits.
  extra: Record<string, unknown>
}

export type ResponseAggregatorNodeType = CommonNodeType & {
  inputs: AggregationInputRef[]
  strategy_name: ResponseStrategyName
  strategy_config: ResponseStrategyConfig
}
