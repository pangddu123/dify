'use client'
import type { FC } from 'react'
import type { AggregatorMeta } from '../types'
import { cn } from '@langgenius/dify-ui/cn'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@langgenius/dify-ui/dropdown-menu'
import * as React from 'react'
import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

type Props = {
  readonly: boolean
  isLoading?: boolean
  aggregators: ReadonlyArray<AggregatorMeta>
  // Aggregators are filtered by ``scope == runner.aggregator_scope`` —
  // the §9 startup pipeline rejects mismatched pairs server-side, so
  // the UI hides them before the user can pick. The runner-side scope
  // is provided as ``requiredScope`` instead of the runner object so
  // this component stays decoupled from RunnerMeta.
  requiredScope: string
  selectedName: string
  onChange: (next: AggregatorMeta) => void
}

const AggregatorSelector: FC<Props> = ({
  readonly,
  isLoading = false,
  aggregators,
  requiredScope,
  selectedName,
  onChange,
}) => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  const filtered = useMemo(
    () => aggregators.filter(a => a.scope === requiredScope),
    [aggregators, requiredScope],
  )

  const handleSelect = useCallback(
    (agg: AggregatorMeta) => {
      setOpen(false)
      if (agg.name !== selectedName)
        onChange(agg)
    },
    [onChange, selectedName],
  )

  const selected = filtered.find(a => a.name === selectedName)
  const renderLabel = () => {
    if (isLoading)
      return t('common.loading', { ns: 'common', defaultValue: 'Loading…' })
    if (filtered.length === 0) {
      return t('nodes.parallelEnsemble.noAggregatorForScope', {
        ns: 'workflow',
        scope: requiredScope,
        defaultValue: `No aggregator for scope=${requiredScope}`,
      })
    }
    if (!selected) {
      return t('nodes.parallelEnsemble.aggregatorPlaceholder', {
        ns: 'workflow',
        defaultValue: selectedName || '—',
      })
    }
    return t(`${selected.i18n_key_prefix}.name`, {
      ns: 'workflow',
      defaultValue: selected.name,
    })
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        disabled={readonly || isLoading || filtered.length === 0}
        className={cn(
          'flex w-full items-center justify-between rounded-lg bg-components-input-bg-normal px-3 py-2',
          (readonly || isLoading || filtered.length === 0)
            ? 'cursor-not-allowed bg-components-input-bg-disabled!'
            : 'cursor-pointer hover:bg-state-base-hover-alt',
          open && 'bg-state-base-hover-alt',
        )}
      >
        <span className="truncate system-sm-regular text-components-input-text-filled">
          {renderLabel()}
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
        popupClassName="min-w-[300px]"
      >
        {filtered.map((agg) => {
          const labelKey = `${agg.i18n_key_prefix}.name`
          const descKey = `${agg.i18n_key_prefix}.description`
          return (
            <DropdownMenuItem
              key={agg.name}
              className="gap-1 px-2 py-1"
              onClick={() => handleSelect(agg)}
            >
              <div className="flex min-h-5 grow flex-col gap-0.5 px-1">
                <span className="system-sm-medium text-text-secondary">
                  {t(labelKey, { ns: 'workflow', defaultValue: agg.name })}
                </span>
                <span className="system-xs-regular text-text-tertiary">
                  {t(descKey, { ns: 'workflow', defaultValue: '' })}
                </span>
              </div>
              {agg.name === selectedName && (
                <span aria-hidden className="i-ri-check-line h-4 w-4 text-text-accent" />
              )}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export default React.memo(AggregatorSelector)
