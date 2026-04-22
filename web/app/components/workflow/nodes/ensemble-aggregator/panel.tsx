import type { FC } from 'react'
import type { EnsembleAggregatorNodeType } from './types'
import type { NodePanelProps } from '@/app/components/workflow/types'
import * as React from 'react'
import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import Field from '@/app/components/workflow/nodes/_base/components/field'
import OutputVars, { VarItem } from '@/app/components/workflow/nodes/_base/components/output-vars'
import Split from '@/app/components/workflow/nodes/_base/components/split'
import InputList from './components/input-list'
import StrategySelector from './components/strategy-selector'
import useConfig from './use-config'

const i18nPrefix = 'nodes.ensembleAggregator'

const Panel: FC<NodePanelProps<EnsembleAggregatorNodeType>> = ({
  id,
  data,
}) => {
  const { t } = useTranslation()

  const {
    readOnly,
    inputs,
    filterStringVar,
    handleAddInput,
    handleRemoveInput,
    handleSourceIdChange,
    handleVariableSelectorChange,
    handleStrategyChange,
    handleStrategyConfigChange,
  } = useConfig(id, data)

  return (
    <div className="pt-2">
      <div className="space-y-4 px-4 pb-2">
        <Field
          title={t(`${i18nPrefix}.inputs`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.inputsTooltip`, { ns: 'workflow' })}
          required
        >
          <InputList
            nodeId={id}
            readonly={readOnly}
            list={inputs.inputs}
            onAdd={handleAddInput}
            onRemove={handleRemoveInput}
            onSourceIdChange={handleSourceIdChange}
            onVariableSelectorChange={handleVariableSelectorChange}
            filterVar={filterStringVar}
          />
        </Field>

        <Field
          title={t(`${i18nPrefix}.strategy`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.strategyTooltip`, { ns: 'workflow' })}
          required
        >
          <StrategySelector
            readonly={readOnly}
            strategyName={inputs.strategy_name}
            strategyConfig={inputs.strategy_config}
            onStrategyChange={handleStrategyChange}
            onStrategyConfigChange={handleStrategyConfigChange}
          />
        </Field>
      </div>

      <Split />

      <div>
        <OutputVars>
          <>
            <VarItem
              name="text"
              type="string"
              description={t(`${i18nPrefix}.outputVars.text`, { ns: 'workflow' })}
            />
            <VarItem
              name="metadata"
              type="object"
              description={t(`${i18nPrefix}.outputVars.metadata`, { ns: 'workflow' })}
            />
          </>
        </OutputVars>
      </div>
    </div>
  )
}

export default memo(Panel)
