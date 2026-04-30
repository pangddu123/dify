'use client'
import type { FC } from 'react'
import type { TokenSourceRef } from '../types'
import type { ValueSelector, Var } from '@/app/components/workflow/types'
import { cn } from '@langgenius/dify-ui/cn'
import { Tooltip, TooltipContent, TooltipTrigger } from '@langgenius/dify-ui/tooltip'
import * as React from 'react'
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/app/components/base/input'
import AddButton from '@/app/components/workflow/nodes/_base/components/add-button'
import RemoveButton from '@/app/components/workflow/nodes/_base/components/remove-button'
import VarReferencePicker from '@/app/components/workflow/nodes/_base/components/variable/var-reference-picker'

const i18nPrefix = 'nodes.parallelEnsemble'

type Props = {
  nodeId: string
  readonly: boolean
  list: TokenSourceRef[]
  onAdd: () => void
  onRemove: (index: number) => void
  onSourceIdChange: (index: number, value: string) => void
  onSpecSelectorChange: (index: number, selector: ValueSelector) => void
  onWeightChange: (index: number, value: number | ValueSelector) => void
  onTopKOverrideChange: (index: number, value: number | null) => void
  onFallbackWeightChange: (index: number, value: number | null) => void
  filterSpecVar: (payload: Var, valueSelector: ValueSelector) => boolean
  filterNumericVar: (payload: Var, valueSelector: ValueSelector) => boolean
}

const isDynamicWeight = (w: TokenSourceRef['weight']): w is ValueSelector =>
  // ``[]`` (just-toggled, no selector chosen yet) is still dynamic mode —
  // we want to keep showing the picker rather than snap back to number.
  Array.isArray(w)

