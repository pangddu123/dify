import type { BackendInfo } from '../../types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { describe, expect, it, vi } from 'vitest'
import ModelSelector from '../model-selector'

// dify-ui primitives that rely on Radix portals don't render their
// content in happy-dom without a portal mount. Replace them with
// inline equivalents so the dropdown items are visible to RTL queries.
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
      // The real DropdownMenuItem swallows clicks via Radix; rendering
      // a plain div with onClick keeps fireEvent.click semantics.
      <div role="menuitem" className={className} onClick={onClick}>{children}</div>
    ),
  }
})

vi.mock('@langgenius/dify-ui/tooltip', () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ render }: { render: React.ReactElement }) => <>{render}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => (
    <div role="tooltip">{children}</div>
  ),
}))

const buildModel = (overrides: Partial<BackendInfo> = {}): BackendInfo => ({
  id: 'llama3-local',
  backend: 'llama_cpp',
  model_name: 'llama3-8b',
  capabilities: ['response_level'],
  metadata: {},
  ...overrides,
})

const buildModels = (): BackendInfo[] => [
  buildModel({
    id: 'llama3-local',
    backend: 'llama_cpp',
    model_name: 'llama3-8b',
    capabilities: ['response_level', 'token_step'],
  }),
  buildModel({
    id: 'qwen-local',
    backend: 'llama_cpp',
    model_name: 'qwen2-7b',
    capabilities: ['response_level', 'token_step'],
  }),
  buildModel({
    id: 'claude-cloud',
    backend: 'anthropic',
    model_name: 'claude-3-haiku',
    capabilities: ['response_level'],
  }),
]

describe('parallel-ensemble/model-selector', () => {
  describe('Rendering', () => {
    // Initial render should expose a placeholder rather than a count
    // until the user picks at least one alias.
    it('shows the empty placeholder when nothing is selected', () => {
      render(
        <ModelSelector
          readonly={false}
          models={buildModels()}
          requiredCapabilities={[]}
          selected={[]}
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText(/workflow\.nodes\.parallelEnsemble\.modelsPlaceholder/)).toBeInTheDocument()
    })

    // A loading flag overrides the count label and disables the trigger.
    it('shows the loading label and disables the trigger while loading', () => {
      render(
        <ModelSelector
          readonly={false}
          isLoading
          models={[]}
          requiredCapabilities={[]}
          selected={[]}
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText(/common\.loading/)).toBeInTheDocument()
      expect(screen.getByTestId('dropdown-trigger')).toBeDisabled()
    })

    // Non-empty selections surface the count label, parameterised with
    // the integer count for the i18n interpolator.
    it('shows the count label when at least one alias is selected', () => {
      render(
        <ModelSelector
          readonly={false}
          models={buildModels()}
          requiredCapabilities={[]}
          selected={['llama3-local', 'qwen-local']}
          onChange={vi.fn()}
        />,
      )

      expect(
        screen.getByText(
          /workflow\.nodes\.parallelEnsemble\.modelsSelectedCount.*"count":2/,
        ),
      ).toBeInTheDocument()
    })

    // Empty registry surfaces the "edit yaml" hint instead of an empty
    // dropdown — guards against the user thinking the panel is broken.
    it('renders the empty-registry hint when no models are passed', () => {
      render(
        <ModelSelector
          readonly={false}
          models={[]}
          requiredCapabilities={[]}
          selected={[]}
          onChange={vi.fn()}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))
      expect(
        screen.getByText(/workflow\.nodes\.parallelEnsemble\.noModelsAvailable/),
      ).toBeInTheDocument()
    })
  })

  describe('Capability filtering — token_step runner', () => {
    // Backbone of the P2.12 spec: when a token_step-style runner is
    // active, alias rows whose backend lacks that capability (e.g.
    // anthropic SaaS) must surface as greyed and gain a tooltip
    // explaining the missing capability.
    const TOKEN_STEP_REQS = ['token_step']

    it('greys out alias rows missing required capability', () => {
      render(
        <ModelSelector
          readonly={false}
          models={buildModels()}
          requiredCapabilities={TOKEN_STEP_REQS}
          selected={[]}
          onChange={vi.fn()}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))

      const incompatibleRow = screen
        .getByText('claude-cloud')
        .closest('[role="menuitem"]') as HTMLElement
      const compatibleRow = screen
        .getByText('llama3-local')
        .closest('[role="menuitem"]') as HTMLElement

      expect(incompatibleRow.className).toContain('opacity-50')
      expect(compatibleRow.className).not.toContain('opacity-50')
    })

    it('renders a tooltip describing the missing capability', () => {
      render(
        <ModelSelector
          readonly={false}
          models={buildModels()}
          requiredCapabilities={TOKEN_STEP_REQS}
          selected={[]}
          onChange={vi.fn()}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))

      // Tooltip mock surfaces TooltipContent inline; the i18n stub
      // serialises params, so the missing capability key shows up in
      // the rendered text.
      const tooltips = screen.getAllByRole('tooltip')
      const fired = tooltips.find(t => t.textContent?.includes('"missing":"token_step"'))
      expect(fired).toBeTruthy()
    })

    it('refuses to add an incompatible alias when newly clicked', () => {
      const onChange = vi.fn()
      render(
        <ModelSelector
          readonly={false}
          models={buildModels()}
          requiredCapabilities={TOKEN_STEP_REQS}
          selected={[]}
          onChange={onChange}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))
      fireEvent.click(screen.getByText('claude-cloud'))

      expect(onChange).not.toHaveBeenCalled()
    })

    it('still allows deselecting an incompatible alias that was already saved', () => {
      // Carrying over an alias from a runner switch must remain
      // de-selectable so the user can clean it up — backend
      // ``validate_selection`` rejects it at run time anyway.
      const onChange = vi.fn()
      render(
        <ModelSelector
          readonly={false}
          models={buildModels()}
          requiredCapabilities={TOKEN_STEP_REQS}
          selected={['claude-cloud']}
          onChange={onChange}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))
      fireEvent.click(screen.getByText('claude-cloud'))

      expect(onChange).toHaveBeenCalledWith([])
    })
  })

  describe('Selection mutation', () => {
    it('toggles the alias on click', () => {
      const onChange = vi.fn()
      render(
        <ModelSelector
          readonly={false}
          models={buildModels()}
          requiredCapabilities={[]}
          selected={['llama3-local']}
          onChange={onChange}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))
      fireEvent.click(screen.getByText('qwen-local'))

      expect(onChange).toHaveBeenCalledWith(['llama3-local', 'qwen-local'])
    })

    it('disables the trigger when readonly is true', () => {
      render(
        <ModelSelector
          readonly
          models={buildModels()}
          requiredCapabilities={[]}
          selected={['llama3-local']}
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByTestId('dropdown-trigger')).toBeDisabled()
    })
  })
})
