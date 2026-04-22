import type { Var, ValueSelector } from '../../types'
import type {
  AggregationInputRef,
  ConcatConfig,
  EnsembleAggregatorNodeType,
  EnsembleStrategyName,
} from './types'
import { produce } from 'immer'
import { useCallback } from 'react'
import { useNodesReadOnly } from '@/app/components/workflow/hooks'
import useAvailableVarList from '@/app/components/workflow/nodes/_base/hooks/use-available-var-list'
import useNodeCrud from '@/app/components/workflow/nodes/_base/hooks/use-node-crud'
import { VarType } from '../../types'

// Upstream text comes through graphon's `segment.text`, which renders
// strings, numbers, objects, and arrays into text. Files are excluded —
// aggregating binary references has no defined semantics for this node.
const TEXT_COMPATIBLE_VAR_TYPES: VarType[] = [
  VarType.string,
  VarType.number,
  VarType.boolean,
  VarType.object,
  VarType.array,
  VarType.arrayString,
  VarType.arrayNumber,
  VarType.arrayBoolean,
  VarType.arrayObject,
  VarType.any,
]

const useConfig = (id: string, payload: EnsembleAggregatorNodeType) => {
  const { nodesReadOnly: readOnly } = useNodesReadOnly()
  const { inputs, setInputs } = useNodeCrud<EnsembleAggregatorNodeType>(id, payload)

  const filterStringVar = useCallback((varPayload: Var) => {
    return TEXT_COMPATIBLE_VAR_TYPES.includes(varPayload.type)
  }, [])

  const { availableVars, availableNodesWithParent } = useAvailableVarList(id, {
    onlyLeafNodeVar: false,
    filterVar: filterStringVar,
  })

  const nextDefaultSourceId = useCallback((refs: AggregationInputRef[]) => {
    // Stable alias naming: `model_1`, `model_2`, … — user is expected to
    // rename, but the default must never collide with an existing entry
    // because the backend rejects duplicate source_id values.
    const existing = new Set(refs.map(r => r.source_id))
    let i = refs.length + 1
    while (existing.has(`model_${i}`))
      i += 1
    return `model_${i}`
  }, [])

  const handleAddInput = useCallback(() => {
    const next = produce(inputs, (draft) => {
      draft.inputs.push({
        source_id: nextDefaultSourceId(draft.inputs),
        variable_selector: [],
      })
    })
    setInputs(next)
  }, [inputs, setInputs, nextDefaultSourceId])

  const handleRemoveInput = useCallback((index: number) => {
    const next = produce(inputs, (draft) => {
      draft.inputs.splice(index, 1)
    })
    setInputs(next)
  }, [inputs, setInputs])

  const handleSourceIdChange = useCallback((index: number, value: string) => {
    const next = produce(inputs, (draft) => {
      if (draft.inputs[index])
        draft.inputs[index].source_id = value
    })
    setInputs(next)
  }, [inputs, setInputs])

  const handleVariableSelectorChange = useCallback(
    (index: number, selector: ValueSelector) => {
      const next = produce(inputs, (draft) => {
        if (draft.inputs[index])
          draft.inputs[index].variable_selector = selector
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleStrategyChange = useCallback((name: EnsembleStrategyName) => {
    const next = produce(inputs, (draft) => {
      draft.strategy_name = name
      // Reset config on strategy switch — the previous strategy's fields
      // are rejected by the new strategy's `extra="forbid"` validator.
      draft.strategy_config = {}
    })
    setInputs(next)
  }, [inputs, setInputs])

  const handleStrategyConfigChange = useCallback(
    (patch: Partial<ConcatConfig>) => {
      const next = produce(inputs, (draft) => {
        const merged: Record<string, unknown> = { ...draft.strategy_config }
        // `undefined` in the patch means "remove the key" so the backend
        // falls back to its Pydantic default. Spread-merging would keep
        // the undefined value, which then gets serialized as explicit
        // `null`/omitted inconsistently by downstream encoders.
        for (const [key, value] of Object.entries(patch)) {
          if (value === undefined)
            delete merged[key]
          else
            merged[key] = value
        }
        draft.strategy_config = merged
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  return {
    readOnly,
    inputs,
    availableVars,
    availableNodesWithParent,
    filterStringVar,
    handleAddInput,
    handleRemoveInput,
    handleSourceIdChange,
    handleVariableSelectorChange,
    handleStrategyChange,
    handleStrategyConfigChange,
  }
}

export default useConfig
