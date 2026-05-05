'use client'
import type { FC } from 'react'
import type {
  ConfigBlob,
  UiFieldSchema,
  UiSchema,
} from '../types'
import { Switch } from '@langgenius/dify-ui/switch'
import * as React from 'react'
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/app/components/base/input'
import Field from '@/app/components/workflow/nodes/_base/components/field'
import { MultiSelectField } from '@/app/components/workflow/nodes/_base/components/form-input-item.sections'
import { UI_CONTROLS } from '../types'

type Props = {
  // ``i18nKeyPrefix`` is the runner's / aggregator's
  // ``i18n_key_prefix`` ClassVar; per-field labels live at
  // ``<prefix>.fields.<fieldName>.{label,tooltip}`` (SPI runner.py
  // contract).
  i18nKeyPrefix: string
  uiSchema: UiSchema
  value: ConfigBlob
  readonly: boolean
  onChange: (patch: ConfigBlob) => void
}

// Whitelist set built from the v0.2 frozen tuple so mismatches show
// up as a TS error if someone edits the tuple but forgets the form.
const ALLOWED_CONTROLS: ReadonlySet<string> = new Set<string>(UI_CONTROLS)

const ControlField: FC<{
  fieldName: string
  field: UiFieldSchema
  i18nKeyPrefix: string
  value: unknown
  readonly: boolean
  onChange: (next: unknown) => void
}> = ({ fieldName, field, i18nKeyPrefix, value, readonly, onChange }) => {
  const { t } = useTranslation()
  const labelKey = `${i18nKeyPrefix}.fields.${fieldName}.label`
  const tooltipKey = `${i18nKeyPrefix}.fields.${fieldName}.tooltip`
  // OQ-2 fallback: when a runner / aggregator ships without complete
  // i18n coverage we render the *raw* key so QA notices the gap. Same
  // contract the SPI documents — the panel must not silently substitute
  // a generic label when the locale file is missing the entry.
  const label = t(labelKey, { ns: 'workflow', defaultValue: labelKey })
  const tooltipResolved = t(tooltipKey, { ns: 'workflow', defaultValue: '' })
  const tooltip = tooltipResolved || undefined

  // Reflective dispatch by control type. Rendering the *unknown
  // control* branch as a visible warning rather than a silent skip
  // mirrors the SPI's startup rejection — if a runner registered a
  // ``foo_bar`` control without bumping ``UI_CONTROL_ALLOWLIST``, the
  // panel exposes the bug instead of swallowing the field.
  if (!ALLOWED_CONTROLS.has(field.control)) {
    return (
      <Field title={label} tooltip={tooltip}>
        <div
          role="alert"
          className="rounded-md border border-components-panel-border bg-state-warning-hover-alt px-3 py-2 system-xs-medium text-text-warning-secondary"
        >
          {t('nodes.parallelEnsemble.errorMsg.unknownUiControl', {
            ns: 'workflow',
            field: fieldName,
            control: String(field.control),
          })}
        </div>
      </Field>
    )
  }

  switch (field.control) {
    case 'number_input': {
      // Number coerces empty / invalid input to ``undefined`` so
      // pydantic falls back to the runner's declared default. Avoid
      // emitting ``NaN`` — backend ``model_validate`` would reject it.
      const onText = (e: React.ChangeEvent<HTMLInputElement>) => {
        const raw = e.target.value
        if (raw === '') {
          onChange(undefined)
          return
        }
        const n = Number(raw)
        if (!Number.isFinite(n))
          return
        onChange(n)
      }
      return (
        <Field title={label} tooltip={tooltip}>
          <Input
            type="number"
            value={typeof value === 'number' ? value : ''}
            onChange={onText}
            min={field.min}
            max={field.max}
            step={field.step}
            disabled={readonly}
          />
        </Field>
      )
    }
    case 'text_input': {
      return (
        <Field title={label} tooltip={tooltip}>
          <Input
            value={typeof value === 'string' ? value : ''}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
              const v = e.target.value
              onChange(v === '' ? undefined : v)
            }}
            disabled={readonly}
          />
        </Field>
      )
    }
    case 'textarea': {
      return (
        <Field title={label} tooltip={tooltip}>
          <textarea
            className="block w-full resize-y rounded-lg bg-components-input-bg-normal px-3 py-2 system-sm-regular text-components-input-text-filled disabled:cursor-not-allowed disabled:bg-components-input-bg-disabled"
            value={typeof value === 'string' ? value : ''}
            onChange={(e) => {
              const v = e.target.value
              onChange(v === '' ? undefined : v)
            }}
            rows={3}
            disabled={readonly}
          />
        </Field>
      )
    }
    case 'switch': {
      return (
        <Field
          title={label}
          tooltip={tooltip}
          inline
          operations={(
            <Switch
              checked={value === true}
              onCheckedChange={(checked: boolean) => onChange(checked)}
              size="md"
              disabled={readonly}
            />
          )}
        />
      )
    }
    case 'select': {
      const options = field.options ?? []
      return (
        <Field title={label} tooltip={tooltip}>
          <select
            className="block w-full rounded-lg bg-components-input-bg-normal px-3 py-2 system-sm-regular text-components-input-text-filled disabled:cursor-not-allowed disabled:bg-components-input-bg-disabled"
            value={value === undefined || value === null ? '' : String(value)}
            onChange={(e) => {
              const v = e.target.value
              onChange(v === '' ? undefined : v)
            }}
            disabled={readonly}
          >
            <option value="">—</option>
            {options.map(opt => (
              <option key={String(opt.value)} value={String(opt.value)}>
                {opt.label ?? String(opt.value)}
              </option>
            ))}
          </select>
        </Field>
      )
    }
    case 'multi_select': {
      const options = field.options ?? []
      const items = options.map(opt => ({
        name: opt.label ?? String(opt.value),
        value: String(opt.value),
      }))
      const valueArr = Array.isArray(value) ? value.map(v => String(v)) : []
      const selectedLabel = items
        .filter(item => valueArr.includes(item.value))
        .map(item => item.name)
        .join(', ')
      return (
        <Field title={label} tooltip={tooltip}>
          <MultiSelectField
            disabled={readonly}
            items={items}
            onChange={next => onChange(next.length === 0 ? undefined : next)}
            placeholder={label}
            selectedLabel={selectedLabel}
            value={valueArr}
          />
        </Field>
      )
    }
    case 'model_alias_select': {
      // model_alias_select is supplied by the model dropdown that the
      // panel already renders (top of page). Embedding a duplicate
      // picker in a runner / aggregator dynamic form would let two
      // controls write to two different keys — confusing and
      // error-prone. The SPI reserves the control name for runners
      // that legitimately need a *secondary* alias (e.g. judge runner
      // with a separate ``judge_alias`` field); v0.2 has no such
      // runner shipped, so we render a placeholder informing extension
      // authors the wiring is not yet plumbed.
      return (
        <Field title={label} tooltip={tooltip}>
          <div className="rounded-md border border-components-panel-border bg-components-panel-bg-alt px-3 py-2 system-xs-regular text-text-tertiary">
            {t('nodes.parallelEnsemble.errorMsg.modelAliasSelectNotPlumbed', {
              ns: 'workflow',
            })}
          </div>
        </Field>
      )
    }
  }
}

