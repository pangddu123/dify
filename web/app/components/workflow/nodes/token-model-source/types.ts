import type { CommonNodeType } from '@/app/components/workflow/types'

export const TOKEN_MODEL_SOURCE_NODE_TYPE = 'token-model-source' as const

// Mirrors backend ``SamplingParams`` (api/core/workflow/nodes/
// token_model_source/entities.py). ``extra="forbid"`` is enforced at
// the backend Pydantic layer; on this side we keep the shape narrow so
// a typo (``temprature``) in the saved DSL becomes a TS error rather
// than a silent no-op at run time.
export type SamplingParams = {
  top_k: number
  temperature: number
  max_tokens: number
  top_p: number | null
  seed: number | null
  stop: string[]
}

// ``ModelInvocationSpec`` is the wire shape this node emits into the
// variable pool — downstream ``parallel-ensemble`` (P3.B.3) consumes it
// by selector. Frontend never instantiates one; the type lives here
// purely so panels that *consume* the spec selector (P3.B.4) can pin
// the expected shape statically.
export type ModelInvocationSpec = {
  model_alias: string
  prompt: string
  sampling_params: SamplingParams
  extra: Record<string, unknown>
}

export type TokenModelSourceNodeType = CommonNodeType & {
  model_alias: string
  prompt_template: string
  sampling_params: SamplingParams
  extra: Record<string, unknown>
}

// Defaults track ``SamplingParams`` Field defaults (entities.py): the
// backend-level form is the canonical source. Diverging defaults here
// would let a panel-saved DSL look one way pre-load and another
// post-validate — the round-trip mismatch CLAUDE.md flags.
export const DEFAULT_SAMPLING_PARAMS: SamplingParams = {
  top_k: 10,
  temperature: 0.7,
  max_tokens: 1024,
  top_p: null,
  seed: null,
  stop: [],
}
