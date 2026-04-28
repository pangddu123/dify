import { useQuery } from '@tanstack/react-query'
import { consoleQuery } from '@/service/client'

// Three console GET endpoints feeding the panel's three axis dropdowns.
// All three are contract-first via ``web/contract/console/parallel-
// ensemble.ts`` (registered under ``consoleRouterContract.parallel
// Ensemble.*``); we consume them via ``consoleQuery.parallelEnsemble.
// <name>.queryOptions()`` per ``web/AGENTS.md`` Mandatory Query &
// Mutation rule and the ``frontend-query-mutation`` skill.
//
// Why a generous ``staleTime``: the underlying ``ModelRegistry`` /
// ``RunnerRegistry`` / ``AggregatorRegistry`` only reload when the
// operator edits ``api/configs/model_net.yaml`` and restarts the
// backend, so a 5-minute window de-duplicates the fetch when the
// panel is closed and re-opened in the same editor session.

const STATIC_REGISTRY_OPTS = {
  staleTime: 5 * 60 * 1000,
  gcTime: 30 * 60 * 1000,
  // No retries: a 401 here means the user lost session, retrying with
  // the same cookie just burns time before the auth wrapper redirects.
  retry: false,
} as const

export const useLocalModels = () => {
  return useQuery(
    consoleQuery.parallelEnsemble.localModels.queryOptions(STATIC_REGISTRY_OPTS),
  )
}

export const useRunners = () => {
  return useQuery(
    consoleQuery.parallelEnsemble.runners.queryOptions(STATIC_REGISTRY_OPTS),
  )
}

export const useAggregators = () => {
  return useQuery(
    consoleQuery.parallelEnsemble.aggregators.queryOptions(STATIC_REGISTRY_OPTS),
  )
}
