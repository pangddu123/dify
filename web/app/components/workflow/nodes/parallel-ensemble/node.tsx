import type { FC } from 'react'
import type { ParallelEnsembleNodeType } from './types'
import type { NodeProps } from '@/app/components/workflow/types'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

const i18nPrefix = 'nodes.parallelEnsemble'

const Node: FC<NodeProps<ParallelEnsembleNodeType>> = ({ data }) => {
  const { t } = useTranslation()
  const ensemble = data.ensemble
  const sourceCount = ensemble?.token_sources?.length ?? 0

  // Match ensemble-aggregator's "hide if no inputs" behaviour: a fresh
  // node on the canvas surfaces only its title + icon; once the user
  // wires upstream sources, the runner label + count chip appear so the
  // graph is glanceable without opening the panel.
  if (sourceCount === 0)
    return null

  const runnerName = ensemble?.runner_name ?? ''

  return (
    <div className="mb-1 px-3 py-1">
      <div className="flex items-center justify-between rounded-md bg-workflow-block-parma-bg px-2 py-1">
        <span className="truncate system-xs-medium text-text-secondary">
          {/* Display the runner's localized name when registered, fall
              back to the raw runner_name for unknown / third-party
              runners — same fallback pattern as
              ``RunnerSelector.renderLabel`` to keep the canvas chip
              and the panel header in sync. */}
          {t(`parallelEnsemble.runners.${runnerName}.name`, {
            ns: 'workflow',
            defaultValue: runnerName,
          })}
        </span>
        <span className="system-xs-regular text-text-tertiary">
          {t(`${i18nPrefix}.sourceCount`, {
            ns: 'workflow',
            count: sourceCount,
          })}
        </span>
      </div>
    </div>
  )
}

export default React.memo(Node)