const DynamicConfigForm: FC<Props> = ({
  i18nKeyPrefix,
  uiSchema,
  value,
  readonly,
  onChange,
}) => {
  const handlePatchKey = useCallback(
    (key: string, next: unknown) => {
      // Match response-aggregator's patch semantics: ``undefined``
      // deletes the key so the backend default applies — assigning
      // ``undefined`` to a property would serialize as ``null`` /
      // explicit absence depending on the encoder.
      const merged: ConfigBlob = { ...value }
      if (next === undefined)
        delete merged[key]
      else
        merged[key] = next
      onChange(merged)
    },
    [value, onChange],
  )

  // Empty schema is legitimate (response_level / majority_vote both
  // declare ``ui_schema: {}``). Render nothing rather than an empty
  // ``<div>`` — keeps the panel compact and avoids a stray bottom
  // margin on the wrapping ``Field``.
  const fieldNames = Object.keys(uiSchema)
  if (fieldNames.length === 0)
    return null

  return (
    <div className="space-y-3">
      {fieldNames.map((name) => {
        const field = uiSchema[name]
        if (!field)
          return null
        return (
          <ControlField
            key={name}
            fieldName={name}
            field={field}
            i18nKeyPrefix={i18nKeyPrefix}
            value={value[name]}
            readonly={readonly}
            onChange={next => handlePatchKey(name, next)}
          />
        )
      })}
    </div>
  )
}

export default React.memo(DynamicConfigForm)
