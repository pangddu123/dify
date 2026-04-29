import type { UiSchema } from '../../types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { describe, expect, it, vi } from 'vitest'
import DynamicConfigForm from '../dynamic-config-form'

// dify-ui Switch is the simplest mock — render a button so the test
// can fire ``onCheckedChange`` via a click, mirroring what users do.
type MockSwitchProps = {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
}
vi.mock('@langgenius/dify-ui/switch', () => ({
  Switch: ({ checked, onCheckedChange, disabled }: MockSwitchProps) => (
    <button
      type="button"
      data-testid="switch"
      data-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
    >
      {`switch:${checked}`}
    </button>
  ),
}))

// MultiSelectField uses Listbox + portals which don't work cleanly in
// happy-dom; replace with a plain button that flips the selection so
// the dispatch path stays observable.
type MockMultiSelectProps = {
  disabled: boolean
  items: ReadonlyArray<{ name: string, value: string }>
  onChange: (next: string[]) => void
  placeholder?: string
  selectedLabel: string
  value: string[]
}
vi.mock('@/app/components/workflow/nodes/_base/components/form-input-item.sections', () => ({
  MultiSelectField: ({ items, onChange, value, disabled, selectedLabel }: MockMultiSelectProps) => (
    <div>
      <span data-testid="multi-select-selected-label">{selectedLabel}</span>
      {items.map(item => (
        <button
          key={item.value}
          type="button"
          disabled={disabled}
          data-testid={`multi-select-item-${item.value}`}
          onClick={() => {
            const next = value.includes(item.value)
              ? value.filter(v => v !== item.value)
              : [...value, item.value]
            onChange(next)
          }}
        >
          {item.name}
        </button>
      ))}
    </div>
  ),
}))

const i18nKeyPrefix = 'parallelEnsemble.runners.tokenStep'

const fullUiSchema: UiSchema = {
  top_k: { control: 'number_input', min: 1, max: 64, step: 1 },
  custom_label: { control: 'text_input' },
  hint: { control: 'textarea' },
  enable_think: { control: 'switch' },
  voting_strategy: {
    control: 'select',
    options: [
      { value: 'majority', label: 'Majority' },
      { value: 'logit_mean', label: 'Logit mean' },
    ],
  },
  capabilities: {
    control: 'multi_select',
    options: [
      { value: 'response_level', label: 'Response level' },
      { value: 'token_step', label: 'Token step' },
    ],
  },
  judge_alias: { control: 'model_alias_select' },
}

