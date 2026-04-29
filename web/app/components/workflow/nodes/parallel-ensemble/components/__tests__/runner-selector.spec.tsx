import type { RunnerMeta } from '../../types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { describe, expect, it, vi } from 'vitest'
import RunnerSelector from '../runner-selector'

// Mirrors model-selector — happy-dom can't render dropdown content
// inside Radix portals, so the inline mock surfaces items directly.
vi.mock('@langgenius/dify-ui/dropdown-menu', async () => {
  const ReactInner = await import('react')
  type Ctx = { open: boolean, setOpen: (next: boolean) => void }
  const DropdownMenuContext = ReactInner.createContext<Ctx | null>(null)
  const useDdCtx = () => {
    const ctx = ReactInner.use(DropdownMenuContext)
    if (!ctx)
      throw new Error('DropdownMenu components must be wrapped in DropdownMenu')
    return ctx
  }
  return {
    DropdownMenu: ({ children, open, onOpenChange }: { children: React.ReactNode, open: boolean, onOpenChange?: (next: boolean) => void }) => (
      <DropdownMenuContext value={{ open, setOpen: onOpenChange ?? (() => undefined) }}>
        <div data-testid="dropdown-menu" data-open={open}>{children}</div>
      </DropdownMenuContext>
    ),
    DropdownMenuTrigger: ({ children, disabled }: { children: React.ReactNode, disabled?: boolean }) => {
      const { open, setOpen } = useDdCtx()
      return (
        <button
          type="button"
          data-testid="dropdown-trigger"
          disabled={disabled}
          onClick={() => setOpen(!open)}
        >
          {children}
        </button>
      )
    },
    DropdownMenuContent: ({ children }: { children: React.ReactNode }) => {
      const { open } = useDdCtx()
      return open ? <div data-testid="dropdown-content">{children}</div> : null
    },
    DropdownMenuItem: ({ children, onClick, className }: { children: React.ReactNode, onClick?: React.MouseEventHandler<HTMLDivElement>, className?: string }) => (
      <div role="menuitem" className={className} onClick={onClick}>{children}</div>
    ),
  }
})

const buildRunner = (overrides: Partial<RunnerMeta> = {}): RunnerMeta => ({
  name: 'response_level',
  i18n_key_prefix: 'parallelEnsemble.runners.responseLevel',
  ui_schema: {},
  config_schema: {},
  aggregator_scope: 'response',
  required_capabilities: [],
  optional_capabilities: [],
  ...overrides,
})

const buildRunners = (): RunnerMeta[] => [
  buildRunner(),
  buildRunner({
    name: 'token_step',
    i18n_key_prefix: 'parallelEnsemble.runners.tokenStep',
    aggregator_scope: 'token',
    required_capabilities: ['token_step'],
  }),
]

describe('parallel-ensemble/runner-selector', () => {
  describe('Rendering', () => {
    // Open the dropdown and verify both runners surface with their
    // i18n-key-prefixed name + description, mirroring SPI metadata.
    it('renders all runners with their i18n labels and descriptions', () => {
      render(
        <RunnerSelector
          readonly={false}
          runners={buildRunners()}
          selectedName="response_level"
          onChange={vi.fn()}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))

      // The trigger label *and* the matching dropdown row both render
      // the active runner's i18n name key — scope the assertion to the
      // dropdown content so it's clear which one we're verifying.
      const content = screen.getByTestId('dropdown-content')
      const rows = Array.from(content.querySelectorAll('[role="menuitem"]')) as HTMLElement[]
      expect(rows).toHaveLength(2)
      expect(rows[0]!.textContent).toMatch(/parallelEnsemble\.runners\.responseLevel\.name/)
      expect(rows[1]!.textContent).toMatch(/parallelEnsemble\.runners\.tokenStep\.name/)
      // Description rows live under the same prefix; both runners
      // resolve a separate `${prefix}.description` entry — assert both
      // are looked up so the descriptor isn't short-circuited.
      const descriptions = screen.getAllByText(/parallelEnsemble\.runners\..*\.description/)
      expect(descriptions).toHaveLength(2)
    })

    // The trigger label always uses the runner's own i18n prefix —
    // never a hardcoded fallback — even when the runner is loaded.
    it('uses the selected runner i18n key for the trigger label', () => {
      render(
        <RunnerSelector
          readonly={false}
          runners={buildRunners()}
          selectedName="token_step"
          onChange={vi.fn()}
        />,
      )

      const trigger = screen.getByTestId('dropdown-trigger')
      expect(trigger.textContent).toMatch(/parallelEnsemble\.runners\.tokenStep\.name/)
    })

    // While the registry is loading, the trigger label shows the
    // common loading string and the trigger is disabled to block
    // selection on stale state.
    it('shows the loading label and disables the trigger when loading', () => {
      render(
        <RunnerSelector
          readonly={false}
          isLoading
          runners={[]}
          selectedName=""
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText(/common\.loading/)).toBeInTheDocument()
      expect(screen.getByTestId('dropdown-trigger')).toBeDisabled()
    })

    // ``selectedName`` not present in the runners list (e.g. yaml was
    // edited while the panel was open) falls back to the placeholder
    // — never a stale label.
    it('falls back to the placeholder when the selected runner is missing', () => {
      render(
        <RunnerSelector
          readonly={false}
          runners={buildRunners()}
          selectedName="judge"
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText(/runnerPlaceholder/)).toBeInTheDocument()
    })
  })

  describe('Selection mutation', () => {
    it('fires onChange with the runner descriptor on click', () => {
      const onChange = vi.fn()
      render(
        <RunnerSelector
          readonly={false}
          runners={buildRunners()}
          selectedName="response_level"
          onChange={onChange}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))
      // Trigger label and dropdown rows both render the same i18n
      // key — query inside the dropdown content to disambiguate.
      const content = screen.getByTestId('dropdown-content')
      const row = (Array.from(content.querySelectorAll('[role="menuitem"]')) as HTMLElement[])
        .find(el => /tokenStep\.name/.test(el.textContent ?? ''))!
      fireEvent.click(row)

      expect(onChange).toHaveBeenCalledTimes(1)
      expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
        name: 'token_step',
        aggregator_scope: 'token',
      }))
    })

    it('does not fire onChange when re-selecting the active runner', () => {
      const onChange = vi.fn()
      render(
        <RunnerSelector
          readonly={false}
          runners={buildRunners()}
          selectedName="response_level"
          onChange={onChange}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))
      const content = screen.getByTestId('dropdown-content')
      const row = (Array.from(content.querySelectorAll('[role="menuitem"]')) as HTMLElement[])
        .find(el => /responseLevel\.name/.test(el.textContent ?? ''))!
      fireEvent.click(row)

      expect(onChange).not.toHaveBeenCalled()
    })

    it('disables the trigger when readonly is true', () => {
      render(
        <RunnerSelector
          readonly
          runners={buildRunners()}
          selectedName="response_level"
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByTestId('dropdown-trigger')).toBeDisabled()
    })
  })
})
