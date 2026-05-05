import type {
  AggregatorMeta,
  ConfigBlob,
  DiagnosticsConfig,
  ParallelEnsembleNodeType,
  RunnerMeta,
  TokenSourceRef,
  ValidationIssue,
} from './types'
import type { ValueSelector, Var } from '@/app/components/workflow/types'
import { produce } from 'immer'
import { useCallback, useMemo } from 'react'
import { useNodesReadOnly } from '@/app/components/workflow/hooks'
import useNodeCrud from '@/app/components/workflow/nodes/_base/hooks/use-node-crud'
import { VarType } from '@/app/components/workflow/types'
import { DEFAULT_DIAGNOSTICS } from './types'
import { useAggregators, useRunners } from './use-registries'

// ``token-model-source`` outputs ``spec`` as ``object`` (see that node's
// panel.tsx OutputVars block); accept ``any`` as a permissive fallback
// for variables coming from third-party sources whose VarType the
// inference layer cannot pin down.
const SPEC_VAR_TYPES: VarType[] = [
  VarType.object,
  VarType.any,
]

// ``weight`` dynamic-mode picker accepts numeric-shaped types only —
// silently coercing strings / objects to numbers would break the
// backend's finite-number guard in ``_resolve_weight``.
const NUMERIC_VAR_TYPES: VarType[] = [
  VarType.number,
  VarType.any,
]

