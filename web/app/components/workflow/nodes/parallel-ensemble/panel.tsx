import type { FC } from 'react'
import type { ParallelEnsembleNodeType } from './types'
import type { NodePanelProps } from '@/app/components/workflow/types'
import * as React from 'react'
import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import Field from '@/app/components/workflow/nodes/_base/components/field'
import OutputVars, { VarItem } from '@/app/components/workflow/nodes/_base/components/output-vars'
import Split from '@/app/components/workflow/nodes/_base/components/split'
import AggregatorSelector from './components/aggregator-selector'
import DiagnosticsConfigForm from './components/diagnostics-config'
import DynamicConfigForm from './components/dynamic-config-form'
import RunnerSelector from './components/runner-selector'
import TokenSourceList from './components/token-source-list'
import useConfig from './use-config'

const i18nPrefix = 'nodes.parallelEnsemble'

const Panel: FC<NodePanelProps<ParallelEnsembleNodeType>> = ({
  id,
  data,
}) => {
  const { t } = useTranslation()
  const {
    readOnly,
    inputs,
    runners,
    aggregators,
    selectedRunner,
    selectedAggregator,
    isLoadingRunners,
    isLoadingAggregators,
    filterSpecVar,
    filterNumericVar,
    validationIssues,
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
  } = useConfig(id, data)

  const ensemble = inputs.ensemble

  // Defensive guard for a DSL import that landed without ``ensemble``;
  // the panel renders nothing rather than crashing on undefined access.
  // ``checkValid`` will surface a structured error in that case.
  if (!ensemble)
    return null

  const requiredScope = selectedRunner?.aggregator_scope ?? ''

  const issuesByField = (field: string) =>
    validationIssues.filter(i => i.field === field)

  const renderIssue = (field: string) => {
    const issues = issuesByField(field)
    if (issues.length === 0)
      return null
    return (
      <ul className="mt-1 space-y-0.5">
        {issues.map(issue => (
          <li
            key={`${field}:${issue.severity}:${issue.i18n_key ?? issue.message}`}
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
        {/* Section 1 — Token sources (one row per upstream
          token-model-source, ADR-v3-16). Replaces v0.2's question +
          alias-list pair: prompt rendering and alias selection live
          upstream now, this layer just references the spec. */}
        <Field
          title={t(`${i18nPrefix}.tokenSources.title`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.tokenSources.tooltip`, { ns: 'workflow' })}
          required
        >
          <>
            <TokenSourceList
              nodeId={id}
              readonly={readOnly}
              list={ensemble.token_sources}
              onAdd={handleAddTokenSource}
              onRemove={handleRemoveTokenSource}
              onSourceIdChange={handleSourceIdChange}
              onSpecSelectorChange={handleSpecSelectorChange}
              onWeightChange={handleWeightChange}
              onTopKOverrideChange={handleTopKOverrideChange}
              onFallbackWeightChange={handleFallbackWeightChange}
              filterSpecVar={filterSpecVar}
              filterNumericVar={filterNumericVar}
            />
            {renderIssue('token_sources')}
          </>
        </Field>

        {/* Section 2 — Runner (cooperation mode) */}
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

        {/* Section 3 — Aggregator */}
        <Field
          title={t(`${i18nPrefix}.aggregator`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.aggregatorTooltip`, { ns: 'workflow' })}
          required
        >
          <>
            <AggregatorSelector
              readonly={readOnly}
              isLoading={isLoadingAggregators}
              aggregators={aggregators}
              requiredScope={requiredScope}
              selectedName={ensemble.aggregator_name}
              onChange={handleAggregatorChange}
            />
            {renderIssue('aggregator_name')}
          </>
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

      {/* Section 4 — Diagnostics */}
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
           * node.py). The ``trace`` slot is only present when
           * ``diagnostics.storage === 'inline'`` — for ``metadata``
           * storage the trace lands in ``process_data.ensemble_trace``
           * which is *not* a variable-pool selector and so must not
           * appear here.
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
