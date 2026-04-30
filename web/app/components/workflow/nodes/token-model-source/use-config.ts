import type { SamplingParams, TokenModelSourceNodeType } from './types'
import { produce } from 'immer'
import { useCallback, useMemo } from 'react'
import { useNodesReadOnly } from '@/app/components/workflow/hooks'
import useNodeCrud from '@/app/components/workflow/nodes/_base/hooks/use-node-crud'
// ``useLocalModels`` already lives under ``parallel-ensemble`` (P2.11) —
// importing it here keeps the wire contract single-sourced. Both nodes
// hit ``GET /workspaces/current/local-models`` and de-dupe via the
// react-query staleTime; pulling it out into a shared hooks module
// would be a premature abstraction with only two consumers.
import { useLocalModels } from '../parallel-ensemble/use-registries'
import { DEFAULT_SAMPLING_PARAMS } from './types'

const useConfig = (id: string, payload: TokenModelSourceNodeType) => {
  const { nodesReadOnly: readOnly } = useNodesReadOnly()
  const { inputs, setInputs } = useNodeCrud<TokenModelSourceNodeType>(id, payload)

  const localModelsQuery = useLocalModels()
  const models = useMemo(
    () => localModelsQuery.data?.models ?? [],
    [localModelsQuery.data],
  )

  // ── Mutation handlers ───────────────────────────────────────────

  const handleModelAliasChange = useCallback(
    (alias: string) => {
      const next = produce(inputs, (draft) => {
        draft.model_alias = alias
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handlePromptTemplateChange = useCallback(
    (template: string) => {
      const next = produce(inputs, (draft) => {
        draft.prompt_template = template
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleSamplingParamsChange = useCallback(
    (patch: Partial<SamplingParams>) => {
      const next = produce(inputs, (draft) => {
        draft.sampling_params = {
          // ``DEFAULT_SAMPLING_PARAMS`` first so a DSL that landed
          // without a ``sampling_params`` block (legacy / hand-edited)
          // still gets the canonical floor; ``draft.sampling_params``
          // second so user-set fields override; ``patch`` last for the
          // current edit.
          ...DEFAULT_SAMPLING_PARAMS,
          ...draft.sampling_params,
          ...patch,
        }
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleExtraChange = useCallback(
    (extra: Record<string, unknown>) => {
      const next = produce(inputs, (draft) => {
        draft.extra = extra
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  return {
    readOnly,
    inputs,
    models,
    isLoadingModels: localModelsQuery.isLoading,
    handleModelAliasChange,
    handlePromptTemplateChange,
    handleSamplingParamsChange,
    handleExtraChange,
  }
}

export default useConfig
