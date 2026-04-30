'use client'
// ── Deliberate deviation from P3.B.2 plan (ui_schema reflection) ────
//
// The TASKS.md draft mentioned "按 ui_schema 反射，复用 P2.11
// dynamic-config-form" for this form. We diverge intentionally:
// ``DynamicConfigForm`` reflects against a *runner-supplied*
// ``ui_schema`` (free-form per third-party runner). ``SamplingParams``
// is a fixed Pydantic schema (entities.py): six known fields with
// known bounds, no extension point at this layer (vLLM-private knobs
// ride on ``TokenModelSourceNodeData.extra``, validated separately).
//
// Reflecting against a static schema would mean either (a) shipping
// a hardcoded ``ui_schema`` constant alongside the form — pure
// indirection, no flexibility gain — or (b) wiring a runner registry
// fetch this node has no business making. So we hand-roll the six
// inputs with their per-field invariants (top_k / max_tokens int-only,
// top_p clamped to (0, 1], stop newline-split). Future sampling
// fields require an explicit edit here AND in entities.py — which is
// what we want for a backend-pinned contract.
import type { FC } from 'react'
import type { SamplingParams } from '../types'
import * as React from 'react'
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/app/components/base/input'
import Field from '@/app/components/workflow/nodes/_base/components/field'

const i18nPrefix = 'nodes.tokenModelSource.sampling'

type Props = {
  readonly: boolean
  value: SamplingParams
  onChange: (patch: Partial<SamplingParams>) => void
}

// ── Per-field numeric coercion ──────────────────────────────────────
//
// The form intentionally distinguishes "field is empty" from "field
// is zero": the empty case maps back to ``undefined`` in the patch so
// the backend can re-apply its default. ``NaN`` would silently be
// rejected at backend validate time — stripping it here keeps the
// panel surface honest. Each setter coerces once at the boundary so
// the rest of the component (including ``checkValid``) sees a typed
// ``SamplingParams`` slice.

const parseRequiredNumber = (raw: string): number | null => {
  if (raw === '')
    return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

const parseOptionalNumber = (raw: string): number | null | undefined => {
  if (raw === '')
    return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : undefined
}

const SamplingParamsForm: FC<Props> = ({ readonly, value, onChange }) => {
  const { t } = useTranslation()

  const handleTopK = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const n = parseRequiredNumber(e.target.value)
      // Backend ``SamplingParams.top_k`` is ``int``; reject fractional
      // input here rather than rounding silently — the user's intent
      // is ambiguous and Pydantic would 422 on round-trip.
      if (n === null || !Number.isInteger(n))
        return
      onChange({ top_k: n })
    },
    [onChange],
  )

  const handleTemperature = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const n = parseRequiredNumber(e.target.value)
      if (n === null)
        return
      onChange({ temperature: n })
    },
    [onChange],
  )

  const handleMaxTokens = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const n = parseRequiredNumber(e.target.value)
      // Same int-only contract as top_k.
      if (n === null || !Number.isInteger(n))
        return
      onChange({ max_tokens: n })
    },
    [onChange],
  )

  const handleTopP = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const n = parseOptionalNumber(e.target.value)
      if (n === undefined)
        return
      onChange({ top_p: n })
    },
    [onChange],
  )

  const handleSeed = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const raw = e.target.value
      if (raw === '') {
        onChange({ seed: null })
        return
      }
      const n = Number(raw)
      // ``seed`` is an integer-only field on the backend; reject
      // fractional input rather than rounding silently — the user's
      // intent is ambiguous and pydantic would reject the round-tripped
      // value anyway.
      if (!Number.isFinite(n) || !Number.isInteger(n))
        return
      onChange({ seed: n })
    },
    [onChange],
  )

  const handleStop = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const raw = e.target.value
      // One stop token per line. Empty lines are dropped so a trailing
      // newline doesn't add a blank entry that the backend would treat
      // as "stop on empty string" (which fires immediately).
      const list = raw
        .split('\n')
        .map(s => s.trimEnd())
        .filter(s => s.length > 0)
      onChange({ stop: list })
    },
    [onChange],
  )

  return (
    <div className="space-y-3">
      <Field
        title={t(`${i18nPrefix}.topK.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.topK.tooltip`, { ns: 'workflow' })}
      >
        <Input
          type="number"
          min={1}
          step={1}
          value={value.top_k}
          onChange={handleTopK}
          disabled={readonly}
        />
      </Field>
      <Field
        title={t(`${i18nPrefix}.temperature.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.temperature.tooltip`, { ns: 'workflow' })}
      >
        <Input
          type="number"
          min={0}
          step={0.1}
          value={value.temperature}
          onChange={handleTemperature}
          disabled={readonly}
        />
      </Field>
      <Field
        title={t(`${i18nPrefix}.maxTokens.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.maxTokens.tooltip`, { ns: 'workflow' })}
      >
        <Input
          type="number"
          min={1}
          step={1}
          value={value.max_tokens}
          onChange={handleMaxTokens}
          disabled={readonly}
        />
      </Field>
      <Field
        title={t(`${i18nPrefix}.topP.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.topP.tooltip`, { ns: 'workflow' })}
      >
        <Input
          type="number"
          min={0.001}
          max={1}
          step={0.05}
          value={value.top_p ?? ''}
          onChange={handleTopP}
          disabled={readonly}
          placeholder={t(`${i18nPrefix}.topP.placeholder`, {
            ns: 'workflow',
            defaultValue: '(disabled)',
          })}
        />
      </Field>
      <Field
        title={t(`${i18nPrefix}.seed.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.seed.tooltip`, { ns: 'workflow' })}
      >
        <Input
          type="number"
          step={1}
          value={value.seed ?? ''}
          onChange={handleSeed}
          disabled={readonly}
          placeholder={t(`${i18nPrefix}.seed.placeholder`, {
            ns: 'workflow',
            defaultValue: '(random)',
          })}
        />
      </Field>
      <Field
        title={t(`${i18nPrefix}.stop.label`, { ns: 'workflow' })}
        tooltip={t(`${i18nPrefix}.stop.tooltip`, { ns: 'workflow' })}
      >
        <textarea
          className="block w-full resize-y rounded-lg bg-components-input-bg-normal px-3 py-2 system-sm-regular text-components-input-text-filled disabled:cursor-not-allowed disabled:bg-components-input-bg-disabled"
          value={value.stop.join('\n')}
          onChange={handleStop}
          rows={2}
          disabled={readonly}
          placeholder={t(`${i18nPrefix}.stop.placeholder`, {
            ns: 'workflow',
            defaultValue: 'One stop sequence per line',
          })}
        />
      </Field>
    </div>
  )
}

export default React.memo(SamplingParamsForm)
