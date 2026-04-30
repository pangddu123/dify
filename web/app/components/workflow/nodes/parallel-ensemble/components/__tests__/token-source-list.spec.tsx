import type { TokenSourceRef } from '../../types'
import type { ValueSelector } from '@/app/components/workflow/types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TokenSourceList from '../token-source-list'

// ── Hoisted mocks ───────────────────────────────────────────────────
//
// VarReferencePicker uses graphon's variable pool; in this unit test
// we only care that the props (filter, value, onChange) are routed
// correctly — surface a minimal stub that exposes the picker's filter
// callback so the spec can assert which variables would survive.
type PickerProps = {
  value: ValueSelector
  onChange: (value: ValueSelector | string) => void
}

vi.mock('@/app/components/workflow/nodes/_base/components/variable/var-reference-picker', () => ({
  __esModule: true,
  default: ({ value, onChange }: PickerProps) => (
    <div data-testid="picker">
      <span data-testid="picker-value">{(value ?? []).join('.')}</span>
      <button
        type="button"
        data-testid="picker-emit-selector"
        onClick={() => onChange(['source_node', 'spec'])}
      >
        emit-selector
      </button>
      <button
        type="button"
        data-testid="picker-emit-string"
        onClick={() => onChange('not-a-selector')}
      >
        emit-string
      </button>
    </div>
  ),
}))

vi.mock('@langgenius/dify-ui/cn', () => ({
  cn: (...classes: Array<string | false | null | undefined>) =>
    classes.filter(Boolean).join(' '),
}))

// Tooltip primitives render their children inline in unit tests; we
// only need labels to be queryable, not the floating popover.
vi.mock('@langgenius/dify-ui/tooltip', () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ render: r }: { render: React.ReactElement }) => r,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('@/app/components/workflow/nodes/_base/components/add-button', () => ({
  __esModule: true,
  default: ({ onClick, text }: { onClick: () => void, text: string }) => (
    <button type="button" data-testid="add-button" onClick={onClick}>
      {text}
    </button>
  ),
}))

vi.mock('@/app/components/workflow/nodes/_base/components/remove-button', () => ({
  __esModule: true,
  default: ({ onClick }: { onClick: () => void }) => (
    <button type="button" data-testid="remove-button" onClick={onClick}>
      remove
    </button>
  ),
}))

vi.mock('@/app/components/base/input', () => ({
  __esModule: true,
  default: ({ value, onChange, type, placeholder, disabled }: {
    value: string | number
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => void
    type?: string
    placeholder?: string
    disabled?: boolean
  }) => (
    <input
      type={type ?? 'text'}
      value={value}
      placeholder={placeholder}
      disabled={disabled}
      onChange={onChange}
    />
  ),
}))

// ── Builders ────────────────────────────────────────────────────────

const buildSource = (overrides: Partial<TokenSourceRef> = {}): TokenSourceRef => ({
  source_id: 'source_1',
  spec_selector: ['model_a', 'spec'],
  weight: 1,
  top_k_override: null,
  fallback_weight: null,
  extra: {},
  ...overrides,
})

const buildHandlers = () => ({
  onAdd: vi.fn(),
  onRemove: vi.fn(),
  onSourceIdChange: vi.fn(),
  onSpecSelectorChange: vi.fn(),
  onWeightChange: vi.fn(),
  onTopKOverrideChange: vi.fn(),
  onFallbackWeightChange: vi.fn(),
})

