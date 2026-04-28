'use client'
import type { FC } from 'react'
import type { DiagnosticsConfig, DiagnosticsStorage } from '../types'
import { Switch } from '@langgenius/dify-ui/switch'
import * as React from 'react'
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/app/components/base/input'
import Field from '@/app/components/workflow/nodes/_base/components/field'

const i18nPrefix = 'nodes.parallelEnsemble.diagnostics'

const STORAGE_VALUES: ReadonlyArray<DiagnosticsStorage> = ['inline', 'metadata']

type Props = {
  readonly: boolean
  value: DiagnosticsConfig
  onChange: (patch: Partial<DiagnosticsConfig>) => void
}

const DiagnosticsConfigForm: FC<Props> = ({ readonly, value, onChange }) => {
  const { t } = useTranslation()

  // Each switch is a single boolean toggle; the parent hook merges
  // the patch into ``ensemble.diagnostics``. The reason a dedicated
  // form exists (instead of routing through ``DynamicConfigForm`` like
  // runner / aggregator config) is that DiagnosticsConfig is a fixed
  // node-level surface — it lives outside the runner / aggregator SPI
  // (entities.py defines it on ``ParallelEnsembleConfig``) and ships
  // i18n keys at a stable prefix the panel can hardcode.
  const handleToggle = useCallback(
    (key: keyof DiagnosticsConfig, checked: boolean) => {
      onChange({ [key]: checked } as Partial<DiagnosticsConfig>)
    },
    [onChange],
  )

  const handleMaxTraceTokens = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value
      if (raw === '') {
        // Empty input restores the SPI default (1000) — assigning
        // ``undefined`` would let the form drift to "no value" state
        // which renders as an empty <input> the next render. Snap back
        // to the SPI default so the field always shows a value.
        onChange({ max_trace_tokens: 1000 })
        return
      }
      const n = Number(raw)
      // Don't propagate sub-1 values; the SPI requires gt=0. UI clamps
      // before write — backend validation covers boundaries we miss.
      if (!Number.isFinite(n) || n <= 0)
        return
      onChange({ max_trace_tokens: Math.floor(n) })
    },
    [onChange],
  )

  const handleStorageChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const raw = e.target.value as DiagnosticsStorage
      if (!STORAGE_VALUES.includes(raw))
        return
      onChange({ storage: raw })
    },
    [onChange],
  )

  return (
    <div className="space-y-3">
      <Field
        title={t(`${i18nPrefix}.includeModelOutputs.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.includeModelOutputs.tooltip`, { ns: 'workflow' })}
        inline
        operations={(
          <Switch
            checked={value.include_model_outputs}
            onCheckedChange={c => handleToggle('include_model_outputs', c)}
            size="md"
            disabled={readonly}
          />
        )}
      />
      <Field
        title={t(`${i18nPrefix}.includeResponseTimings.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.includeResponseTimings.tooltip`, { ns: 'workflow' })}
        inline
        operations={(
          <Switch
            checked={value.include_response_timings}
            onCheckedChange={c => handleToggle('include_response_timings', c)}
            size="md"
            disabled={readonly}
          />
        )}
      />
      <Field
        title={t(`${i18nPrefix}.includeTokenCandidates.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.includeTokenCandidates.tooltip`, { ns: 'workflow' })}
        inline
        operations={(
          <Switch
            checked={value.include_token_candidates}
            onCheckedChange={c => handleToggle('include_token_candidates', c)}
            size="md"
            disabled={readonly}
          />
        )}
      />
      <Field
        title={t(`${i18nPrefix}.includeLogits.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.includeLogits.tooltip`, { ns: 'workflow' })}
        inline
        operations={(
          <Switch
            checked={value.include_logits}
            onCheckedChange={c => handleToggle('include_logits', c)}
            size="md"
            disabled={readonly}
          />
        )}
      />
      <Field
        title={t(`${i18nPrefix}.includeAggregatorReasoning.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.includeAggregatorReasoning.tooltip`, { ns: 'workflow' })}
        inline
        operations={(
          <Switch
            checked={value.include_aggregator_reasoning}
            onCheckedChange={c => handleToggle('include_aggregator_reasoning', c)}
            size="md"
            disabled={readonly}
          />
        )}
      />
      <Field
        title={t(`${i18nPrefix}.includeThinkTrace.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.includeThinkTrace.tooltip`, { ns: 'workflow' })}
        inline
        operations={(
          <Switch
            checked={value.include_think_trace}
            onCheckedChange={c => handleToggle('include_think_trace', c)}
            size="md"
            disabled={readonly}
          />
        )}
      />
      <Field
        title={t(`${i18nPrefix}.includePerBackendErrors.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.includePerBackendErrors.tooltip`, { ns: 'workflow' })}
        inline
        operations={(
          <Switch
            checked={value.include_per_backend_errors}
            onCheckedChange={c => handleToggle('include_per_backend_errors', c)}
            size="md"
            disabled={readonly}
          />
        )}
      />
      <Field
        title={t(`${i18nPrefix}.maxTraceTokens.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.maxTraceTokens.tooltip`, { ns: 'workflow' })}
      >
        <Input
          type="number"
          min={1}
          step={1}
          value={value.max_trace_tokens}
          onChange={handleMaxTraceTokens}
          disabled={readonly}
        />
      </Field>
      <Field
        title={t(`${i18nPrefix}.storage.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.storage.tooltip`, { ns: 'workflow' })}
      >
        <select
          className="block w-full rounded-lg bg-components-input-bg-normal px-3 py-2 system-sm-regular text-components-input-text-filled disabled:cursor-not-allowed disabled:bg-components-input-bg-disabled"
          value={value.storage}
          onChange={handleStorageChange}
          disabled={readonly}
        >
          {STORAGE_VALUES.map(s => (
            <option key={s} value={s}>
              {t(`${i18nPrefix}.storage.options.${s}`, { ns: 'workflow' })}
            </option>
          ))}
        </select>
      </Field>
    </div>
  )
}

export default React.memo(DiagnosticsConfigForm)
