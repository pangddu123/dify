'use client'
import type { FC } from 'react'
import type {
  EnsembleStrategyConfig,
  EnsembleStrategyName,
} from '../types'
import { cn } from '@langgenius/dify-ui/cn'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@langgenius/dify-ui/dropdown-menu'
import * as React from 'react'
import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Field from '@/app/components/workflow/nodes/_base/components/field'
import DynamicConfigForm from '@/app/components/workflow/nodes/parallel-ensemble/components/dynamic-config-form'
import {
  ENSEMBLE_STRATEGY_META,
  ENSEMBLE_STRATEGY_NAMES,
} from '../types'

const i18nPrefix = 'nodes.ensembleAggregator'

type Props = {
  readonly: boolean
  strategyName: EnsembleStrategyName
  strategyConfig: EnsembleStrategyConfig
  onStrategyChange: (name: EnsembleStrategyName) => void
  onStrategyConfigChange: (next: EnsembleStrategyConfig) => void
}

const StrategySelector: FC<Props> = ({
  readonly,
  strategyName,
  strategyConfig,
  onStrategyChange,
  onStrategyConfigChange,
}) => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  const handleSelect = useCallback(
    (name: EnsembleStrategyName) => {
      setOpen(false)
      // Re-selecting the active strategy would otherwise reset
      // strategy_config in the parent hook (e.g. wipe a saved separator).
      if (name !== strategyName)
        onStrategyChange(name)
    },
    [onStrategyChange, strategyName],
  )

  const meta = ENSEMBLE_STRATEGY_META[strategyName]
  // ``ui_schema`` is the per-strategy config schema mirror (P3.A.2 +
  // ADR-v3-9). When non-empty, render via the shared
  // ``DynamicConfigForm`` so the form is reflective — adding a new
  // strategy with ``ui_schema`` declarations gets a working panel
  // without touching this file. Empty schemas (e.g. ``majority_vote`` /
  // ``weighted_majority_vote``) render a hint only.
  const hasSchema = meta && Object.keys(meta.ui_schema).length > 0

  return (
    <div className="space-y-3">
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger
          disabled={readonly}
          className={cn(
            'flex w-full items-center justify-between rounded-lg bg-components-input-bg-normal px-3 py-2',
            readonly
              ? 'cursor-not-allowed bg-components-input-bg-disabled!'
              : 'cursor-pointer hover:bg-state-base-hover-alt',
            open && 'bg-state-base-hover-alt',
          )}
        >
          <span className="system-sm-regular text-components-input-text-filled">
            {t(`${i18nPrefix}.strategies.${strategyName}.label`, { ns: 'workflow' })}
          </span>
          <span
            aria-hidden
            className={cn(
              'i-ri-arrow-down-s-line h-4 w-4 text-text-quaternary',
              open && 'text-text-secondary',
            )}
          />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          placement="bottom-start"
          sideOffset={4}
          popupClassName="min-w-[240px]"
        >
          {ENSEMBLE_STRATEGY_NAMES.map(name => (
            <DropdownMenuItem
              key={name}
              className="gap-1 px-2 py-1"
              onClick={() => handleSelect(name)}
            >
              <div className="flex min-h-5 grow flex-col gap-0.5 px-1">
                <span className="system-sm-medium text-text-secondary">
                  {t(`${i18nPrefix}.strategies.${name}.label`, { ns: 'workflow' })}
                </span>
                <span className="system-xs-regular text-text-tertiary">
                  {t(`${i18nPrefix}.strategies.${name}.description`, { ns: 'workflow' })}
                </span>
              </div>
              {name === strategyName && (
                <span aria-hidden className="i-ri-check-line h-4 w-4 text-text-accent" />
              )}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      {!hasSchema && (
        <p className="system-xs-regular text-text-tertiary">
          {t(`${i18nPrefix}.strategies.${strategyName}.hint`, { ns: 'workflow' })}
        </p>
      )}

      {hasSchema && (
        <Field
          title={t(`${i18nPrefix}.strategyConfig`, { ns: 'workflow' })}
          isSubTitle
        >
          <DynamicConfigForm
            i18nKeyPrefix={meta.i18n_key_prefix}
            uiSchema={meta.ui_schema}
            value={strategyConfig}
            readonly={readonly}
            onChange={onStrategyConfigChange}
          />
        </Field>
      )}
    </div>
  )
}

export default React.memo(StrategySelector)
