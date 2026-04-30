import type { FC } from 'react'
import type { TokenModelSourceNodeType } from './types'
import type { NodePanelProps } from '@/app/components/workflow/types'
import * as React from 'react'
import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import Field from '@/app/components/workflow/nodes/_base/components/field'
import OutputVars, { VarItem } from '@/app/components/workflow/nodes/_base/components/output-vars'
import Split from '@/app/components/workflow/nodes/_base/components/split'
import ModelAliasSelect from './components/model-alias-select'
import SamplingParamsForm from './components/sampling-params-form'
import useConfig from './use-config'

const i18nPrefix = 'nodes.tokenModelSource'

const Panel: FC<NodePanelProps<TokenModelSourceNodeType>> = ({
  id,
  data,
}) => {
  const { t } = useTranslation()
  const {
    readOnly,
    inputs,
    models,
    isLoadingModels,
    handleModelAliasChange,
    handlePromptTemplateChange,
    handleSamplingParamsChange,
  } = useConfig(id, data)

  return (
    <div className="pt-2">
      <div className="space-y-4 px-4 pb-2">
        {/* Section 1 — Model alias */}
        <Field
          title={t(`${i18nPrefix}.modelAlias`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.modelAliasTooltip`, { ns: 'workflow' })}
          required
        >
          <ModelAliasSelect
            readonly={readOnly}
            isLoading={isLoadingModels}
            models={models}
            selected={inputs.model_alias}
            onChange={handleModelAliasChange}
          />
        </Field>

        {/* Section 2 — Prompt template */}
        {/*
         * The prompt template is a free-form text field with
         * ``{{#node.field#}}`` placeholders. Backend
         * ``TokenModelSourceNode._render_prompt`` resolves them via
         * ``VariableTemplateParser`` so any upstream selector wired in
         * the variable pool is fair game; constant prompts (no
         * placeholders) skip the pool entirely. We deliberately don't
         * render a ``VarReferencePicker`` here — token-mode prompts
         * commonly mix prose with multiple references, which a single
         * picker can't express.
         */}
        <Field
          title={t(`${i18nPrefix}.promptTemplate`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.promptTemplateTooltip`, { ns: 'workflow' })}
        >
          <textarea
            className="block min-h-32 w-full resize-y rounded-lg bg-components-input-bg-normal px-3 py-2 system-sm-regular text-components-input-text-filled disabled:cursor-not-allowed disabled:bg-components-input-bg-disabled"
            value={inputs.prompt_template ?? ''}
            onChange={e => handlePromptTemplateChange(e.target.value)}
            rows={6}
            disabled={readOnly}
            placeholder={t(`${i18nPrefix}.promptTemplatePlaceholder`, {
              ns: 'workflow',
              defaultValue: 'Answer: {{#start.q#}}',
            })}
          />
        </Field>
      </div>

      <Split />

      {/* Section 3 — Sampling params */}
      <div className="space-y-4 px-4 pt-2 pb-2">
        <Field
          title={t(`${i18nPrefix}.sampling.title`, { ns: 'workflow' })}
          tooltip={t(`${i18nPrefix}.sampling.tooltip`, { ns: 'workflow' })}
        >
          <SamplingParamsForm
            readonly={readOnly}
            value={inputs.sampling_params}
            onChange={handleSamplingParamsChange}
          />
        </Field>
      </div>

      <Split />

      <div>
        <OutputVars>
          <>
            {/*
             * Mirrors backend ``TokenModelSourceNode._run`` outputs
             * (api/core/workflow/nodes/token_model_source/node.py):
             * ``spec`` is the ``ModelInvocationSpec`` payload the
             * downstream parallel-ensemble consumes by selector;
             * ``model_alias`` is duplicated at the top level so panels
             * / debug views can show "which model" without unpacking
             * the spec dict.
             */}
            <VarItem
              name="spec"
              type="object"
              description={t(`${i18nPrefix}.outputVars.spec`, { ns: 'workflow' })}
            />
            <VarItem
              name="model_alias"
              type="string"
              description={t(`${i18nPrefix}.outputVars.modelAlias`, { ns: 'workflow' })}
            />
          </>
        </OutputVars>
      </div>
    </div>
  )
}

export default memo(Panel)
