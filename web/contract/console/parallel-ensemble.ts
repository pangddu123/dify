import type {
  AggregatorMeta,
  BackendInfo,
  RunnerMeta,
} from '@/app/components/workflow/nodes/parallel-ensemble/types'
import { type } from '@orpc/contract'
import { base } from '../base'

// Three read-only console endpoints feeding the parallel-ensemble
// node's three axis dropdowns:
//
//   GET /workspaces/current/local-models  → BackendInfo[]
//   GET /workspaces/current/runners       → RunnerMeta[]
//   GET /workspaces/current/aggregators   → AggregatorMeta[]
//
// Backend projection helpers live in
// ``api/controllers/console/workspace/{local_models,runners,
// aggregators}.py`` (the ``_project_*`` functions strip url / api_key /
// api_key_env from the BackendInfo wire shape — that's the SSRF /
// credential boundary documented in EXTENSIBILITY_SPEC §4.4 T2).
//
// All three are static-ish: they reload only when the operator edits
// ``api/configs/model_net.yaml`` and restarts the backend, so consumers
// in ``service/use-parallel-ensemble.ts`` apply a generous staleTime.

export const parallelEnsembleLocalModelsContract = base
  .route({
    path: '/workspaces/current/local-models',
    method: 'GET',
  })
  .output(type<{ models: BackendInfo[] }>())

export const parallelEnsembleRunnersContract = base
  .route({
    path: '/workspaces/current/runners',
    method: 'GET',
  })
  .output(type<{ runners: RunnerMeta[] }>())

export const parallelEnsembleAggregatorsContract = base
  .route({
    path: '/workspaces/current/aggregators',
    method: 'GET',
  })
  .output(type<{ aggregators: AggregatorMeta[] }>())