const useConfig = (id: string, payload: ParallelEnsembleNodeType) => {
  const { nodesReadOnly: readOnly } = useNodesReadOnly()
  const { inputs, setInputs } = useNodeCrud<ParallelEnsembleNodeType>(id, payload)

  const runnersQuery = useRunners()
  const aggregatorsQuery = useAggregators()

  // The orpc/contract ``type<T>()`` machinery widens the inner
  // ``options`` field to ``unknown`` when the runner / aggregator
  // ui_schema arrives over the wire — re-cast through the SPI types
  // so the rest of the hook sees the same shape ``RunnerMeta`` /
  // ``AggregatorMeta`` declare. The contract still pins the wire
  // schema (``contract/console/parallel-ensemble.ts``); the assertion
  // is purely a TypeScript-side narrowing.
  const runners = useMemo<ReadonlyArray<RunnerMeta>>(
    () => (runnersQuery.data?.runners ?? []) as ReadonlyArray<RunnerMeta>,
    [runnersQuery.data],
  )
  const aggregators = useMemo<ReadonlyArray<AggregatorMeta>>(
    () => (aggregatorsQuery.data?.aggregators ?? []) as ReadonlyArray<AggregatorMeta>,
    [aggregatorsQuery.data],
  )

  // Resolve the *currently selected* runner / aggregator descriptors
  // from the registry fetches. Falls back to ``undefined`` while
  // loading — components downstream gate their dropdowns on the
  // ``isLoading`` flag rather than the descriptor presence so the user
  // sees "Loading…" instead of an empty placeholder during boot.
  const runnerName = inputs.ensemble?.runner_name
  const aggregatorName = inputs.ensemble?.aggregator_name
  const selectedRunner = useMemo(
    () => runners.find(r => r.name === runnerName),
    [runners, runnerName],
  )
  const selectedAggregator = useMemo(
    () => aggregators.find(a => a.name === aggregatorName),
    [aggregators, aggregatorName],
  )

  // Backend ``ParallelEnsembleNode._validate_at_startup`` runs the §9
  // pipeline (capability filter + requirements match + cross-field) at
  // run time against the resolved ``ModelInvocationSpec``s — selectors
  // the panel cannot resolve statically. The only check we can do here
  // without the live variable pool is scope-match between the runner
  // and aggregator; capability-mismatch errors now surface server-side
  // with structured field metadata when the spec resolves.
  const validationIssues: ValidationIssue[] = useMemo(() => {
    const issues: ValidationIssue[] = []
    // Wait for the registries before flagging "unknown" — if the fetch
    // is still in flight, ``runners.length === 0`` is not the same as
    // "name doesn't exist". Hold the issue until we have ground truth.
    const runnersReady = !runnersQuery.isLoading && runners.length > 0
    const aggregatorsReady = !aggregatorsQuery.isLoading && aggregators.length > 0

    if (runnerName && runnersReady && !selectedRunner) {
      issues.push({
        severity: 'error',
        field: 'runner_name',
        message: `Runner "${runnerName}" is not registered`,
        i18n_key: 'parallelEnsemble.errors.runnerNotRegistered',
      })
    }
    if (aggregatorName && aggregatorsReady && !selectedAggregator) {
      issues.push({
        severity: 'error',
        field: 'aggregator_name',
        message: `Aggregator "${aggregatorName}" is not registered`,
        i18n_key: 'parallelEnsemble.errors.aggregatorNotRegistered',
      })
    }
    if (
      selectedRunner
      && selectedAggregator
      && selectedAggregator.scope !== selectedRunner.aggregator_scope
    ) {
      issues.push({
        severity: 'error',
        field: 'aggregator_name',
        message: `Aggregator scope "${selectedAggregator.scope}" does not match runner scope "${selectedRunner.aggregator_scope}"`,
        i18n_key: 'parallelEnsemble.errors.aggregatorScopeMismatch',
      })
    }
    return issues
  }, [
    aggregatorName,
    aggregators.length,
    aggregatorsQuery.isLoading,
    runnerName,
    runners.length,
    runnersQuery.isLoading,
    selectedAggregator,
    selectedRunner,
  ])

  const filterSpecVar = useCallback(
    (varPayload: Var, valueSelector: ValueSelector) => {
      // ADR-v3-16 — only an upstream ``token-model-source`` node's
      // ``outputs.spec`` field is a valid source. The variable pool
      // surfaces every node output as an ``object`` selector, so the
      // type filter alone would let the user pick e.g.
      // ``http_request.body`` and crash at run time inside
      // ``ModelInvocationSpec.model_validate``. Pinning the selector
      // tail to ``"spec"`` keeps the picker honest at edit time.
      if (!SPEC_VAR_TYPES.includes(varPayload.type))
        return false
      const last = valueSelector[valueSelector.length - 1]
      return last === 'spec'
    },
    [],
  )

  const filterNumericVar = useCallback((varPayload: Var) => {
    return NUMERIC_VAR_TYPES.includes(varPayload.type)
  }, [])

  // ── Token-source mutation handlers ──────────────────────────────

  const nextDefaultSourceId = useCallback((refs: ReadonlyArray<TokenSourceRef>) => {
    // Stable default naming: ``source_1``, ``source_2``, … — user is
    // expected to rename, but the default must never collide with an
    // existing entry because the backend rejects duplicate source_id.
    const existing = new Set(refs.map(r => r.source_id))
    let i = refs.length + 1
    while (existing.has(`source_${i}`))
      i += 1
    return `source_${i}`
  }, [])

  const handleAddTokenSource = useCallback(() => {
    const next = produce(inputs, (draft) => {
      if (!draft.ensemble)
        return
      draft.ensemble.token_sources.push({
        source_id: nextDefaultSourceId(draft.ensemble.token_sources),
        spec_selector: [],
        weight: 1,
        top_k_override: null,
        fallback_weight: null,
        extra: {},
      })
    })
    setInputs(next)
  }, [inputs, setInputs, nextDefaultSourceId])

  const handleRemoveTokenSource = useCallback((index: number) => {
    const next = produce(inputs, (draft) => {
      if (!draft.ensemble)
        return
      draft.ensemble.token_sources.splice(index, 1)
    })
    setInputs(next)
  }, [inputs, setInputs])

  const handleSourceIdChange = useCallback((index: number, value: string) => {
    const next = produce(inputs, (draft) => {
      const ref = draft.ensemble?.token_sources[index]
      if (ref)
        ref.source_id = value
    })
    setInputs(next)
  }, [inputs, setInputs])

  const handleSpecSelectorChange = useCallback(
    (index: number, selector: ValueSelector) => {
      const next = produce(inputs, (draft) => {
        const ref = draft.ensemble?.token_sources[index]
        if (ref)
          ref.spec_selector = selector
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleWeightChange = useCallback(
    (index: number, value: number | ValueSelector) => {
      const next = produce(inputs, (draft) => {
        const ref = draft.ensemble?.token_sources[index]
        if (ref)
          ref.weight = value
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleTopKOverrideChange = useCallback(
    (index: number, value: number | null) => {
      const next = produce(inputs, (draft) => {
        const ref = draft.ensemble?.token_sources[index]
        if (ref)
          ref.top_k_override = value
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleFallbackWeightChange = useCallback(
    (index: number, value: number | null) => {
      const next = produce(inputs, (draft) => {
        const ref = draft.ensemble?.token_sources[index]
        if (ref)
          ref.fallback_weight = value
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  // ── Runner / aggregator mutation handlers ───────────────────────

  const handleRunnerChange = useCallback(
    (runner: RunnerMeta) => {
      const next = produce(inputs, (draft) => {
        if (!draft.ensemble)
          return
        draft.ensemble.runner_name = runner.name
        // Reset runner_config — the previous runner's fields are
        // rejected by the new runner's ``extra="forbid"`` validator.
        // Same pattern response-aggregator's strategy switch uses.
        draft.ensemble.runner_config = {}
        // If the old aggregator's scope no longer matches the new
        // runner's scope, drop it — leaving a stale aggregator name
        // would let the user save an invalid pairing the §9 startup
        // pipeline rejects at run time. Snap to the empty string so
        // the aggregator dropdown forces a re-pick.
        const oldAgg = aggregators.find(
          a => a.name === draft.ensemble?.aggregator_name,
        )
        if (oldAgg && oldAgg.scope !== runner.aggregator_scope) {
          draft.ensemble.aggregator_name = ''
          draft.ensemble.aggregator_config = {}
        }
      })
      setInputs(next)
    },
    [aggregators, inputs, setInputs],
  )

  const handleAggregatorChange = useCallback(
    (agg: AggregatorMeta) => {
      const next = produce(inputs, (draft) => {
        if (!draft.ensemble)
          return
        draft.ensemble.aggregator_name = agg.name
        draft.ensemble.aggregator_config = {}
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleRunnerConfigChange = useCallback(
    (cfg: ConfigBlob) => {
      const next = produce(inputs, (draft) => {
        if (!draft.ensemble)
          return
        draft.ensemble.runner_config = cfg
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleAggregatorConfigChange = useCallback(
    (cfg: ConfigBlob) => {
      const next = produce(inputs, (draft) => {
        if (!draft.ensemble)
          return
        draft.ensemble.aggregator_config = cfg
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleDiagnosticsChange = useCallback(
    (patch: Partial<DiagnosticsConfig>) => {
      const next = produce(inputs, (draft) => {
        if (!draft.ensemble)
          return
        draft.ensemble.diagnostics = {
          ...DEFAULT_DIAGNOSTICS,
          ...draft.ensemble.diagnostics,
          ...patch,
        }
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  return {
    readOnly,
    inputs,
    // Registry data
    runners,
    aggregators,
    selectedRunner,
    selectedAggregator,
    isLoadingRunners: runnersQuery.isLoading,
    isLoadingAggregators: aggregatorsQuery.isLoading,
    // Filters
    filterSpecVar,
    filterNumericVar,
    // Local validation surface
    validationIssues,
    // Handlers
    handleAddTokenSource,
    handleRemoveTokenSource,
    handleSourceIdChange,
    handleSpecSelectorChange,
    handleWeightChange,
    handleTopKOverrideChange,
    handleFallbackWeightChange,
    handleRunnerChange,
    handleAggregatorChange,
    handleRunnerConfigChange,
    handleAggregatorConfigChange,
    handleDiagnosticsChange,
  }
}

export default useConfig
