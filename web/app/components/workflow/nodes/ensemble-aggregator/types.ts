import type { CommonNodeType, ValueSelector } from '@/app/components/workflow/types'

export const ENSEMBLE_AGGREGATOR_NODE_TYPE = 'ensemble-aggregator' as const

export type EnsembleStrategyName = 'majority_vote' | 'concat'

export const ENSEMBLE_STRATEGY_NAMES: EnsembleStrategyName[] = [
  'majority_vote',
  'concat',
]

// Config shape for `concat`. `majority_vote` currently takes no options;
// the backend enforces `extra="forbid"`, so any unknown keys submitted
// in `strategy_config` are rejected at run time.
export type ConcatConfig = {
  separator?: string
  include_source_label?: boolean
}

export const DEFAULT_CONCAT_SEPARATOR = '\n\n---\n\n'

// Matches backend `dict[str, object]`. Strategy-specific shapes
// (e.g. `ConcatConfig`) are narrowed inside the strategy selector.
export type EnsembleStrategyConfig = Record<string, unknown>

// Mirrors backend `AggregationInputRef` (api/core/workflow/nodes/
// ensemble_aggregator/entities.py). `variable_selector` stays as the
// graphon selector shape (`[node_id, key, ...]`) — same as every other
// workflow node's upstream reference.
export type AggregationInputRef = {
  source_id: string
  variable_selector: ValueSelector
}

export type EnsembleAggregatorNodeType = CommonNodeType & {
  inputs: AggregationInputRef[]
  strategy_name: EnsembleStrategyName
  strategy_config: EnsembleStrategyConfig
}
