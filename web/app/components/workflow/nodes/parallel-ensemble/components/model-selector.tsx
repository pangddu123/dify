'use client'
import type { FC } from 'react'
import type { BackendInfo } from '../types'
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
import { Tooltip, TooltipContent, TooltipTrigger } from '@langgenius/dify-ui/tooltip'

type Props = {
  readonly: boolean
  isLoading?: boolean
  models: ReadonlyArray<BackendInfo>
  // Capabilities required by the currently selected runner. Models
  // missing any one of these are still listed (so the user knows they
  // exist) but greyed-out and prefixed with a tooltip explaining why
  // they can't be picked. Matches P2.11 spec verbatim.
  requiredCapabilities: ReadonlyArray<string>
  selected: ReadonlyArray<string>
  onChange: (next: string[]) => void
}

const isCompatible = (model: BackendInfo, required: ReadonlyArray<string>) => {
  if (required.length === 0)
    return true
  const have = new Set(model.capabilities)
  return required.every(cap => have.has(cap))
}

const ModelSelector: FC<Props> = ({
  readonly,
  isLoading = false,
  models,
  requiredCapabilities,
  selected,
  onChange,
}) => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  // Pre-compute compatibility flag once per render. Each row uses it
  // to decide whether the click handler / checkmark fires; selected
  // aliases that no longer satisfy the new runner stay visible (and
  // greyed) so the user can deselect them, matching backend behaviour
  // where ``validate_selection`` rejects them at run time anyway.
  const annotated = useMemo(
    () =>
      models.map(m => ({
        info: m,
        compatible: isCompatible(m, requiredCapabilities),
      })),
    [models, requiredCapabilities],
  )

  const handleToggle = useCallback(
    (alias: string, compatible: boolean) => {
      if (!compatible && !selected.includes(alias))
        return
      const next = selected.includes(alias)
        ? selected.filter(a => a !== alias)
        : [...selected, alias]
      onChange(next)
    },
    [selected, onChange],
  )

  const renderLabel = () => {
    if (isLoading)
      return t('common.loading', { ns: 'common', defaultValue: 'Loading…' })
    if (selected.length === 0) {
      return t('nodes.parallelEnsemble.modelsPlaceholder', {
        ns: 'workflow',
        defaultValue: 'Pick at least one model alias',
      })
    }
    return t('nodes.parallelEnsemble.modelsSelectedCount', {
      ns: 'workflow',
      count: selected.length,
      defaultValue: `${selected.length} selected`,
    })
  }

  const incompatTip = (model: BackendInfo) =>
    t('nodes.parallelEnsemble.errorMsg.modelMissingCapability', {
      ns: 'workflow',
      backend: model.backend,
      missing: requiredCapabilities
        .filter(c => !model.capabilities.includes(c))
        .join(', '),
      defaultValue: `Backend "${model.backend}" lacks capability for token-step`,
    })

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
        popupClassName="max-h-[320px] min-w-[320px] overflow-y-auto"
      >
        {annotated.length === 0 && (
          <div className="px-3 py-2 system-xs-regular text-text-tertiary">
            {t('nodes.parallelEnsemble.noModelsAvailable', {
              ns: 'workflow',
              defaultValue: 'No model aliases registered. Edit api/configs/model_net.yaml.',
            })}
          </div>
        )}
        {annotated.map(({ info, compatible }) => {
          const isSelected = selected.includes(info.id)
          // Click is allowed for already-selected incompatible aliases
          // (so users can deselect them) but blocked for newly clicking
          // an incompatible row — onClick still fires; ``handleToggle``
          // gates the actual mutation.
          const row = (
            <DropdownMenuItem
              key={info.id}
              className={cn(
                'gap-1 px-2 py-1',
                !compatible && !isSelected && 'cursor-not-allowed opacity-50',
              )}
              onClick={(e) => {
                e.preventDefault()
                handleToggle(info.id, compatible)
              }}
            >
              <div className="flex min-h-5 grow flex-col gap-0.5 px-1">
                <span className="system-sm-medium text-text-secondary">
                  {info.id}
                </span>
                <span className="system-xs-regular text-text-tertiary">
                  {info.backend} · {info.model_name}
                </span>
              </div>
              {isSelected && (
                <span aria-hidden className="i-ri-check-line h-4 w-4 text-text-accent" />
              )}
            </DropdownMenuItem>
          )
          if (!compatible) {
            // dify-ui Tooltip is the Radix-style triplet (Tooltip /
            // TooltipTrigger / TooltipContent). ``TooltipTrigger``
            // takes a render prop so we can wrap the existing
            // ``DropdownMenuItem`` row without injecting an extra
            // inline div in the markup tree.
            return (
              <Tooltip key={info.id}>
                <TooltipTrigger render={row} />
                <TooltipContent>{incompatTip(info)}</TooltipContent>
              </Tooltip>
            )
          }
          return row
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export default React.memo(ModelSelector)