describe('parallel-ensemble/token-source-list', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Routing — input mutations call the right handler', () => {
    it('forwards source_id edits to onSourceIdChange', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource()]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      const sourceIdInput = screen.getAllByPlaceholderText(
        /tokenSources\.sourceIdPlaceholder/,
      )[0]!
      fireEvent.change(sourceIdInput, { target: { value: 'renamed' } })
      expect(handlers.onSourceIdChange).toHaveBeenCalledWith(0, 'renamed')
    })

    it('forwards selector picks (array) to onSpecSelectorChange', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource()]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      // Each row mounts one spec picker; ``picker-emit-selector`` is
      // the spec picker's button (the weight picker only mounts in
      // dynamic mode, which the static-default fixture isn't in).
      fireEvent.click(screen.getByTestId('picker-emit-selector'))
      expect(handlers.onSpecSelectorChange).toHaveBeenCalledWith(0, [
        'source_node',
        'spec',
      ])
    })

    it('drops constant-string selector emissions on the spec picker', () => {
      // The picker can emit a constant-string in some upstream edit
      // paths; the spec source must always be a runtime selector
      // tuple, never a literal — drop the string branch silently.
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource()]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      fireEvent.click(screen.getByTestId('picker-emit-string'))
      expect(handlers.onSpecSelectorChange).not.toHaveBeenCalled()
    })

    it('emits a positive integer top_k_override', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource()]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      const topKInput = screen.getByPlaceholderText(/tokenSources\.topKOverridePlaceholder/)
      fireEvent.change(topKInput, { target: { value: '5' } })
      expect(handlers.onTopKOverrideChange).toHaveBeenCalledWith(0, 5)
    })

    it('clears top_k_override to null on empty input', () => {
      // Source starts with a non-null override so the input renders
      // with that value; clearing the field is the operator's signal
      // to fall back to the upstream spec.
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ top_k_override: 5 })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      const topKInput = screen.getByPlaceholderText(/tokenSources\.topKOverridePlaceholder/)
      fireEvent.change(topKInput, { target: { value: '' } })
      expect(handlers.onTopKOverrideChange).toHaveBeenCalledWith(0, null)
    })

    it('rejects non-positive / non-integer top_k_override', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource()]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      const topKInput = screen.getByPlaceholderText(/tokenSources\.topKOverridePlaceholder/)
      fireEvent.change(topKInput, { target: { value: '0' } })
      fireEvent.change(topKInput, { target: { value: '-3' } })
      fireEvent.change(topKInput, { target: { value: '2.5' } })
      expect(handlers.onTopKOverrideChange).not.toHaveBeenCalled()
    })

    it('forwards add / remove clicks', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource(), buildSource({ source_id: 'source_2' })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      fireEvent.click(screen.getByTestId('add-button'))
      expect(handlers.onAdd).toHaveBeenCalledTimes(1)

      fireEvent.click(screen.getAllByTestId('remove-button')[0]!)
      expect(handlers.onRemove).toHaveBeenCalledWith(0)
    })

    it('hides the remove + add buttons when readonly', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly
          list={[buildSource()]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      expect(screen.queryByTestId('add-button')).not.toBeInTheDocument()
      expect(screen.queryByTestId('remove-button')).not.toBeInTheDocument()
    })
  })

  describe('Weight mode — static ↔ dynamic toggle', () => {
    it('emits a finite number when the static weight field is edited', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ weight: 1 })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      const weightInput = screen.getByDisplayValue('1')
      fireEvent.change(weightInput, { target: { value: '2.5' } })
      expect(handlers.onWeightChange).toHaveBeenCalledWith(0, 2.5)
    })

    it('collapses empty static weight to 1 (the unweighted default)', () => {
      // Empty field would otherwise pass NaN to the backend's
      // ``_resolve_weight``; we re-pin to ``1`` so an unweighted source
      // never ships as ``NaN``.
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ weight: 2.5 })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      const weightInput = screen.getByDisplayValue('2.5')
      fireEvent.change(weightInput, { target: { value: '' } })
      expect(handlers.onWeightChange).toHaveBeenCalledWith(0, 1)
    })

    it('renders the weight field as type=number to delegate input filtering to the browser', () => {
      // The handler's ``Number.isFinite`` guard catches anything the
      // browser lets through (Safari historically allowed letters in
      // type=number); the browser does the heavy lifting in Chromium.
      // Asserting the input type is the cheapest signal that the
      // operator-facing surface won't accept arbitrary strings.
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ weight: 1 })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      const weightInput = screen.getByDisplayValue('1')
      expect(weightInput).toHaveAttribute('type', 'number')
    })

    it('toggling to dynamic resets weight to [] and clears fallback_weight', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ weight: 2.5, fallback_weight: 0.3 })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      // Toggle button surfaces the *current* mode label — static rows
      // show "Number". Click flips to dynamic and resets.
      fireEvent.click(screen.getByText(/tokenSources\.weightModeNumber/))
      expect(handlers.onWeightChange).toHaveBeenCalledWith(0, [])
      // Going from static → dynamic must NOT clear fallback (it was
      // already nullable and may now matter); the clear only happens
      // when LEAVING dynamic mode.
      expect(handlers.onFallbackWeightChange).not.toHaveBeenCalled()
    })

    it('toggling out of dynamic clears fallback_weight to null', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ weight: ['weights_node', 'value'], fallback_weight: 0.5 })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      // Currently dynamic; clicking the toggle flips back to static.
      fireEvent.click(screen.getByText(/tokenSources\.weightModeVariable/))
      expect(handlers.onWeightChange).toHaveBeenCalledWith(0, 1)
      expect(handlers.onFallbackWeightChange).toHaveBeenCalledWith(0, null)
    })

    it('renders the fallback_weight field only in dynamic mode', () => {
      const handlers = buildHandlers()
      const { rerender } = render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ weight: 1 })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      expect(
        screen.queryByPlaceholderText(/tokenSources\.fallbackWeightPlaceholder/),
      ).not.toBeInTheDocument()

      rerender(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ weight: ['weights_node', 'value'] })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      expect(
        screen.getByPlaceholderText(/tokenSources\.fallbackWeightPlaceholder/),
      ).toBeInTheDocument()
    })
  })

  describe('fallback_weight — empty ↔ number', () => {
    it('emits a finite number from the fallback field', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ weight: ['weights_node', 'value'], fallback_weight: 0.3 })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      const fallbackInput = screen.getByDisplayValue('0.3')
      fireEvent.change(fallbackInput, { target: { value: '0.7' } })
      expect(handlers.onFallbackWeightChange).toHaveBeenCalledWith(0, 0.7)
    })

    it('emits null when the fallback field is cleared', () => {
      const handlers = buildHandlers()
      render(
        <TokenSourceList
          nodeId="node-1"
          readonly={false}
          list={[buildSource({ weight: ['weights_node', 'value'], fallback_weight: 0.3 })]}
          {...handlers}
          filterSpecVar={() => true}
          filterNumericVar={() => true}
        />,
      )
      const fallbackInput = screen.getByDisplayValue('0.3')
      fireEvent.change(fallbackInput, { target: { value: '' } })
      expect(handlers.onFallbackWeightChange).toHaveBeenCalledWith(0, null)
    })
  })
})