describe('parallel-ensemble/dynamic-config-form', () => {
  describe('Empty schema', () => {
    // ``response_level`` and ``majority_vote`` ship empty ui_schema in
    // v0.2 — the form must collapse to nothing rather than render an
    // empty stub div.
    it('renders nothing when the ui_schema is empty', () => {
      const { container } = render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={{}}
          value={{}}
          readonly={false}
          onChange={vi.fn()}
        />,
      )
      expect(container.firstChild).toBeNull()
    })
  })

  describe('Whitelisted controls (7)', () => {
    // The form must render exactly one node per declared field; the
    // SPI ``UI_CONTROL_ALLOWLIST`` and frontend ``UI_CONTROLS`` ship
    // the same v0.2 set so the test exercises every entry once.
    it('renders one node per declared field for the full v0.2 control set', () => {
      const { container } = render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={fullUiSchema}
          value={{
            top_k: 8,
            custom_label: 'pickup',
            hint: 'helpful',
            enable_think: true,
            voting_strategy: 'majority',
            capabilities: ['response_level'],
          }}
          readonly={false}
          onChange={vi.fn()}
        />,
      )

      // 7 distinct field labels — each rendered as a Field title using
      // the runner-specific i18n prefix.
      const labels = container.querySelectorAll('.system-sm-semibold-uppercase')
      expect(labels.length).toBeGreaterThanOrEqual(7)

      // Number / text / textarea controls map to recognisable inputs.
      expect(container.querySelector('input[type="number"]')).not.toBeNull()
      expect(container.querySelector('textarea')).not.toBeNull()
      expect(screen.getByTestId('switch')).toBeInTheDocument()

      // Select control surfaces all options (plus the empty "—").
      const select = container.querySelector('select')
      expect(select).not.toBeNull()
      expect(select!.querySelectorAll('option')).toHaveLength(3)

      // multi_select uses the mocked field with one button per item.
      expect(screen.getByTestId('multi-select-item-response_level')).toBeInTheDocument()
      expect(screen.getByTestId('multi-select-item-token_step')).toBeInTheDocument()

      // model_alias_select renders the "not plumbed" placeholder per
      // the SPI's reserved-control guidance — never silently empty.
      expect(screen.getByText(/modelAliasSelectNotPlumbed/)).toBeInTheDocument()
    })
  })

  describe('Unknown control fallback', () => {
    // Reflective dispatch must surface a visible warning when a
    // runner ships an unrecognised control name; silently dropping
    // the field would mask a real misconfiguration.
    it('renders a role=alert warning for an unknown control name', () => {
      const schema = {
        rogue: { control: 'foo_bar' as never },
      } as unknown as UiSchema
      render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={schema}
          value={{}}
          readonly={false}
          onChange={vi.fn()}
        />,
      )

      const alert = screen.getByRole('alert')
      expect(alert).toBeInTheDocument()
      // The alert mentions both the field name and the bad control
      // string so the QA / runner author can locate it instantly.
      expect(alert.textContent).toMatch(/unknownUiControl/)
      expect(alert.textContent).toMatch(/rogue/)
      expect(alert.textContent).toMatch(/foo_bar/)
    })
  })

  describe('Dispatch — number_input', () => {
    it('coerces empty input to undefined so the backend default applies', () => {
      const onChange = vi.fn()
      render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={{ top_k: { control: 'number_input', min: 1, step: 1 } }}
          value={{ top_k: 5 }}
          readonly={false}
          onChange={onChange}
        />,
      )

      const input = document.querySelector('input[type="number"]') as HTMLInputElement
      fireEvent.change(input, { target: { value: '' } })

      // Empty string deletes the key from the merged blob — the
      // resulting object is empty rather than ``{ top_k: undefined }``.
      expect(onChange).toHaveBeenCalledWith({})
    })

    it('writes the parsed numeric value when given a valid integer', () => {
      const onChange = vi.fn()
      render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={{ top_k: { control: 'number_input' } }}
          value={{}}
          readonly={false}
          onChange={onChange}
        />,
      )

      const input = document.querySelector('input[type="number"]') as HTMLInputElement
      fireEvent.change(input, { target: { value: '12' } })

      expect(onChange).toHaveBeenCalledWith({ top_k: 12 })
    })
  })

  describe('Dispatch — switch', () => {
    it('toggles the value between true and false', () => {
      const onChange = vi.fn()
      render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={{ enable_think: { control: 'switch' } }}
          value={{ enable_think: false }}
          readonly={false}
          onChange={onChange}
        />,
      )

      fireEvent.click(screen.getByTestId('switch'))
      expect(onChange).toHaveBeenCalledWith({ enable_think: true })
    })
  })

  describe('Dispatch — select', () => {
    it('writes the selected option value', () => {
      const onChange = vi.fn()
      render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={{
            voting_strategy: {
              control: 'select',
              options: [
                { value: 'majority', label: 'Majority' },
                { value: 'logit_mean', label: 'Logit mean' },
              ],
            },
          }}
          value={{}}
          readonly={false}
          onChange={onChange}
        />,
      )

      const select = document.querySelector('select') as HTMLSelectElement
      fireEvent.change(select, { target: { value: 'logit_mean' } })

      expect(onChange).toHaveBeenCalledWith({ voting_strategy: 'logit_mean' })
    })
  })

  describe('Dispatch — multi_select', () => {
    it('adds and removes entries via the mocked control', () => {
      const onChange = vi.fn()
      render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={{
            capabilities: {
              control: 'multi_select',
              options: [
                { value: 'response_level', label: 'Response level' },
                { value: 'token_step', label: 'Token step' },
              ],
            },
          }}
          value={{ capabilities: ['response_level'] }}
          readonly={false}
          onChange={onChange}
        />,
      )

      fireEvent.click(screen.getByTestId('multi-select-item-token_step'))

      expect(onChange).toHaveBeenCalledWith({
        capabilities: ['response_level', 'token_step'],
      })
    })
  })

  describe('Dispatch — text controls', () => {
    it('treats an empty text_input as deletion', () => {
      const onChange = vi.fn()
      render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={{ custom_label: { control: 'text_input' } }}
          value={{ custom_label: 'old' }}
          readonly={false}
          onChange={onChange}
        />,
      )

      const input = document.querySelector('input[type="text"], input:not([type])') as HTMLInputElement
      fireEvent.change(input, { target: { value: '' } })

      expect(onChange).toHaveBeenCalledWith({})
    })

    it('writes textarea content into the config blob', () => {
      const onChange = vi.fn()
      render(
        <DynamicConfigForm
          i18nKeyPrefix={i18nKeyPrefix}
          uiSchema={{ hint: { control: 'textarea' } }}
          value={{}}
          readonly={false}
          onChange={onChange}
        />,
      )

      const textarea = document.querySelector('textarea') as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: 'be helpful' } })

      expect(onChange).toHaveBeenCalledWith({ hint: 'be helpful' })
    })
  })
})
