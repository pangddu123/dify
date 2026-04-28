'use client'
import type { FC } from 'react'
import type { RunnerMeta } from '../types'
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

type Props = {
  readonly: boolean
  isLoading?: boolean
  runners: ReadonlyArray<RunnerMeta>
  selectedName: string
  onChange: (next: RunnerMeta) => void
}

const RunnerSelector: FC<Props> = ({
  readonly,
  isLoading = false,
  runners,
  selectedName,
  onChange,
}) => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  const handleSelect = useCallback(
    (runner: RunnerMeta) => {
      setOpen(false)
      // Re-selecting the active runner is a no-op so the parent hook's
      // "wipe runner_config / aggregator pairing" reset doesn't fire on
      // every re-render of the dropdown. Same shape as
      // ensemble-aggregator's strategy-selector.
      if (runner.name !== selectedName)
        onChange(runner)
    },
    [onChange, selectedName],
  )

  const selected = runners.find(r => r.name === selectedName)
  const renderLabel = () => {
    if (isLoading)
      return t('common.loading', { ns: 'common', defaultValue: 'Loading…' })
    if (!selected) {
      return t('nodes.parallelEnsemble.runnerPlaceholder', {
        ns: 'workflow',
        defaultValue: selectedName || '—',
      })
    }
    // The runner's own ``i18n_key_prefix`` owns its display label —
    // matches the SPI contract documented in spi/runner.py.
    return t(`${selected.i18n_key_prefix}.name`, {
      ns: 'workflow',
      defaultValue: selected.name,
    })
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        disabled={readonly || isLoading}
        className={cn(
          'flex w-full items-center justify-between rounded-lg bg-components-input-bg-normal px-3 py-2',
          (readonly || isLoading)
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
        {runners.map((runner) => {
          const labelKey = `${runner.i18n_key_prefix}.name`
          const descKey = `${runner.i18n_key_prefix}.description`
          return (
            <DropdownMenuItem
              key={runner.name}
              className="gap-1 px-2 py-1"
              onClick={() => handleSelect(runner)}
            >
              <div className="flex min-h-5 grow flex-col gap-0.5 px-1">
                <span className="system-sm-medium text-text-secondary">
                  {t(labelKey, { ns: 'workflow', defaultValue: runner.name })}
                </span>
                <span className="system-xs-regular text-text-tertiary">
                  {t(descKey, { ns: 'workflow', defaultValue: '' })}
                </span>
              </div>
              {runner.name === selectedName && (
                <span aria-hidden className="i-ri-check-line h-4 w-4 text-text-accent" />
              )}
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export default React.memo(RunnerSelector)
