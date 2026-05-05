import type { AggregationInputRef } from '../../types'
import type { ValueSelector } from '@/app/components/workflow/types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import InputList from '../input-list'

// VarReferencePicker pulls in the workflow store + portals; replace
// with a thin stub that surfaces the props we care about and lets the
// test fire ``onChange`` directly to drive the dispatch path.
type PickerProps = {
  value: ValueSelector
  onChange: (value: ValueSelector) => void
  filterVar?: unknown
  readonly?: boolean
}
vi.mock('@/app/components/workflow/nodes/_base/components/variable/var-reference-picker', () => {
  let pickerInstanceCounter = 0
  // Each picker instance gets a stable id so a row's two pickers
  // (variable_selector + dynamic weight) can be told apart.
  const PickerStub: React.FC<PickerProps> = ({ value, onChange, readonly, filterVar }) => {
    const idRef = React.useRef<string | null>(null)
    if (idRef.current === null) {
      pickerInstanceCounter += 1
      idRef.current = `picker-${pickerInstanceCounter}`
    }
    const id = idRef.current
    return (
      <div
        data-testid={id}
        data-value={JSON.stringify(value)}
        data-filter={typeof filterVar === 'function' ? filterVar.name || 'fn' : 'none'}
        data-readonly={Boolean(readonly)}
      >
        <button
          type="button"
          data-testid={`${id}-pick`}
          onClick={() => onChange(['weights_node', 'value'])}
        >
          pick
        </button>
      </div>
    )
  }
  return { __esModule: true, default: PickerStub }
})

const refFactory = (overrides: Partial<AggregationInputRef> = {}): AggregationInputRef => ({
  source_id: 'gpt4',
  variable_selector: ['llm_a', 'text'],
  weight: 1,
  fallback_weight: null,
  extra: {},
  ...overrides,
})

type Handler<Args extends unknown[]> = ReturnType<typeof vi.fn<(...args: Args) => void>>
type Handlers = {
  onAdd: Handler<[]>
  onRemove: Handler<[number]>
  onSourceIdChange: Handler<[number, string]>
  onVariableSelectorChange: Handler<[number, ValueSelector]>
  onWeightChange: Handler<[number, number | ValueSelector]>
  onFallbackWeightChange: Handler<[number, number | null]>
}
const renderList = (overrides: Partial<Handlers> & {
  list?: AggregationInputRef[]
  readonly?: boolean
} = {}) => {
  const handlers: Handlers = {
    onAdd: overrides.onAdd ?? vi.fn<() => void>(),
    onRemove: overrides.onRemove ?? vi.fn<(index: number) => void>(),
    onSourceIdChange: overrides.onSourceIdChange ?? vi.fn<(index: number, value: string) => void>(),
    onVariableSelectorChange: overrides.onVariableSelectorChange ?? vi.fn<(index: number, selector: ValueSelector) => void>(),
    onWeightChange: overrides.onWeightChange ?? vi.fn<(index: number, value: number | ValueSelector) => void>(),
    onFallbackWeightChange: overrides.onFallbackWeightChange ?? vi.fn<(index: number, value: number | null) => void>(),
  }
  render(
    <InputList
      nodeId="agg_1"
      readonly={overrides.readonly ?? false}
      list={overrides.list ?? [refFactory()]}
      filterVar={() => true}
      filterNumericVar={() => true}
      {...handlers}
    />,
  )
  return handlers
}

