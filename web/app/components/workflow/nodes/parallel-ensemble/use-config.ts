import type {
  AggregatorMeta,
  BackendInfo,
  ConfigBlob,
  DiagnosticsConfig,
  ParallelEnsembleNodeType,
  RunnerMeta,
  ValidationIssue,
} from './types'
import type { ValueSelector } from '@/app/components/workflow/types'
import { produce } from 'immer'
import { useCallback, useMemo } from 'react'
import { useNodesReadOnly } from '@/app/components/workflow/hooks'
import useNodeCrud from '@/app/components/workflow/nodes/_base/hooks/use-node-crud'
import { DEFAULT_DIAGNOSTICS } from './types'
import { useAggregators, useLocalModels, useRunners } from './use-registries'

const useConfig = (id: string, payload: ParallelEnsembleNodeType) => {
  const { nodesReadOnly: readOnly } = useNodesReadOnly()
  const { inputs, setInputs } = useNodeCrud<ParallelEnsembleNodeType>(id, payload)

  const localModelsQuery = useLocalModels()
  const runnersQuery = useRunners()
  const aggregatorsQuery = useAggregators()

  // The orpc/contract ``type<T>()`` machinery widens the inner
  // ``options`` field to ``unknown`` when the runner / aggregator
  // ui_schema arrives over the wire — re-cast through the SPI types
  // so the rest of the hook sees the same shape ``RunnerMeta`` /
  // ``AggregatorMeta`` declare. The contract still pins the wire
  // schema (``contract/console/parallel-ensemble.ts``); the assertion
  // is purely a TypeScript-side narrowing.
  const models = useMemo<ReadonlyArray<BackendInfo>>(
    () => (localModelsQuery.data?.models ?? []) as ReadonlyArray<BackendInfo>,
    [localModelsQuery.data],
  )
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

  // ``useMemo`` keeps the array reference stable so the
  // ``validationIssues`` recompute doesn't churn on every render
  // (eslint ``react/exhaustive-deps``).
  const aliases = useMemo<ReadonlyArray<string>>(
    () => inputs.ensemble?.model_aliases ?? [],
    [inputs.ensemble?.model_aliases],
  )

  // ── Local capability check ──────────────────────────────────────
  //
  // P2.11 spec mentions a real-time backend ``validate_requirements``
  // round-trip; the v0.2 backend does not yet expose that endpoint —
  // ``runner_cls.validate_selection`` runs at run time inside §9. So
  // the panel mirrors what it can statically: capability subset check
  // + scope match. Any additional rule the runner declares
  // (``judge_alias must be in model_aliases``, ``enable_think + ≥1
  // type=think model``, ...) surfaces server-side at run time as a
  // structured panel error, which is the same surface this hook
  // would render.
  const validationIssues: ValidationIssue[] = useMemo(() => {
    const issues: ValidationIssue[] = []
    if (!selectedRunner)
      return issues
    const required = new Set(selectedRunner.required_capabilities)
    if (required.size > 0) {
      const offenders = aliases.filter((alias) => {
        const m = models.find(x => x.id === alias)
        if (!m)
          return false
        return !selectedRunner.required_capabilities.every(c =>
          m.capabilities.includes(c),
        )
      })
      for (const alias of offenders) {
        issues.push({
          severity: 'error',
          field: 'model_aliases',
          message: `Model "${alias}" lacks the runner's required capabilities`,
          i18n_key: 'parallelEnsemble.errors.modelMissingCapability',
        })
      }
    }
    if (
      selectedAggregator
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
  }, [aliases, models, selectedAggregator, selectedRunner])

  // ── Mutation handlers ───────────────────────────────────────────

  const handleQuestionVariableChange = useCallback(
    (selector: ValueSelector) => {
      const next = produce(inputs, (draft) => {
        if (!draft.ensemble)
          return
        draft.ensemble.question_variable = selector
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleModelAliasesChange = useCallback(
    (aliasesNext: string[]) => {
      const next = produce(inputs, (draft) => {
        if (!draft.ensemble)
          return
        // De-dupe; the backend rejects duplicates downstream but the UI
        // layer also enforces it so the dropdown's selection state
        // reflects the actual runtime list.
        draft.ensemble.model_aliases = Array.from(new Set(aliasesNext))
      })
      setInputs(next)
    },
    [inputs, setInputs],
  )

  const handleRunnerChange = useCallback(
    (runner: RunnerMeta) => {
      const next = produce(inputs, (draft) => {
        if (!draft.ensemble)
          return
        draft.ensemble.runner_name = runner.name
        // Reset runner_config — the previous runner's fields are
        // rejected by the new runner's ``extra="forbid"`` validator.
        // Same pattern ensemble-aggregator's strategy switch uses.
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
    models,
    runners,
    aggregators,
    selectedRunner,
    selectedAggregator,
    isLoadingModels: localModelsQuery.isLoading,
    isLoadingRunners: runnersQuery.isLoading,
    isLoadingAggregators: aggregatorsQuery.isLoading,
    // Local validation surface
    validationIssues,
    // Handlers
    handleQuestionVariableChange,
    handleModelAliasesChange,
    handleRunnerChange,
    handleAggregatorChange,
    handleRunnerConfigChange,
    handleAggregatorConfigChange,
    handleDiagnosticsChange,
  }
}

export default useConfig
