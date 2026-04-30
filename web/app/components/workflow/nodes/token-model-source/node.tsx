import type { FC } from 'react'
import type { TokenModelSourceNodeType } from './types'
import type { NodeProps } from '@/app/components/workflow/types'
import * as React from 'react'

const Node: FC<NodeProps<TokenModelSourceNodeType>> = ({ data }) => {
  const alias = data.model_alias

  // Match parallel-ensemble's "hide if not configured" behaviour: a
  // fresh node on the canvas surfaces only its title + icon. Once the
  // user picks a model alias the chip appears so the graph is
  // glanceable without opening the panel.
  if (!alias)
    return null

  return (
    <div className="mb-1 px-3 py-1">
      <div className="flex items-center justify-between rounded-md bg-workflow-block-parma-bg px-2 py-1">
        <span className="truncate system-xs-medium text-text-secondary">
          {alias}
        </span>
      </div>
    </div>
  )
}

export default React.memo(Node)
