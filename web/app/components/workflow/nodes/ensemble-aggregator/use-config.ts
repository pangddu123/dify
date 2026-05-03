import type { ValueSelector, Var } from '../../types'
import type {
  AggregationInputRef,
  EnsembleAggregatorNodeType,
  EnsembleStrategyConfig,
  EnsembleStrategyName,
} from './types'
import { produce } from 'immer'
import { useCallback } from 'react'
import { useNodesReadOnly } from '@/app/components/workflow/hooks'
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

// Weight var-reference picker accepts the numeric-shaped types only —
// string / object / array would either need coercion (silent drift) or
// break the backend's finite-number guard. Match backend
// ``_resolve_weight`` which expects ``int | float`` from the var pool.
const NUMERIC_VAR_TYPES: VarType[] = [
  VarType.number,
  VarType.any,
]

const useConfig = (id: string, payload: EnsembleAggregatorNodeType) => {
  const { nodesReadOnly: readOnly } = useNodesReadOnly()
  const { inputs, setInputs } = useNodeCrud<EnsembleAggregatorNodeType>(id, payload)

  const filterStringVar = useCallback((varPayload: Var) => {
    return TEXT_COMPATIBLE_VAR_TYPES.includes(varPayload.type)
  }, [])

  const filterNumericVar = useCallback((varPayload: Var) => {
    return NUMERIC_VAR_TYPES.includes(varPayload.type)
  }, [])

  // `VarReferencePicker` (used inside `InputList`) runs its own
  // `useAvailableVarList` when rendered, so computing/returning the list
  // here is a redundant walk of the graph. Keep only the filter helpers,
  // which the picker needs via props.

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
        // ``1.0`` keeps the input neutral relative to existing peers —
        // ``concat``'s ``order_by_weight`` flag treats unit weights as
        // a tie and preserves declared input order.
        weight: 1,
        // ``null`` = fail-fast on dynamic weight resolution failure
        // (ADR-v3-15). Operators opt into graceful degrade explicitly.
        fallback_weight: null,
        extra: {},
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

  const handleWeightChange = useCallback(
    (index: number, value: number | ValueSelector) => {
      const next = produce(inputs, (draft) => {
        if (draft.inputs[index])
          draft.inputs[index].weight = value
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleFallbackWeightChange = useCallback(
    (index: number, value: number | null) => {
      const next = produce(inputs, (draft) => {
        if (draft.inputs[index])
          draft.inputs[index].fallback_weight = value
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
    (patch: EnsembleStrategyConfig) => {
      // ``DynamicConfigForm`` already handles the "patch contains all
      // current keys minus deletions" semantics for us: it emits the
      // full ``ConfigBlob`` snapshot (with any ``undefined`` keys
      // already stripped via that component's ``handlePatchKey``).
      // Treat the patch as the new authoritative ``strategy_config``.
      const next = produce(inputs, (draft) => {
        draft.strategy_config = { ...patch }
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  return {
    readOnly,
    inputs,
    filterStringVar,
    filterNumericVar,
    handleAddInput,
    handleRemoveInput,
    handleSourceIdChange,
    handleVariableSelectorChange,
    handleWeightChange,
    handleFallbackWeightChange,
    handleStrategyChange,
    handleStrategyConfigChange,
  }
}

export default useConfig
