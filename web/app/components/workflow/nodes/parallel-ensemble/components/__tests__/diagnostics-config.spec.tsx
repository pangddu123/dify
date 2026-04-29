import type { DiagnosticsConfig } from '../../types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { describe, expect, it, vi } from 'vitest'
import { DEFAULT_DIAGNOSTICS } from '../../types'
import DiagnosticsConfigForm from '../diagnostics-config'

type MockSwitchProps = {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
}

vi.mock('@langgenius/dify-ui/switch', () => ({
  Switch: ({ checked, onCheckedChange, disabled }: MockSwitchProps) => (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
    >
      {`switch:${checked}`}
    </button>
  ),
}))

const buildDiagnostics = (overrides: Partial<DiagnosticsConfig> = {}): DiagnosticsConfig => ({
  ...DEFAULT_DIAGNOSTICS,
  ...overrides,
})

describe('parallel-ensemble/diagnostics-config', () => {
  describe('Rendering', () => {
    // Seven switches + two non-toggle controls (max_trace_tokens
    // number input + storage select). The form is the source of truth
    // for how many surfaces DiagnosticsConfig exposes; if a knob is
    // added without bumping this count the test catches the gap.
    it('renders all 7 boolean toggles, the trace cap and the storage select', () => {
      render(
        <DiagnosticsConfigForm
          readonly={false}
          value={buildDiagnostics()}
          onChange={vi.fn()}
        />,
      )

      // 7 switches — one per ``include_*`` boolean field on
      // ``DiagnosticsConfig`` (entities.py). If a future landing adds
      // an 8th include flag, this number bumps in the same patch.
      const switches = screen.getAllByRole('switch')
      expect(switches).toHaveLength(7)

      // Trace token cap renders as a number input.
      const numberInput = document.querySelector('input[type="number"]') as HTMLInputElement
      expect(numberInput).not.toBeNull()
      expect(numberInput.value).toBe('1000')

      // Storage select offers exactly the two literal values from the
      // SPI (``inline`` / ``metadata``).
      const select = document.querySelector('select') as HTMLSelectElement
      expect(select).not.toBeNull()
      const opts = Array.from(select.querySelectorAll('option')).map(o => o.value)
      expect(opts).toEqual(['inline', 'metadata'])
      expect(select.value).toBe('metadata')
    })
  })

  describe('Storage select — P2.12 spec', () => {
    // P2.12: changing the storage radio (rendered as <select>) → state
    // change → persists to NodeData via ``onChange`` patch.
    it('emits the storage patch when the user switches from metadata to inline', () => {
      const onChange = vi.fn()
      render(
        <DiagnosticsConfigForm
          readonly={false}
          value={buildDiagnostics({ storage: 'metadata' })}
          onChange={onChange}
        />,
      )

      const select = document.querySelector('select') as HTMLSelectElement
      fireEvent.change(select, { target: { value: 'inline' } })

      expect(onChange).toHaveBeenCalledTimes(1)
      expect(onChange).toHaveBeenCalledWith({ storage: 'inline' })
    })

    it('ignores values outside the SPI literal storage allowlist', () => {
      const onChange = vi.fn()
      render(
        <DiagnosticsConfigForm
          readonly={false}
          value={buildDiagnostics()}
          onChange={onChange}
        />,
      )

      const select = document.querySelector('select') as HTMLSelectElement
      // Manually fire change with a bad payload — the form mustn't
      // surface "artifact" or any other non-allowlisted literal even
      // if a hand-edited DSL injected it.
      fireEvent.change(select, { target: { value: 'artifact' } })
      expect(onChange).not.toHaveBeenCalled()
    })
  })

  describe('Toggle handlers', () => {
    it('emits a single-key patch when toggling include_logits', () => {
      const onChange = vi.fn()
      render(
        <DiagnosticsConfigForm
          readonly={false}
          value={buildDiagnostics({ include_logits: false })}
          onChange={onChange}
        />,
      )

      // Each switch has a deterministic data-checked label (``switch:
      // <bool>``); pick the one matching the include_logits position.
      const switches = screen.getAllByRole('switch')
      // include_logits is the 4th in render order — see the form's
      // declarative listing.
      fireEvent.click(switches[3]!)
      expect(onChange).toHaveBeenCalledWith({ include_logits: true })
    })

    it('disables every switch and the inputs when readonly is true', () => {
      render(
        <DiagnosticsConfigForm
          readonly
          value={buildDiagnostics()}
          onChange={vi.fn()}
        />,
      )

      const switches = screen.getAllByRole('switch')
      for (const sw of switches) expect(sw).toBeDisabled()

      const numberInput = document.querySelector('input[type="number"]') as HTMLInputElement
      expect(numberInput).toBeDisabled()

      const select = document.querySelector('select') as HTMLSelectElement
      expect(select).toBeDisabled()
    })
  })

  describe('max_trace_tokens', () => {
    // Empty input snaps to the SPI default rather than ``undefined`` so
    // the field never enters a "no value" rendering.
    it('snaps an empty input back to the SPI default 1000', () => {
      const onChange = vi.fn()
      render(
        <DiagnosticsConfigForm
          readonly={false}
          value={buildDiagnostics({ max_trace_tokens: 50 })}
          onChange={onChange}
        />,
      )

      const numberInput = document.querySelector('input[type="number"]') as HTMLInputElement
      fireEvent.change(numberInput, { target: { value: '' } })
      expect(onChange).toHaveBeenCalledWith({ max_trace_tokens: 1000 })
    })

    it('writes a positive integer through to the patch handler', () => {
      const onChange = vi.fn()
      render(
        <DiagnosticsConfigForm
          readonly={false}
          value={buildDiagnostics()}
          onChange={onChange}
        />,
      )

      const numberInput = document.querySelector('input[type="number"]') as HTMLInputElement
      fireEvent.change(numberInput, { target: { value: '256' } })
      expect(onChange).toHaveBeenCalledWith({ max_trace_tokens: 256 })
    })

    it('rejects sub-1 values without firing onChange', () => {
      const onChange = vi.fn()
      render(
        <DiagnosticsConfigForm
          readonly={false}
          value={buildDiagnostics()}
          onChange={onChange}
        />,
      )

      const numberInput = document.querySelector('input[type="number"]') as HTMLInputElement
      // ``-3`` and ``0`` violate the SPI ``gt=0`` constraint; the form
      // must drop them rather than emit a value the backend would
      // reject downstream.
      fireEvent.change(numberInput, { target: { value: '-3' } })
      fireEvent.change(numberInput, { target: { value: '0' } })
      expect(onChange).not.toHaveBeenCalled()
    })
  })
})
