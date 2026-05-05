import type { FC } from 'react'
import type { ResponseAggregatorNodeType } from './types'
import type { NodeProps } from '@/app/components/workflow/types'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

const i18nPrefix = 'nodes.responseAggregator'

const Node: FC<NodeProps<ResponseAggregatorNodeType>> = ({ data }) => {
  const { t } = useTranslation()
  const inputCount = data.inputs?.length ?? 0

  if (inputCount === 0)
    return null

  return (
    <div className="mb-1 px-3 py-1">
      <div className="flex items-center justify-between rounded-md bg-workflow-block-parma-bg px-2 py-1">
        <span className="truncate system-xs-medium text-text-secondary">
          {t(`${i18nPrefix}.strategies.${data.strategy_name}.label`, { ns: 'workflow' })}
        </span>
        <span className="system-xs-regular text-text-tertiary">
          {t(`${i18nPrefix}.inputCount`, { ns: 'workflow', count: inputCount })}
        </span>
      </div>
    </div>
  )
}

export default React.memo(Node)
