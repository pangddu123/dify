import type { FC } from 'react'
import type { ParallelEnsembleNodeType } from './types'
import type { NodePanelProps, ValueSelector, Var } from '@/app/components/workflow/types'
import * as React from 'react'
import { memo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import Field from '@/app/components/workflow/nodes/_base/components/field'
import OutputVars, { VarItem } from '@/app/components/workflow/nodes/_base/components/output-vars'
import Split from '@/app/components/workflow/nodes/_base/components/split'
import VarReferencePicker from '@/app/components/workflow/nodes/_base/components/variable/var-reference-picker'
import { VarType } from '@/app/components/workflow/types'
import AggregatorSelector from './components/aggregator-selector'
import DiagnosticsConfigForm from './components/diagnostics-config'
import DynamicConfigForm from './components/dynamic-config-form'
import ImportModelInfoButton from './components/import-model-info-button'
import ModelSelector from './components/model-selector'
import RunnerSelector from './components/runner-selector'
import useConfig from './use-config'

const i18nPrefix = 'nodes.parallelEnsemble'

// The question variable selector accepts text-shaped variables. Files
// have no defined "question to ensemble" semantics so they're filtered
// out — same logic ensemble-aggregator uses for its inputs picker.
const TEXT_COMPATIBLE_VAR_TYPES: ReadonlyArray<VarType> = [
  VarType.string,
  VarType.number,
  VarType.boolean,
  VarType.object,
  VarType.any,
]

const Panel: FC<NodePanelProps<ParallelEnsembleNodeType>> = ({
  id,
  data,
}) => {
  const { t } = useTranslation()
  const {
    readOnly,
    inputs,
    models,
    runners,
    aggregators,
    selectedRunner,
    selectedAggregator,
    isLoadingModels,
    isLoadingRunners,
    isLoadingAggregators,
    validationIssues,
    handleQuestionVariableChange,
    handleModelAliasesChange,
    handleRunnerChange,
    handleAggregatorChange,
    handleRunnerConfigChange,
    handleAggregatorConfigChange,
    handleDiagnosticsChange,
  } = useConfig(id, data)

  const ensemble = inputs.ensemble

  const filterTextVar = useCallback((v: Var) => {
    return TEXT_COMPATIBLE_VAR_TYPES.includes(v.type)
  }, [])

  const handleSelectorChange = useCallback(
    (value: ValueSelector | string) => {
      // Constant strings are not a valid question source for the
      // node — the SPI requires a runtime variable so the runner can
      // re-template per backend at run time.
      if (Array.isArray(value))
        handleQuestionVariableChange(value)
    },
    [handleQuestionVariableChange],
  )

  // ``ensemble.model_aliases`` reads default to [] for the "DSL
  // landed without nested ensemble block" case — the import handler
  // hook would otherwise need to live after the early return, which
  // violates rules-of-hooks. ``checkValid`` still surfaces the missing
  // ``ensemble`` block structurally so this fallback never silently
  // produces a working node from invalid DSL.
  const existingAliases = ensemble?.model_aliases ?? []
  const handleImport = useCallback(
    (importedAliases: string[]) => {
      // Merge with existing selection rather than replacing — users
      // commonly add a few aliases manually and then bulk-import the
      // rest from a saved JSON. ``handleModelAliasesChange`` de-dupes.
      handleModelAliasesChange([...existingAliases, ...importedAliases])
    },
    [existingAliases, handleModelAliasesChange],
  )

  // Defensive guard for a DSL import that landed without ``ensemble``;
  // the panel renders nothing rather than crashing on undefined access.
  // ``checkValid`` will surface a structured error in that case.
  if (!ensemble)
    return null

  const requiredCapabilities = selectedRunner?.required_capabilities ?? []
  const requiredScope = selectedRunner?.aggregator_scope ?? ''

  const knownAliases = models.map(m => m.id)

  const issuesByField = (field: string) =>
    validationIssues.filter(i => i.field === field)

  const renderIssue = (field: string) => {
    const issues = issuesByField(field)
    if (issues.length === 0)
      return null
    return (
      <ul className="mt-1 space-y-0.5">
        {issues.map((issue, idx) => (
          <li
            key={idx}
            className={
              issue.severity === 'error'
                ? 'system-xs-regular text-text-warning-secondary'
                : 'system-xs-regular text-text-tertiary'
            }
          >
            {issue.i18n_key
              ? t(issue.i18n_key, {
                ns: 'workflow',
                defaultValue: issue.message,
              })
              : issue.message}
          </li>
        ))}
      </ul>
    )
  }

  return (
    <div className="pt-2">
      <div className="space-y-4 px-4 pb-2">
        {/* Section 1 — Question variable */}
        <Field
          title={t(`${i18nPrefix}.questionVariable`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.questionVariableTooltip`, { ns: 'workflow' })}
          required
        >
          <VarReferencePicker
            nodeId={id}
            readonly={readOnly}
            isShowNodeName
            value={ensemble.question_variable}
            onChange={handleSelectorChange}
            filterVar={filterTextVar}
            isSupportFileVar={false}
          />
        </Field>

        {/* Section 2 — Models */}
        <Field
          title={t(`${i18nPrefix}.models`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.modelsTooltip`, { ns: 'workflow' })}
          required
          operations={(
            <ImportModelInfoButton
              readonly={readOnly}
              knownAliases={knownAliases}
              onImport={handleImport}
            />
          )}
        >
          <ModelSelector
            readonly={readOnly}
            isLoading={isLoadingModels}
            models={models}
            requiredCapabilities={requiredCapabilities}
            selected={ensemble.model_aliases}
            onChange={handleModelAliasesChange}
          />
          {renderIssue('model_aliases')}
        </Field>

        {/* Section 3 — Runner (cooperation mode) */}
        <Field
          title={t(`${i18nPrefix}.runner`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.runnerTooltip`, { ns: 'workflow' })}
          required
        >
          <RunnerSelector
            readonly={readOnly}
            isLoading={isLoadingRunners}
            runners={runners}
            selectedName={ensemble.runner_name}
            onChange={handleRunnerChange}
          />
        </Field>
        {selectedRunner && Object.keys(selectedRunner.ui_schema).length > 0 && (
          <Field
            title={t(`${i18nPrefix}.runnerConfig`, { ns: 'workflow' })}
            isSubTitle
          >
            <DynamicConfigForm
              i18nKeyPrefix={selectedRunner.i18n_key_prefix}
              uiSchema={selectedRunner.ui_schema}
              value={ensemble.runner_config}
              readonly={readOnly}
              onChange={handleRunnerConfigChange}
            />
          </Field>
        )}

        {/* Section 4 — Aggregator */}
        <Field
          title={t(`${i18nPrefix}.aggregator`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.aggregatorTooltip`, { ns: 'workflow' })}
          required
        >
          <AggregatorSelector
            readonly={readOnly}
            isLoading={isLoadingAggregators}
            aggregators={aggregators}
            requiredScope={requiredScope}
            selectedName={ensemble.aggregator_name}
            onChange={handleAggregatorChange}
          />
          {renderIssue('aggregator_name')}
        </Field>
        {selectedAggregator && Object.keys(selectedAggregator.ui_schema).length > 0 && (
          <Field
            title={t(`${i18nPrefix}.aggregatorConfig`, { ns: 'workflow' })}
            isSubTitle
          >
            <DynamicConfigForm
              i18nKeyPrefix={selectedAggregator.i18n_key_prefix}
              uiSchema={selectedAggregator.ui_schema}
              value={ensemble.aggregator_config}
              readonly={readOnly}
              onChange={handleAggregatorConfigChange}
            />
          </Field>
        )}
      </div>

      <Split />

      {/* Section 5 — Diagnostics */}
      <div className="space-y-4 px-4 pt-2 pb-2">
        <Field
          title={t(`${i18nPrefix}.diagnostics.title`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.diagnostics.tooltip`, { ns: 'workflow' })}
          supportFold
        >
          <DiagnosticsConfigForm
            readonly={readOnly}
            value={ensemble.diagnostics}
            onChange={handleDiagnosticsChange}
          />
        </Field>
      </div>

      <Split />

      <div>
        <OutputVars>
          {/*
           * Mirrors what ``ParallelEnsembleNode._finalize_outputs``
           * actually emits (api/core/workflow/nodes/parallel_ensemble/
           * node.py:494). The ``trace`` slot is only present when
           * ``diagnostics.storage === 'inline'`` — for ``metadata``
           * storage the trace lands in ``process_data.ensemble_trace``
           * which is *not* a variable-pool selector and so must not
           * appear here. (See backend `_finalize_outputs` branch.)
           */}
          <>
            <VarItem
              name="text"
              type="string"
              description={t(`${i18nPrefix}.outputVars.text`, { ns: 'workflow' })}
            />
            <VarItem
              name="tokens_count"
              type="number"
              description={t(`${i18nPrefix}.outputVars.tokensCount`, { ns: 'workflow' })}
            />
            <VarItem
              name="elapsed_ms"
              type="number"
              description={t(`${i18nPrefix}.outputVars.elapsedMs`, { ns: 'workflow' })}
            />
            {ensemble.diagnostics.storage === 'inline' && (
              <VarItem
                name="trace"
                type="object"
                description={t(`${i18nPrefix}.outputVars.trace`, { ns: 'workflow' })}
              />
            )}
          </>
        </OutputVars>
      </div>
    </div>
  )
}

export default memo(Panel)
