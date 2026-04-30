'use client'
import type { FC } from 'react'
import type { BackendInfo } from '../../parallel-ensemble/types'
import { cn } from '@langgenius/dify-ui/cn'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@langgenius/dify-ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@langgenius/dify-ui/tooltip'
import * as React from 'react'
import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

// ``token-model-source`` is a token-mode building block: every value
// it produces ends up inside a ``parallel-ensemble`` runner that
// requires ``token_step``. Filter the dropdown by this capability
// statically — there is no per-runner ``required_capabilities`` to
// reflect against here (the runner config lives on the downstream
// node), so the constraint is canonical, not configurable.
const REQUIRED_CAPABILITY = 'token_step'

type Props = {
  readonly: boolean
  isLoading?: boolean
  models: ReadonlyArray<BackendInfo>
  selected: string
  onChange: (next: string) => void
}

// Single-select sibling of parallel-ensemble's ``ModelSelector``. The
// node binds *one* alias per source — fan-out happens at the
// parallel-ensemble level by wiring multiple token-model-source nodes
// in. We don't reuse the multi-select component because its
// "deselect-by-toggle" semantics confuse a single-select picker (no
// way to express "must always have one selected").
const ModelAliasSelect: FC<Props> = ({
  readonly,
  isLoading = false,
  models,
  selected,
  onChange,
}) => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  // Pre-compute compatibility flag once per render. Mirrors
  // parallel-ensemble's ``ModelSelector`` policy: incompatible aliases
  // stay visible (greyed) so the user understands *why* they cannot
  // be picked, but new selection is blocked. An already-selected
  // incompatible alias remains pickable so the user can deselect it
  // (relevant if model_net.yaml drops a capability between sessions).
  const annotated = useMemo(
    () =>
      models.map(m => ({
        info: m,
        compatible: m.capabilities.includes(REQUIRED_CAPABILITY),
      })),
    [models],
  )

  const handlePick = useCallback(
    (alias: string, compatible: boolean) => {
      if (!compatible && alias !== selected)
        return
      onChange(alias)
      setOpen(false)
    },
    [onChange, selected],
  )

  const incompatTip = (info: BackendInfo) =>
    t('nodes.tokenModelSource.errorMsg.modelMissingCapability', {
      ns: 'workflow',
      backend: info.backend,
      capability: REQUIRED_CAPABILITY,
      defaultValue: `Backend "${info.backend}" lacks "${REQUIRED_CAPABILITY}" — cannot be used in token mode.`,
    })

  const renderLabel = () => {
    if (isLoading)
      return t('common.loading', { ns: 'common', defaultValue: 'Loading…' })
    if (!selected) {
      return t('nodes.tokenModelSource.modelAliasPlaceholder', {
        ns: 'workflow',
        defaultValue: 'Pick a model alias',
      })
    }
    return selected
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
        popupClassName="max-h-[320px] min-w-[320px] overflow-y-auto"
      >
        {annotated.length === 0 && (
          <div className="px-3 py-2 system-xs-regular text-text-tertiary">
            {t('nodes.tokenModelSource.noModelsAvailable', {
              ns: 'workflow',
              defaultValue: 'No model aliases registered. Edit api/configs/model_net.yaml.',
            })}
          </div>
        )}
        {annotated.map(({ info, compatible }) => {
          const isActive = info.id === selected
          const row = (
            <DropdownMenuItem
              key={info.id}
              className={cn(
                'gap-1 px-2 py-1',
                !compatible && !isActive && 'cursor-not-allowed opacity-50',
              )}
              onClick={(e) => {
                e.preventDefault()
                handlePick(info.id, compatible)
              }}
            >
              <div className="flex min-h-5 grow flex-col gap-0.5 px-1">
                <span className="system-sm-medium text-text-secondary">
                  {info.id}
                </span>
                <span className="system-xs-regular text-text-tertiary">
                  {info.backend}
                  {' · '}
                  {info.model_name}
                </span>
              </div>
              {isActive && (
                <span aria-hidden className="i-ri-check-line h-4 w-4 text-text-accent" />
              )}
            </DropdownMenuItem>
          )
          if (!compatible) {
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

export default React.memo(ModelAliasSelect)
