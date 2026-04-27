'use client'
import type { FC } from 'react'
import type { AggregationInputRef } from '../types'
import type { ValueSelector, Var } from '@/app/components/workflow/types'
import { cn } from '@langgenius/dify-ui/cn'
import * as React from 'react'
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/app/components/base/input'
import AddButton from '@/app/components/workflow/nodes/_base/components/add-button'
import RemoveButton from '@/app/components/workflow/nodes/_base/components/remove-button'
import VarReferencePicker from '@/app/components/workflow/nodes/_base/components/variable/var-reference-picker'

const i18nPrefix = 'nodes.ensembleAggregator'

type Props = {
  nodeId: string
  readonly: boolean
  list: AggregationInputRef[]
  onAdd: () => void
  onRemove: (index: number) => void
  onSourceIdChange: (index: number, value: string) => void
  onVariableSelectorChange: (index: number, selector: ValueSelector) => void
  filterVar: (payload: Var, valueSelector: ValueSelector) => boolean
}

const InputList: FC<Props> = ({
  nodeId,
  readonly,
  list,
  onAdd,
  onRemove,
  onSourceIdChange,
  onVariableSelectorChange,
  filterVar,
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
      // Upstream references always resolve to a selector tuple; the string
      // branch is the constant-value path, which this node never enables.
      if (Array.isArray(value))
        onVariableSelectorChange(index, value)
    },
    [onVariableSelectorChange],
  )

  return (
    <div className="space-y-3">
      {list.map((item, index) => (
        <div
          key={index}
          className="space-y-2 rounded-lg border border-divider-subtle bg-background-section-burn px-2 py-2"
        >
          <div className="flex items-center gap-x-2">
            <Input
              className={cn('grow text-xs')}
              value={item.source_id}
              onChange={handleSourceIdInput(index)}
              placeholder={t(`${i18nPrefix}.sourceIdPlaceholder`, { ns: 'workflow' })!}
              disabled={readonly}
            />
            {!readonly && <RemoveButton onClick={() => onRemove(index)} />}
          </div>
          <VarReferencePicker
            nodeId={nodeId}
            readonly={readonly}
            isShowNodeName
            value={item.variable_selector}
            onChange={handleSelectorChange(index)}
            filterVar={filterVar}
            isSupportFileVar={false}
          />
        </div>
      ))}
      {!readonly && (
        <AddButton
          onClick={onAdd}
          text={t(`${i18nPrefix}.addInput`, { ns: 'workflow' })}
        />
      )}
    </div>
  )
}

export default React.memo(InputList)