const TokenSourceList: FC<Props> = ({
  nodeId,
  readonly,
  list,
  onAdd,
  onRemove,
  onSourceIdChange,
  onSpecSelectorChange,
  onWeightChange,
  onTopKOverrideChange,
  onFallbackWeightChange,
  filterSpecVar,
  filterNumericVar,
}) => {
  const { t } = useTranslation()

  const handleSourceIdInput = useCallback(
    (index: number) => (e: React.ChangeEvent<HTMLInputElement>) => {
      onSourceIdChange(index, e.target.value)
    },
    [onSourceIdChange],
  )

  const handleSelectorChange = useCallback(
    (index: number) => (value: ValueSelector | string) => {
      // Constant strings are never a valid spec source — the upstream
      // ``token-model-source`` node always produces a selector tuple.
      if (Array.isArray(value))
        onSpecSelectorChange(index, value)
    },
    [onSpecSelectorChange],
  )

  const handleStaticWeightInput = useCallback(
    (index: number) => (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value
      if (raw === '') {
        // Empty field collapses to ``1`` (the backend default + node
        // semantic for "treat this source equally"). Emitting ``NaN``
        // would trip the frontend's finite-number guard in ``default.ts``.
        onWeightChange(index, 1)
        return
      }
      const n = Number(raw)
      if (!Number.isFinite(n))
        return
      onWeightChange(index, n)
    },
    [onWeightChange],
  )

  const handleWeightSelectorChange = useCallback(
    (index: number) => (value: ValueSelector | string) => {
      if (Array.isArray(value))
        onWeightChange(index, value)
    },
    [onWeightChange],
  )

  const handleToggleWeightMode = useCallback(
    (index: number, currentlyDynamic: boolean) => () => {
      // Switching mode resets to the new mode's neutral default: ``1``
      // for static (= unweighted), ``[]`` for dynamic (= picker not yet
      // pointed at anything). Mirrors ensemble-aggregator's input list.
      onWeightChange(index, currentlyDynamic ? 1 : [])
      if (currentlyDynamic) {
        // ``fallback_weight`` only has meaning when ``weight`` is a
        // selector that can fail to resolve at runtime; clear it on the
        // way out so a static row never carries a stale fallback.
        onFallbackWeightChange(index, null)
      }
    },
    [onWeightChange, onFallbackWeightChange],
  )

  const handleTopKOverrideInput = useCallback(
    (index: number) => (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value
      if (raw === '') {
        onTopKOverrideChange(index, null)
        return
      }
      const n = Number(raw)
      if (!Number.isFinite(n) || !Number.isInteger(n) || n <= 0)
        return
      onTopKOverrideChange(index, n)
    },
    [onTopKOverrideChange],
  )

  const handleFallbackInput = useCallback(
    (index: number) => (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value
      if (raw === '') {
        // Empty = ``null`` = fail-fast (ADR-v3-15). Operators opt into
        // graceful degrade by typing a number.
        onFallbackWeightChange(index, null)
        return
      }
      const n = Number(raw)
      if (!Number.isFinite(n))
        return
      onFallbackWeightChange(index, n)
    },
    [onFallbackWeightChange],
  )

  return (
    <div className="space-y-3">
      {list.map((item, index) => {
        const dynamic = isDynamicWeight(item.weight)
        return (
          <div
            key={index}
            className="space-y-2 rounded-lg border border-divider-subtle bg-background-section-burn px-2 py-2"
          >
            <div className="flex items-center gap-x-2">
              <Input
                className={cn('grow text-xs')}
                value={item.source_id}
                onChange={handleSourceIdInput(index)}
                placeholder={t(`${i18nPrefix}.tokenSources.sourceIdPlaceholder`, { ns: 'workflow' })!}
                disabled={readonly}
              />
              {!readonly && <RemoveButton onClick={() => onRemove(index)} />}
            </div>
            <VarReferencePicker
              nodeId={nodeId}
              readonly={readonly}
              isShowNodeName
              value={item.spec_selector}
              onChange={handleSelectorChange(index)}
              filterVar={filterSpecVar}
              isSupportFileVar={false}
            />

            {/* Weight row — two modes: static finite number or dynamic
              VariableSelector. Backend resolves both via
              ``_resolve_weight``; the toggle here surfaces which path
              the operator opted into (ADR-v3-15). */}
            <div className="flex items-center gap-x-2">
              <span className="shrink-0 system-xs-medium text-text-tertiary">
                {t(`${i18nPrefix}.tokenSources.weight`, { ns: 'workflow' })}
              </span>
              <button
                type="button"
                className={cn(
                  'shrink-0 rounded-md border border-divider-subtle px-1.5 py-0.5 system-xs-regular text-text-tertiary',
                  readonly ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-state-base-hover-alt',
                )}
                onClick={handleToggleWeightMode(index, dynamic)}
                disabled={readonly}
                aria-label={t(`${i18nPrefix}.tokenSources.weightToggleAria`, { ns: 'workflow' })}
              >
                {dynamic
                  ? t(`${i18nPrefix}.tokenSources.weightModeVariable`, { ns: 'workflow' })
                  : t(`${i18nPrefix}.tokenSources.weightModeNumber`, { ns: 'workflow' })}
              </button>
              {dynamic
                ? (
                    <div className="grow">
                      <VarReferencePicker
                        nodeId={nodeId}
                        readonly={readonly}
                        isShowNodeName
                        value={item.weight as ValueSelector}
                        onChange={handleWeightSelectorChange(index)}
                        filterVar={filterNumericVar}
                        isSupportFileVar={false}
                      />
                    </div>
                  )
                : (
                    <Input
                      className="grow text-xs"
                      type="number"
                      value={typeof item.weight === 'number' ? item.weight : 1}
                      onChange={handleStaticWeightInput(index)}
                      disabled={readonly}
                      step={0.1}
                    />
                  )}
            </div>

            {/* top_k override — PN.py joint voting needs every voter to
              surface the same top-k count per step. Empty box = inherit
              the upstream spec's ``sampling_params.top_k`` (ADR-v3-6). */}
            <div className="flex items-center gap-x-2">
              <Tooltip>
                <TooltipTrigger
                  render={(
                    <span className="shrink-0 cursor-help system-xs-medium text-text-tertiary">
                      {t(`${i18nPrefix}.tokenSources.topKOverride`, { ns: 'workflow' })}
                    </span>
                  )}
                />
                <TooltipContent>
                  {t(`${i18nPrefix}.tokenSources.topKOverrideTooltip`, { ns: 'workflow' })}
                </TooltipContent>
              </Tooltip>
              <Input
                className="grow text-xs"
                type="number"
                value={item.top_k_override ?? ''}
                onChange={handleTopKOverrideInput(index)}
                placeholder={t(`${i18nPrefix}.tokenSources.topKOverridePlaceholder`, { ns: 'workflow' })!}
                disabled={readonly}
                step={1}
                min={1}
              />
            </div>

            {/* Fallback weight only matters in dynamic mode — the field is
              ignored at runtime when weight is a static number. Hiding it
              avoids surfacing a knob that has no effect. */}
            {dynamic && (
              <div className="flex items-center gap-x-2">
                <Tooltip>
                  <TooltipTrigger
                    render={(
                      <span className="shrink-0 cursor-help system-xs-medium text-text-tertiary">
                        {t(`${i18nPrefix}.tokenSources.fallbackWeight`, { ns: 'workflow' })}
                      </span>
                    )}
                  />
                  <TooltipContent>
                    {t(`${i18nPrefix}.tokenSources.fallbackWeightTooltip`, { ns: 'workflow' })}
                  </TooltipContent>
                </Tooltip>
                <Input
                  className="grow text-xs"
                  type="number"
                  value={item.fallback_weight ?? ''}
                  onChange={handleFallbackInput(index)}
                  placeholder={t(`${i18nPrefix}.tokenSources.fallbackWeightPlaceholder`, { ns: 'workflow' })!}
                  disabled={readonly}
                  step={0.1}
                />
              </div>
            )}
          </div>
        )
      })}
      {!readonly && (
        <AddButton
          onClick={onAdd}
          text={t(`${i18nPrefix}.tokenSources.addSource`, { ns: 'workflow' })}
        />
      )}
    </div>
  )
}

export default React.memo(TokenSourceList)
