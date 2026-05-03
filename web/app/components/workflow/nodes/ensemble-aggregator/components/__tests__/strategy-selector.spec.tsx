import type { EnsembleStrategyConfig, EnsembleStrategyName } from '../../types'
import type { UiSchema } from '@/app/components/workflow/nodes/parallel-ensemble/types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StrategySelector from '../strategy-selector'

// Radix-UI dropdown content lives in a portal happy-dom doesn't
// render — replace the menu surface with an inline render that keeps
// click semantics intact while staying queryable.
vi.mock('@langgenius/dify-ui/dropdown-menu', async () => {
  const ReactInner = await import('react')
  type Ctx = { open: boolean, setOpen: (next: boolean) => void }
  const DropdownMenuContext = ReactInner.createContext<Ctx | null>(null)
  const useCtx = () => {
    const ctx = ReactInner.use(DropdownMenuContext)
    if (!ctx)
      throw new Error('DropdownMenu must wrap children')
    return ctx
  }
  return {
    DropdownMenu: ({ children, open, onOpenChange }: { children: React.ReactNode, open: boolean, onOpenChange?: (next: boolean) => void }) => (
      <DropdownMenuContext value={{ open, setOpen: onOpenChange ?? (() => undefined) }}>
        <div data-testid="dropdown-menu" data-open={open}>{children}</div>
      </DropdownMenuContext>
    ),
    DropdownMenuTrigger: ({ children, disabled }: { children: React.ReactNode, disabled?: boolean }) => {
      const { open, setOpen } = useCtx()
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
      const { open } = useCtx()
      return open ? <div data-testid="dropdown-content">{children}</div> : null
    },
    DropdownMenuItem: ({ children, onClick, className }: { children: React.ReactNode, onClick?: React.MouseEventHandler<HTMLDivElement>, className?: string }) => (
      <div role="menuitem" className={className} onClick={onClick}>{children}</div>
    ),
  }
})

// DynamicConfigForm is reflective and the contract under test is the
// reflection itself — capture the props instead of rendering its full
// surface (which has its own dedicated spec under parallel-ensemble).
type FormProps = {
  i18nKeyPrefix: string
  uiSchema: UiSchema
  value: EnsembleStrategyConfig
  readonly?: boolean
  onChange: (next: EnsembleStrategyConfig) => void
}
let lastFormProps: FormProps | null = null
vi.mock('@/app/components/workflow/nodes/parallel-ensemble/components/dynamic-config-form', () => ({
  __esModule: true,
  default: (props: FormProps) => {
    lastFormProps = props
    return (
      <div
        data-testid="dynamic-form"
        data-prefix={props.i18nKeyPrefix}
        data-keys={Object.keys(props.uiSchema).sort().join(',')}
      >
        <button
          type="button"
          data-testid="dynamic-form-emit"
          onClick={() => props.onChange({ separator: '|' })}
        >
          emit
        </button>
      </div>
    )
  },
}))

type Handlers = {
  onStrategyChange: ReturnType<typeof vi.fn<(name: EnsembleStrategyName) => void>>
  onStrategyConfigChange: ReturnType<typeof vi.fn<(next: EnsembleStrategyConfig) => void>>
}
const renderSelector = (overrides: Partial<Handlers> & {
  strategyName?: EnsembleStrategyName
  strategyConfig?: EnsembleStrategyConfig
  readonly?: boolean
} = {}) => {
  const handlers: Handlers = {
    onStrategyChange: overrides.onStrategyChange ?? vi.fn<(name: EnsembleStrategyName) => void>(),
    onStrategyConfigChange: overrides.onStrategyConfigChange ?? vi.fn<(next: EnsembleStrategyConfig) => void>(),
  }
  render(
    <StrategySelector
      readonly={overrides.readonly ?? false}
      strategyName={overrides.strategyName ?? 'concat'}
      strategyConfig={overrides.strategyConfig ?? {}}
      {...handlers}
    />,
  )
  return handlers
}

describe('ensemble-aggregator/strategy-selector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    lastFormProps = null
  })

  describe('Dropdown surface', () => {
    // The closed dropdown shows the active strategy's i18n label,
    // never a stale or generic placeholder.
    it('shows the active strategy label on the trigger', () => {
      renderSelector({ strategyName: 'concat' })

      const trigger = screen.getByTestId('dropdown-trigger')
      expect(trigger.textContent).toMatch(
        /nodes\.ensembleAggregator\.strategies\.concat\.label/,
      )
    })

    // Voting strategies were removed; only the concat row remains.
    it('lists the registered strategy in the dropdown', () => {
      renderSelector()

      fireEvent.click(screen.getByTestId('dropdown-trigger'))

      const content = screen.getByTestId('dropdown-content')
      const rows = Array.from(
        content.querySelectorAll('[role="menuitem"]'),
      ) as HTMLElement[]
      expect(rows).toHaveLength(1)
      expect(rows[0].textContent ?? '').toMatch(/strategies\.concat\.label/)
    })
  })

  describe('Selection mutation', () => {
    // Re-selecting the active strategy is a no-op so a stray click
    // doesn't reset accumulated strategy_config (e.g. wipe a
    // separator the operator already typed).
    it('does not fire onStrategyChange when re-selecting the active strategy', () => {
      const onStrategyChange = vi.fn<(name: EnsembleStrategyName) => void>()
      renderSelector({ strategyName: 'concat', onStrategyChange })

      fireEvent.click(screen.getByTestId('dropdown-trigger'))
      const content = screen.getByTestId('dropdown-content')
      const concatRow = (Array.from(content.querySelectorAll('[role="menuitem"]')) as HTMLElement[])
        .find(r => /strategies\.concat\.label/.test(r.textContent ?? ''))!
      fireEvent.click(concatRow)

      expect(onStrategyChange).not.toHaveBeenCalled()
    })

    it('disables the trigger when readonly', () => {
      renderSelector({ readonly: true })
      expect(screen.getByTestId('dropdown-trigger')).toBeDisabled()
    })
  })

  describe('Schema reflection', () => {
    // ``concat`` exposes three keys in ui_schema; the dynamic form
    // receives the same keys + the strategy's i18n_key_prefix so the
    // shared field renderer resolves labels under that namespace.
    it('renders DynamicConfigForm with the concat ui_schema and i18n prefix', () => {
      renderSelector({
        strategyName: 'concat',
        strategyConfig: { separator: ' | ' },
      })

      const form = screen.getByTestId('dynamic-form')
      expect(form.dataset.prefix).toBe(
        'nodes.ensembleAggregator.concat',
      )
      expect(form.dataset.keys).toBe(
        'include_source_label,order_by_weight,separator',
      )
      expect(lastFormProps?.value).toEqual({ separator: ' | ' })
    })

    // Form's onChange flows through verbatim — strategy-selector is
    // a pass-through so the parent hook owns the merge semantics.
    it('forwards form mutations to onStrategyConfigChange', () => {
      const onStrategyConfigChange = vi.fn<(next: EnsembleStrategyConfig) => void>()
      renderSelector({
        strategyName: 'concat',
        onStrategyConfigChange,
      })

      fireEvent.click(screen.getByTestId('dynamic-form-emit'))

      expect(onStrategyConfigChange).toHaveBeenCalledWith({ separator: '|' })
    })

    // Readonly is propagated to the inner form so strategy-specific
    // controls (text input / switches) all reach a disabled state.
    it('propagates readonly into DynamicConfigForm', () => {
      renderSelector({ strategyName: 'concat', readonly: true })

      expect(lastFormProps?.readonly).toBe(true)
    })
  })
})
