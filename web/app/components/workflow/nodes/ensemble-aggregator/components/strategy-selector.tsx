'use client'
import type { FC } from 'react'
import type {
  ConcatConfig,
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
import { Switch } from '@langgenius/dify-ui/switch'
import * as React from 'react'
import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/app/components/base/input'
import Field from '@/app/components/workflow/nodes/_base/components/field'
import { DEFAULT_CONCAT_SEPARATOR, ENSEMBLE_STRATEGY_NAMES } from '../types'

const i18nPrefix = 'nodes.ensembleAggregator'

type Props = {
  readonly: boolean
  strategyName: EnsembleStrategyName
  strategyConfig: EnsembleStrategyConfig
  onStrategyChange: (name: EnsembleStrategyName) => void
  onStrategyConfigChange: (patch: Partial<ConcatConfig>) => void
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

  const handleSeparatorChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value
      // Emit `undefined` on an empty field so use-config drops the key
      // entirely — the backend only applies the default separator when
      // the key is absent, not when it is set to "".
      onStrategyConfigChange({ separator: value === '' ? undefined : value })
    },
    [onStrategyConfigChange],
  )

  const handleLabelToggle = useCallback(
    (checked: boolean) => {
      onStrategyConfigChange({ include_source_label: checked })
    },
    [onStrategyConfigChange],
  )

  const concatConfig = strategyName === 'concat'
    ? (strategyConfig as ConcatConfig)
    : {}

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

      {strategyName === 'majority_vote' && (
        <p className="system-xs-regular text-text-tertiary">
          {t(`${i18nPrefix}.strategies.majority_vote.hint`, { ns: 'workflow' })}
        </p>
      )}

      {strategyName === 'concat' && (
        <div className="space-y-3">
          <Field
            title={t(`${i18nPrefix}.strategies.concat.separator`, { ns: 'workflow' })}
            tooltip={t(`${i18nPrefix}.strategies.concat.separatorTooltip`, { ns: 'workflow' })}
          >
            <Input
              value={concatConfig.separator ?? ''}
              onChange={handleSeparatorChange}
              placeholder={DEFAULT_CONCAT_SEPARATOR}
              disabled={readonly}
            />
          </Field>
          <Field
            title={t(`${i18nPrefix}.strategies.concat.includeSourceLabel`, { ns: 'workflow' })}
            tooltip={t(`${i18nPrefix}.strategies.concat.includeSourceLabelTooltip`, { ns: 'workflow' })}
            inline
            operations={(
              <Switch
                checked={concatConfig.include_source_label ?? false}
                onCheckedChange={handleLabelToggle}
                size="md"
                disabled={readonly}
              />
            )}
          />
        </div>
      )}
    </div>
  )
}

export default React.memo(StrategySelector)
