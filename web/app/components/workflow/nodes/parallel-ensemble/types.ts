import type { CommonNodeType, ValueSelector } from '@/app/components/workflow/types'

export const PARALLEL_ENSEMBLE_NODE_TYPE = 'parallel-ensemble' as const

// Mirrors backend ``UI_CONTROL_ALLOWLIST`` (api/core/workflow/nodes/
// parallel_ensemble/spi/runner.py). Adding a new control is intentionally
// a framework-level change — both the SPI runner module and this
// allowlist must be updated together, otherwise the panel renders
// nothing for that field. Kept as a string-literal union so TS catches
// a typo at compile time.
export const UI_CONTROLS = [
  'number_input',
  'text_input',
  'textarea',
  'switch',
  'select',
  'multi_select',
  'model_alias_select',
] as const
export type UiControl = (typeof UI_CONTROLS)[number]

// Per-field ui_schema entry. The backend hands us free-form ``dict`` so
// we keep this open with the four common knobs the v0.2 controls
// support; a runner declaring an unknown control name is rejected by
// ``DynamicConfigForm`` at render time (mirrors the SPI's startup check).
export type UiFieldSchema = {
  control: UiControl
  // number_input
  min?: number
  max?: number
  step?: number
  // select / multi_select
  options?: ReadonlyArray<{ value: string | number, label?: string }>
}

export type UiSchema = Record<string, UiFieldSchema>

// ── Backend metadata projections ────────────────────────────────────
//
// These mirror the JSON shapes returned by the three console endpoints
// added in P2.9-adjacent backend work:
//   GET /workspaces/current/local-models  -> { models: BackendInfo[] }
//   GET /workspaces/current/runners       -> { runners: RunnerMeta[] }
//   GET /workspaces/current/aggregators   -> { aggregators: AggregatorMeta[] }
// They are *projections*, not the SPI Python types — url / api_key /
// api_key_env are intentionally absent from BackendInfo (T2 SSRF /
// credential boundary, see EXTENSIBILITY_SPEC §4.4).

export type BackendInfo = {
  id: string
  backend: string
  model_name: string
  capabilities: string[]
  metadata: Record<string, unknown>
}

export type RunnerMeta = {
  name: string
  i18n_key_prefix: string
  ui_schema: UiSchema
  config_schema: Record<string, unknown>
  aggregator_scope: string
  required_capabilities: string[]
  optional_capabilities: string[]
}

export type AggregatorMeta = {
  name: string
  i18n_key_prefix: string
  ui_schema: UiSchema
  config_schema: Record<string, unknown>
  scope: string
}

// ── Node data shape (DSL surface) ───────────────────────────────────
//
// Mirrors backend ``ParallelEnsembleConfig`` (api/core/workflow/nodes/
// parallel_ensemble/entities.py). The nested ``ensemble`` wrapper is
// the SSRF / credential boundary the Pydantic ``extra="forbid"`` lives
// on — we keep the same nesting frontend-side so ``checkValid`` can
// guard the same surface the backend will reject anyway.

export type DiagnosticsStorage = 'inline' | 'metadata'

export type DiagnosticsConfig = {
  include_model_outputs: boolean
  include_response_timings: boolean
  include_token_candidates: boolean
  include_logits: boolean
  include_aggregator_reasoning: boolean
  max_trace_tokens: number
  include_think_trace: boolean
  include_per_backend_errors: boolean
  storage: DiagnosticsStorage
}

// runner_config / aggregator_config are ``dict[str, object]`` server-
// side; the runner / aggregator's own pydantic ``config_class`` is the
// real schema and lives in ``RunnerMeta.config_schema`` /
// ``AggregatorMeta.config_schema``. Frontend keeps the dict shape and
// reflects controls off ``ui_schema``.
export type ConfigBlob = Record<string, unknown>

// Mirrors backend ``TokenSourceRef`` (api/core/workflow/nodes/
// parallel_ensemble/entities.py, ADR-v3-16). Each entry is one upstream
// ``token-model-source`` node contributing to the joint-vote loop:
// ``spec_selector`` points at that source's ``outputs.spec`` field, and
// the parallel-ensemble node resolves it against the variable pool at
// run time to recover a ``ModelInvocationSpec`` (alias + prompt +
// sampling_params). Prompt rendering and alias selection live upstream
// — this layer only carries weight + the one sampling knob (``top_k``)
// the joint-vote algorithm needs aligned across voters.
//
// ``weight`` mirrors ``AggregationInputRef.weight`` (static finite
// number OR ``VariableSelector``-shaped ``list[str]``); ``fallback_weight``
// only takes effect on the dynamic branch (ADR-v3-15).
export type TokenSourceRef = {
  source_id: string
  spec_selector: ValueSelector
  weight: number | ValueSelector
  // ADR-v3-6: optional per-source override for the spec's ``top_k``.
  // ``null`` keeps the upstream spec's ``sampling_params.top_k``.
  top_k_override: number | null
  // ``null`` (default) = fail-fast on dynamic-weight resolution failure.
  // Setting a number opts into graceful-degrade mode (ADR-v3-15).
  fallback_weight: number | null
  extra: Record<string, unknown>
}

export type ParallelEnsembleConfig = {
  token_sources: TokenSourceRef[]
  runner_name: string
  runner_config: ConfigBlob
  aggregator_name: string
  aggregator_config: ConfigBlob
  diagnostics: DiagnosticsConfig
}

export type ParallelEnsembleNodeType = CommonNodeType & {
  ensemble: ParallelEnsembleConfig
}

// After ADR-v3-16 the parallel-ensemble node is token-mode-only:
// ``response_level`` runner + ``majority_vote`` / ``concat`` aggregators
// were lifted out (response-level ensembling now lives on the
// response-aggregator node). Backend currently only registers
// ``token_step`` + the token-scope aggregators (``sum_score``,
// ``max_score``); a fresh node must default to that pair so saving an
// untouched node never produces a DSL the §9 startup pipeline rejects
// at run time.
export const DEFAULT_RUNNER_NAME = 'token_step'
export const DEFAULT_AGGREGATOR_NAME = 'sum_score'

export const DEFAULT_DIAGNOSTICS: DiagnosticsConfig = {
  include_model_outputs: false,
  include_response_timings: true,
  include_token_candidates: false,
  include_logits: false,
  include_aggregator_reasoning: false,
  max_trace_tokens: 1000,
  include_think_trace: false,
  include_per_backend_errors: true,
  storage: 'metadata',
}

// Top-level keys the backend ``ParallelEnsembleNodeData`` hard-rejects
// before pydantic stores them in ``__pydantic_extra__`` (entities.py
// ``_FORBIDDEN_TOP_LEVEL_KEYS``). The import-model-info button strips
// these client-side too so a yaml-style ``model_info.json`` paste with
// urls keeps the urls out of the saved DSL.
export const FORBIDDEN_DSL_KEYS = [
  'model_url',
  'api_key',
  'api_key_env',
  'url',
  'endpoint',
] as const

export type ValidationIssue = {
  severity: 'error' | 'warning'
  message: string
  i18n_key?: string
  field?: string
}