describe('response-aggregator/input-list', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Static weight mode (default)', () => {
    // Default refs ship with ``weight: 1``; the row must show the
    // numeric input + the toggle reads the static-mode label.
    it('renders a number input and the static-mode toggle label', () => {
      renderList()

      const toggle = screen.getByRole('button', {
        name: /weightToggleAria/,
      })
      expect(toggle.textContent).toMatch(/weightModeNumber/)
      const numberInput = screen.getByDisplayValue('1') as HTMLInputElement
      expect(numberInput.type).toBe('number')
      // Fallback row is hidden in static mode (only meaningful when the
      // weight is a selector that can fail to resolve).
      expect(screen.queryByText(/fallbackWeight/)).toBeNull()
    })

    // Editing the field surfaces a finite number — the spec sticks to
    // the user's exact value rather than coercing or normalizing.
    it('emits the parsed finite number when the input changes', () => {
      const onWeightChange = vi.fn<(index: number, value: number | ValueSelector) => void>()
      renderList({ onWeightChange })

      const input = screen.getByDisplayValue('1')
      fireEvent.change(input, { target: { value: '2.5' } })

      expect(onWeightChange).toHaveBeenCalledWith(0, 2.5)
    })

    // Empty field collapses to ``1`` (treat as unweighted) so the
    // backend default + frontend finite guard stay in sync.
    it('collapses an emptied field to 1', () => {
      const onWeightChange = vi.fn<(index: number, value: number | ValueSelector) => void>()
      renderList({
        list: [refFactory({ weight: 3 })],
        onWeightChange,
      })

      const input = screen.getByDisplayValue('3')
      fireEvent.change(input, { target: { value: '' } })

      expect(onWeightChange).toHaveBeenCalledWith(0, 1)
    })
  })

  describe('Dynamic weight mode', () => {
    // ``weight: []`` represents the just-toggled state where no
    // selector has been chosen yet — keep showing the picker rather
    // than snapping back to the number input.
    it('treats an empty array weight as dynamic mode', () => {
      renderList({ list: [refFactory({ weight: [] })] })

      const toggle = screen.getByRole('button', {
        name: /weightToggleAria/,
      })
      expect(toggle.textContent).toMatch(/weightModeVariable/)
      // Two pickers expected: one for variable_selector + one for weight.
      expect(screen.getAllByTestId(/^picker-\d+$/)).toHaveLength(2)
      // Fallback row visible — only meaningful while resolving a
      // selector at runtime.
      expect(screen.getByText(/fallbackWeight$/)).toBeInTheDocument()
    })

    // Picking a selector through the picker fires ``onWeightChange``
    // with the array value (not a number).
    it('forwards the selector to onWeightChange when the dynamic picker fires', () => {
      const onWeightChange = vi.fn<(index: number, value: number | ValueSelector) => void>()
      renderList({
        list: [refFactory({ weight: [] })],
        onWeightChange,
      })

      // Two pickers: pick from the second one (weight picker).
      const pickerButtons = screen.getAllByText('pick')
      fireEvent.click(pickerButtons[1]!)

      expect(onWeightChange).toHaveBeenCalledWith(0, ['weights_node', 'value'])
    })
  })

  describe('Mode toggle', () => {
    // Switching from static to dynamic clears the number value and
    // emits ``[]`` so downstream state initializes the picker cleanly.
    it('switches to dynamic mode by emitting an empty selector', () => {
      const onWeightChange = vi.fn<(index: number, value: number | ValueSelector) => void>()
      const onFallbackWeightChange = vi.fn<(index: number, value: number | null) => void>()
      renderList({
        list: [refFactory({ weight: 0.7 })],
        onWeightChange,
        onFallbackWeightChange,
      })

      fireEvent.click(
        screen.getByRole('button', { name: /weightToggleAria/ }),
      )

      expect(onWeightChange).toHaveBeenCalledWith(0, [])
      // Static → dynamic shouldn't touch fallback.
      expect(onFallbackWeightChange).not.toHaveBeenCalled()
    })

    // Switching from dynamic back to static resets weight to 1 AND
    // clears fallback_weight (which has no meaning in static mode).
    it('switches to static mode and clears fallback_weight', () => {
      const onWeightChange = vi.fn<(index: number, value: number | ValueSelector) => void>()
      const onFallbackWeightChange = vi.fn<(index: number, value: number | null) => void>()
      renderList({
        list: [
          refFactory({
            weight: ['weights_node', 'value'],
            fallback_weight: 0.5,
          }),
        ],
        onWeightChange,
        onFallbackWeightChange,
      })

      fireEvent.click(
        screen.getByRole('button', { name: /weightToggleAria/ }),
      )

      expect(onWeightChange).toHaveBeenCalledWith(0, 1)
      expect(onFallbackWeightChange).toHaveBeenCalledWith(0, null)
    })
  })

  describe('Fallback weight input', () => {
    // Empty fallback opts back into fail-fast — backend reads ``null``
    // as "raise WeightResolutionError if the selector breaks".
    it('emits null when the fallback field is cleared', () => {
      const onFallbackWeightChange = vi.fn<(index: number, value: number | null) => void>()
      renderList({
        list: [
          refFactory({
            weight: ['weights_node', 'value'],
            fallback_weight: 0.5,
          }),
        ],
        onFallbackWeightChange,
      })

      const fallbackInput = screen.getByDisplayValue('0.5')
      fireEvent.change(fallbackInput, { target: { value: '' } })

      expect(onFallbackWeightChange).toHaveBeenCalledWith(0, null)
    })

    // Numeric fallback flows through verbatim — the field is the
    // user's escape hatch into graceful degrade (ADR-v3-15).
    it('emits the parsed finite number when a fallback is typed', () => {
      const onFallbackWeightChange = vi.fn<(index: number, value: number | null) => void>()
      renderList({
        list: [refFactory({ weight: [], fallback_weight: null })],
        onFallbackWeightChange,
      })

      const fallbackInput = screen
        .getByPlaceholderText(/fallbackWeightPlaceholder/) as HTMLInputElement
      fireEvent.change(fallbackInput, { target: { value: '0.25' } })

      expect(onFallbackWeightChange).toHaveBeenCalledWith(0, 0.25)
    })
  })

  describe('Readonly', () => {
    // Readonly disables the toggle, the source-id field, and both
    // pickers — and hides the add button entirely.
    it('disables every interaction and hides the add button when readonly', () => {
      renderList({
        list: [refFactory({ weight: [] })],
        readonly: true,
      })

      expect(
        screen.getByRole('button', { name: /weightToggleAria/ }),
      ).toBeDisabled()
      expect(screen.queryByText(/addInput/)).toBeNull()
      // VarReferencePicker stub propagates the readonly flag.
      const pickers = screen.getAllByTestId(/^picker-\d+$/)
      expect(pickers.every(p => p.dataset.readonly === 'true')).toBe(true)
    })
  })

  describe('Source list management', () => {
    // Multi-row lists keep their handlers indexed correctly, so
    // editing the second row's source_id reports index 1 — not 0.
    it('forwards the row index when editing source_id', () => {
      const onSourceIdChange = vi.fn<(index: number, value: string) => void>()
      renderList({
        list: [
          refFactory({ source_id: 'a' }),
          refFactory({ source_id: 'b' }),
        ],
        onSourceIdChange,
      })

      const inputs = screen.getAllByPlaceholderText(/sourceIdPlaceholder/)
      fireEvent.change(inputs[1]!, { target: { value: 'B' } })

      expect(onSourceIdChange).toHaveBeenCalledWith(1, 'B')
    })

    // The remove handler also receives the row index, so the panel
    // can splice the right slot from its draft.
    it('forwards the row index when remove is clicked', () => {
      const onRemove = vi.fn<(index: number) => void>()
      renderList({
        list: [refFactory({ source_id: 'a' }), refFactory({ source_id: 'b' })],
        onRemove,
      })

      // Remove buttons render an ActionButton wrapping a delete icon —
      // the destructive hover class is the most reliable marker.
      const removeButtons = screen
        .getAllByRole('button')
        .filter(b => /destructive/.test(b.className))
      expect(removeButtons.length).toBeGreaterThanOrEqual(2)
      fireEvent.click(removeButtons[0]!)

      expect(onRemove).toHaveBeenCalledWith(0)
    })

    // Add button surfaces only when not readonly and clicking it
    // fires ``onAdd`` exactly once.
    it('triggers onAdd when the add button is clicked', () => {
      const onAdd = vi.fn<() => void>()
      renderList({ onAdd })

      // The add button text uses the i18n key (mocked) — search by
      // role+name fragment instead of exact match.
      const button = screen.getByRole('button', { name: /addInput/i })
      fireEvent.click(button)

      expect(onAdd).toHaveBeenCalledTimes(1)
    })
  })
})
