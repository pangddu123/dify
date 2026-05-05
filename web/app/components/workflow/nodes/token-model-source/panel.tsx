import type { FC } from 'react'
import type { TokenModelSourceNodeType } from './types'
import type { NodePanelProps, Var } from '@/app/components/workflow/types'
import * as React from 'react'
import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import Field from '@/app/components/workflow/nodes/_base/components/field'
import OutputVars, { VarItem } from '@/app/components/workflow/nodes/_base/components/output-vars'
import PromptEditor from '@/app/components/workflow/nodes/_base/components/prompt/editor'
import Split from '@/app/components/workflow/nodes/_base/components/split'
import useAvailableVarList from '@/app/components/workflow/nodes/_base/hooks/use-available-var-list'
import { VarType } from '@/app/components/workflow/types'
import ModelAliasSelect from './components/model-alias-select'
import SamplingParamsForm from './components/sampling-params-form'
import useConfig from './use-config'

const i18nPrefix = 'nodes.tokenModelSource'

// All ``VarType`` entries the backend ``VariableTemplateParser`` can
// safely stringify into a prompt. ``file`` / ``arrayFile`` are
// deliberately excluded — those are downloaded by the runtime as file
// handles, not interpolated as text. ``any`` / ``arrayAny`` are
// included because schema-loose vars (e.g. tool / plugin output of
// unknown type) can still be ``str()``-ed at render time.
const PROMPT_VAR_TYPES: ReadonlySet<VarType> = new Set([
  VarType.string,
  VarType.number,
  VarType.integer,
  VarType.secret,
  VarType.boolean,
  VarType.object,
  VarType.array,
  VarType.arrayString,
  VarType.arrayNumber,
  VarType.arrayBoolean,
  VarType.arrayObject,
  VarType.any,
  VarType.arrayAny,
])

// ``filterPromptVar`` and ``EMPTY_BLOCK_STATUS`` live at module scope
// so the props ``PromptEditor`` receives have stable identity across
// renders — avoiding the Lexical editor's relatively heavy re-mount
// path on every parent render.
const filterPromptVar = (payload: Var) => PROMPT_VAR_TYPES.has(payload.type)
const EMPTY_BLOCK_STATUS = { context: false, history: false, query: false } as const

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

  const { availableVars, availableNodesWithParent } = useAvailableVarList(id, {
    onlyLeafNodeVar: false,
    filterVar: filterPromptVar,
  })

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

        {/* Section 2 — Prompt template.
         *
         * Backend ``TokenModelSourceNode._render_prompt`` resolves
         * ``{{#node.field#}}`` placeholders via ``VariableTemplateParser``;
         * the Lexical-based ``PromptEditor`` writes the same wire format,
         * so the slash trigger / variable picker is purely a UX upgrade
         * over the prior plain ``<textarea>`` — no backend change.
         *
         * LLM-only knobs are intentionally off: token-mode prompts don't
         * speak the chat ``context`` block, jinja2 mode, or the AI
         * prompt-generator (which is wired to ``modelConfig`` from a
         * model picker that this node doesn't have).
         */}
        <PromptEditor
          instanceId={`${id}-token-model-source-prompt-editor`}
          nodeId={id}
          title={t(`${i18nPrefix}.promptTemplate`, { ns: 'workflow' })}
          titleTooltip={t(`${i18nPrefix}.promptTemplateTooltip`, { ns: 'workflow' })}
          value={inputs.prompt_template ?? ''}
          onChange={handlePromptTemplateChange}
          readOnly={readOnly}
          isShowContext={false}
          hasSetBlockStatus={EMPTY_BLOCK_STATUS}
          nodesOutputVars={availableVars}
          availableNodes={availableNodesWithParent}
          placeholder={t(`${i18nPrefix}.promptTemplatePlaceholder`, {
            ns: 'workflow',
            defaultValue: 'Answer: {{#start.q#}}',
          })}
        />
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
