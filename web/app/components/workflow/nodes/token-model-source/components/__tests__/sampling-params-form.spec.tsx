import type { SamplingParams } from '../../types'
import { fireEvent, render } from '@testing-library/react'
import * as React from 'react'
import { describe, expect, it, vi } from 'vitest'
import { DEFAULT_SAMPLING_PARAMS } from '../../types'
import SamplingParamsForm from '../sampling-params-form'

const baseValue = (overrides: Partial<SamplingParams> = {}): SamplingParams => ({
  ...DEFAULT_SAMPLING_PARAMS,
  ...overrides,
})

type RenderOverrides = {
  readonly?: boolean
  value?: SamplingParams
  onChange?: (patch: Partial<SamplingParams>) => void
}

const renderForm = (overrides: RenderOverrides = {}) => {
  const onChange = overrides.onChange ?? vi.fn()
  const result = render(
    <SamplingParamsForm
      readonly={overrides.readonly ?? false}
      value={overrides.value ?? baseValue()}
      onChange={onChange}
    />,
  )
  return { ...result, onChange }
}

describe('token-model-source/sampling-params-form', () => {
  describe('Rendering', () => {
    it('renders 5 number inputs + 1 stop textarea (six fields total)', () => {
      // Five numeric inputs: top_k / temperature / max_tokens / top_p
      // / seed. Stop list is a textarea (one stop sequence per line).
      const { container } = renderForm()
      const numbers = container.querySelectorAll('input[type="number"]')
      const textareas = container.querySelectorAll('textarea')
      expect(numbers).toHaveLength(5)
      expect(textareas).toHaveLength(1)
    })

    it('renders default values from the canonical sampling defaults', () => {
      const { container } = renderForm()
      const numbers = container.querySelectorAll('input[type="number"]')
      // Order in the markup matches the form's declaration: top_k,
      // temperature, max_tokens, top_p, seed.
      expect((numbers[0] as HTMLInputElement).value).toBe('10')
      expect((numbers[1] as HTMLInputElement).value).toBe('0.7')
      expect((numbers[2] as HTMLInputElement).value).toBe('1024')
      // top_p: null → empty string (placeholder reads "(disabled)")
      expect((numbers[3] as HTMLInputElement).value).toBe('')
      // seed: null → empty string
      expect((numbers[4] as HTMLInputElement).value).toBe('')
    })

    it('disables every field when readonly=true', () => {
      const { container } = renderForm({ readonly: true })
      const numbers = container.querySelectorAll('input[type="number"]')
      const textareas = container.querySelectorAll('textarea')
      numbers.forEach(n => expect((n as HTMLInputElement).disabled).toBe(true))
      textareas.forEach(t => expect((t as HTMLTextAreaElement).disabled).toBe(true))
    })

    it('renders existing stop list as one entry per line', () => {
      const { container } = renderForm({
        value: baseValue({ stop: ['\n\n', '</s>'] }),
      })
      const textarea = container.querySelector('textarea') as HTMLTextAreaElement
      expect(textarea.value).toBe('\n\n\n</s>')
    })
  })

  describe('Boundary coercion', () => {
    it('emits top_k as a number when the input changes', () => {
      const { container, onChange } = renderForm()
      const topK = container.querySelectorAll('input[type="number"]')[0] as HTMLInputElement
      fireEvent.change(topK, { target: { value: '12' } })
      expect(onChange).toHaveBeenCalledWith({ top_k: 12 })
    })

    it('does not emit when top_k is cleared (required field, NaN-guard)', () => {
      // Empty string for a required field is *unchanged* — the form
      // doesn't emit ``undefined`` because the backend has no default
      // to fall back to (the user must fix it before save).
      const { container, onChange } = renderForm()
      const topK = container.querySelectorAll('input[type="number"]')[0] as HTMLInputElement
      fireEvent.change(topK, { target: { value: '' } })
      expect(onChange).not.toHaveBeenCalled()
    })

    it('rejects fractional top_k (backend int-only contract)', () => {
      const { container, onChange } = renderForm()
      const topK = container.querySelectorAll('input[type="number"]')[0] as HTMLInputElement
      fireEvent.change(topK, { target: { value: '1.5' } })
      expect(onChange).not.toHaveBeenCalled()
    })

    it('rejects fractional max_tokens (backend int-only contract)', () => {
      const { container, onChange } = renderForm()
      const maxTokens = container.querySelectorAll('input[type="number"]')[2] as HTMLInputElement
      fireEvent.change(maxTokens, { target: { value: '64.5' } })
      expect(onChange).not.toHaveBeenCalled()
    })

    it('accepts temperature = 0 (greedy decoding)', () => {
      const { container, onChange } = renderForm()
      const temp = container.querySelectorAll('input[type="number"]')[1] as HTMLInputElement
      fireEvent.change(temp, { target: { value: '0' } })
      expect(onChange).toHaveBeenCalledWith({ temperature: 0 })
    })

    it('emits null when top_p is cleared (disable nucleus sampling)', () => {
      const { container, onChange } = renderForm({
        value: baseValue({ top_p: 0.9 }),
      })
      const topP = container.querySelectorAll('input[type="number"]')[3] as HTMLInputElement
      fireEvent.change(topP, { target: { value: '' } })
      expect(onChange).toHaveBeenCalledWith({ top_p: null })
    })

    it('rejects fractional seed (no onChange call)', () => {
      const { container, onChange } = renderForm()
      const seed = container.querySelectorAll('input[type="number"]')[4] as HTMLInputElement
      fireEvent.change(seed, { target: { value: '1.5' } })
      expect(onChange).not.toHaveBeenCalled()
    })

    it('emits null when seed is cleared', () => {
      const { container, onChange } = renderForm({
        value: baseValue({ seed: 42 }),
      })
      const seed = container.querySelectorAll('input[type="number"]')[4] as HTMLInputElement
      fireEvent.change(seed, { target: { value: '' } })
      expect(onChange).toHaveBeenCalledWith({ seed: null })
    })

    it('parses the stop textarea into a list, dropping blank lines', () => {
      // A trailing newline must NOT add an empty stop entry —
      // ``stop: [""]`` would terminate generation immediately on
      // every backend.
      const { container, onChange } = renderForm()
      const textarea = container.querySelector('textarea') as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: 'first\n\nsecond\n' } })
      expect(onChange).toHaveBeenCalledWith({ stop: ['first', 'second'] })
    })

    it('emits an empty stop list when the textarea is cleared', () => {
      const { container, onChange } = renderForm({
        value: baseValue({ stop: ['\n'] }),
      })
      const textarea = container.querySelector('textarea') as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: '' } })
      expect(onChange).toHaveBeenCalledWith({ stop: [] })
    })
  })
})
