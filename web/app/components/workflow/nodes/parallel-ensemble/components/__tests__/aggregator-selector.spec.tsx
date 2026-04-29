import type { AggregatorMeta } from '../../types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { describe, expect, it, vi } from 'vitest'
import AggregatorSelector from '../aggregator-selector'

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

const buildAggregator = (overrides: Partial<AggregatorMeta> = {}): AggregatorMeta => ({
  name: 'majority_vote',
  i18n_key_prefix: 'parallelEnsemble.aggregators.majorityVote',
  ui_schema: {},
  config_schema: {},
  scope: 'response',
  ...overrides,
})

const buildAggregators = (): AggregatorMeta[] => [
  buildAggregator(),
  buildAggregator({
    name: 'self_consistency',
    i18n_key_prefix: 'parallelEnsemble.aggregators.selfConsistency',
    scope: 'response',
  }),
  buildAggregator({
    name: 'token_logit_mean',
    i18n_key_prefix: 'parallelEnsemble.aggregators.tokenLogitMean',
    scope: 'token',
  }),
]

describe('parallel-ensemble/aggregator-selector', () => {
  describe('Scope filtering', () => {
    // P2.12 spec: switching to a token-scope runner must hide
    // response-scope aggregators so the user can't pick a pair the §9
    // startup pipeline would reject server-side.
    it('hides response-scope aggregators when the runner requires token scope', () => {
      render(
        <AggregatorSelector
          readonly={false}
          aggregators={buildAggregators()}
          requiredScope="token"
          selectedName=""
          onChange={vi.fn()}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))

      const content = screen.getByTestId('dropdown-content')
      const rows = Array.from(content.querySelectorAll('[role="menuitem"]')) as HTMLElement[]
      expect(rows).toHaveLength(1)
      expect(rows[0]!.textContent).toMatch(/parallelEnsemble\.aggregators\.tokenLogitMean\.name/)
    })

    it('shows only response-scope aggregators when the runner requires response scope', () => {
      render(
        <AggregatorSelector
          readonly={false}
          aggregators={buildAggregators()}
          requiredScope="response"
          selectedName="majority_vote"
          onChange={vi.fn()}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))

      const content = screen.getByTestId('dropdown-content')
      const rows = Array.from(content.querySelectorAll('[role="menuitem"]')) as HTMLElement[]
      const labels = rows.map(r => r.textContent ?? '')
      expect(rows).toHaveLength(2)
      expect(labels.some(t => /majorityVote\.name/.test(t))).toBe(true)
      expect(labels.some(t => /selfConsistency\.name/.test(t))).toBe(true)
      expect(labels.every(t => !/tokenLogitMean/.test(t))).toBe(true)
    })

    // When the active scope has no compatible aggregators, the trigger
    // surfaces the "no aggregator" hint and is disabled — saving an
    // empty pairing would break §9 anyway.
    it('disables the trigger and surfaces a hint when no aggregator matches the scope', () => {
      render(
        <AggregatorSelector
          readonly={false}
          aggregators={buildAggregators()}
          requiredScope="judge"
          selectedName=""
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText(/noAggregatorForScope/)).toBeInTheDocument()
      expect(screen.getByTestId('dropdown-trigger')).toBeDisabled()
    })
  })

  describe('Selection mutation', () => {
    it('fires onChange with the aggregator descriptor on click', () => {
      const onChange = vi.fn()
      render(
        <AggregatorSelector
          readonly={false}
          aggregators={buildAggregators()}
          requiredScope="response"
          selectedName="majority_vote"
          onChange={onChange}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))
      const content = screen.getByTestId('dropdown-content')
      const row = (Array.from(content.querySelectorAll('[role="menuitem"]')) as HTMLElement[])
        .find(el => /selfConsistency\.name/.test(el.textContent ?? ''))!
      fireEvent.click(row)

      expect(onChange).toHaveBeenCalledTimes(1)
      expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
        name: 'self_consistency',
        scope: 'response',
      }))
    })

    it('does not fire onChange when re-selecting the active aggregator', () => {
      const onChange = vi.fn()
      render(
        <AggregatorSelector
          readonly={false}
          aggregators={buildAggregators()}
          requiredScope="response"
          selectedName="majority_vote"
          onChange={onChange}
        />,
      )

      fireEvent.click(screen.getByTestId('dropdown-trigger'))
      const content = screen.getByTestId('dropdown-content')
      const row = (Array.from(content.querySelectorAll('[role="menuitem"]')) as HTMLElement[])
        .find(el => /majorityVote\.name/.test(el.textContent ?? ''))!
      fireEvent.click(row)

      expect(onChange).not.toHaveBeenCalled()
    })

    it('disables the trigger when readonly is true', () => {
      render(
        <AggregatorSelector
          readonly
          aggregators={buildAggregators()}
          requiredScope="response"
          selectedName="majority_vote"
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByTestId('dropdown-trigger')).toBeDisabled()
    })
  })

  describe('Loading and placeholder states', () => {
    it('shows the loading label when loading is in progress', () => {
      render(
        <AggregatorSelector
          readonly={false}
          isLoading
          aggregators={buildAggregators()}
          requiredScope="response"
          selectedName=""
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText(/common\.loading/)).toBeInTheDocument()
      expect(screen.getByTestId('dropdown-trigger')).toBeDisabled()
    })

    it('shows the placeholder when nothing valid is selected', () => {
      render(
        <AggregatorSelector
          readonly={false}
          aggregators={buildAggregators()}
          requiredScope="response"
          selectedName=""
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText(/aggregatorPlaceholder/)).toBeInTheDocument()
    })
  })
})
