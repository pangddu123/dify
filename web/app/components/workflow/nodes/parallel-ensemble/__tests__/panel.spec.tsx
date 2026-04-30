import type {
  AggregatorMeta,
  ParallelEnsembleNodeType,
  RunnerMeta,
  TokenSourceRef,
  ValidationIssue,
} from '../types'
import type { NodePanelProps } from '@/app/components/workflow/types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BlockEnum } from '@/app/components/workflow/types'
import Panel from '../panel'
import { DEFAULT_DIAGNOSTICS } from '../types'

// ── Hoisted mocks ───────────────────────────────────────────────────
//
// Panel composes several child surfaces — keep them as thin
// pass-through stubs so this test focuses on the panel's wiring (which
// section appears, which handler fires for which child callback).
const mockUseConfig = vi.hoisted(() => vi.fn())

vi.mock('../use-config', () => ({
  __esModule: true,
  default: (...args: unknown[]) => mockUseConfig(...args),
}))

type SelectorMockProps = {
  selectedName?: string
  requiredScope?: string
  runners?: ReadonlyArray<{ name: string }>
  aggregators?: ReadonlyArray<{ name: string }>
  onChange: (next: unknown) => void
}

vi.mock('../components/token-source-list', () => ({
  __esModule: true,
  default: ({
    list,
    onAdd,
    onSourceIdChange,
    onSpecSelectorChange,
    onWeightChange,
    onTopKOverrideChange,
    onFallbackWeightChange,
  }: {
    list: TokenSourceRef[]
    onAdd: () => void
    onRemove: (i: number) => void
    onSourceIdChange: (i: number, v: string) => void
    onSpecSelectorChange: (i: number, sel: string[]) => void
    onWeightChange: (i: number, v: number | string[]) => void
    onTopKOverrideChange: (i: number, v: number | null) => void
    onFallbackWeightChange: (i: number, v: number | null) => void
  }) => (
    <div data-testid="token-source-list">
      <span data-testid="token-source-list-count">{list.length}</span>
      <span data-testid="token-source-list-ids">
        {list.map(r => r.source_id).join(',')}
      </span>
      <button type="button" onClick={onAdd}>add-source</button>
      <button type="button" onClick={() => onSourceIdChange(0, 'renamed')}>rename-source</button>
      <button type="button" onClick={() => onSpecSelectorChange(0, ['source_node', 'spec'])}>pick-spec</button>
      <button type="button" onClick={() => onWeightChange(0, 2.5)}>set-weight</button>
      <button type="button" onClick={() => onTopKOverrideChange(0, 8)}>set-top-k</button>
      <button type="button" onClick={() => onFallbackWeightChange(0, 0.5)}>set-fallback</button>
    </div>
  ),
}))

vi.mock('../components/runner-selector', () => ({
  __esModule: true,
  default: ({ selectedName, runners, onChange }: SelectorMockProps) => (
    <div data-testid="runner-selector">
      <span data-testid="runner-selector-active">{selectedName}</span>
      <span data-testid="runner-selector-count">{runners?.length}</span>
      <button
        type="button"
        onClick={() => onChange({
          name: 'token_step',
          aggregator_scope: 'token',
          ui_schema: {},
          i18n_key_prefix: 'parallelEnsemble.runners.tokenStep',
          required_capabilities: ['token_step'],
          optional_capabilities: [],
          config_schema: {},
        })}
      >
        change-runner
      </button>
    </div>
  ),
}))

vi.mock('../components/aggregator-selector', () => ({
  __esModule: true,
  default: ({ selectedName, requiredScope, aggregators, onChange }: SelectorMockProps) => (
    <div data-testid="aggregator-selector">
      <span data-testid="aggregator-selector-active">{selectedName}</span>
      <span data-testid="aggregator-selector-scope">{requiredScope}</span>
      <span data-testid="aggregator-selector-count">{aggregators?.length}</span>
      <button
        type="button"
        onClick={() => onChange({
          name: 'sum_score',
          scope: 'token',
          ui_schema: {},
          i18n_key_prefix: 'parallelEnsemble.aggregators.sumScore',
          config_schema: {},
        })}
      >
        change-aggregator
      </button>
    </div>
  ),
}))

vi.mock('../components/dynamic-config-form', () => ({
  __esModule: true,
  default: ({ i18nKeyPrefix, onChange }: { i18nKeyPrefix: string, onChange: (v: Record<string, unknown>) => void }) => (
    <div data-testid={`dynamic-config-form:${i18nKeyPrefix}`}>
      <button
        type="button"
        onClick={() => onChange({ top_k: 8 })}
      >
        change-config-
        {i18nKeyPrefix}
      </button>
    </div>
  ),
}))

vi.mock('../components/diagnostics-config', () => ({
  __esModule: true,
  default: ({ value, onChange }: { value: { storage: string }, onChange: (patch: Record<string, unknown>) => void }) => (
    <div data-testid="diagnostics-config">
      <span data-testid="diagnostics-storage">{value.storage}</span>
      <button type="button" onClick={() => onChange({ storage: 'inline' })}>
        change-diagnostics
      </button>
    </div>
  ),
}))

// Output-vars wrapper renders children — surface them in a div so
// assertions can verify the `trace` slot's conditional inclusion.
vi.mock('@/app/components/workflow/nodes/_base/components/output-vars', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="output-vars">{children}</div>
  ),
  VarItem: ({ name, type }: { name: string, type: string }) => (
    <div data-testid={`var-item-${name}`}>{`${name}:${type}`}</div>
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

const buildRunner = (overrides: Partial<RunnerMeta> = {}): RunnerMeta => ({
  name: 'token_step',
  i18n_key_prefix: 'parallelEnsemble.runners.tokenStep',
  ui_schema: {},
  config_schema: {},
  aggregator_scope: 'token',
  required_capabilities: ['token_step'],
  optional_capabilities: [],
  ...overrides,
})

const buildAggregator = (overrides: Partial<AggregatorMeta> = {}): AggregatorMeta => ({
  name: 'sum_score',
  i18n_key_prefix: 'parallelEnsemble.aggregators.sumScore',
  ui_schema: {},
  config_schema: {},
  scope: 'token',
  ...overrides,
})

const buildPayload = (overrides: Partial<ParallelEnsembleNodeType> = {}): ParallelEnsembleNodeType => ({
  title: 'Parallel Ensemble',
  desc: '',
  type: BlockEnum.ParallelEnsemble,
  ensemble: {
    token_sources: [buildSource()],
    // Pin to ``token_step`` + ``sum_score`` so the fixture aligns with
    // the runner / aggregator builders below — the v3 default
    // (``response_level`` / ``majority_vote``) wouldn't resolve against
    // the test registry.
    runner_name: 'token_step',
    runner_config: {},
    aggregator_name: 'sum_score',
    aggregator_config: {},
    diagnostics: { ...DEFAULT_DIAGNOSTICS },
  },
  ...overrides,
})

type ConfigResult = {
  readOnly: boolean
  inputs: ParallelEnsembleNodeType
  runners: ReadonlyArray<RunnerMeta>
  aggregators: ReadonlyArray<AggregatorMeta>
  selectedRunner?: RunnerMeta
  selectedAggregator?: AggregatorMeta
  isLoadingRunners: boolean
  isLoadingAggregators: boolean
  filterSpecVar: () => boolean
  filterNumericVar: () => boolean
  validationIssues: ValidationIssue[]
  handleAddTokenSource: ReturnType<typeof vi.fn>
  handleRemoveTokenSource: ReturnType<typeof vi.fn>
  handleSourceIdChange: ReturnType<typeof vi.fn>
  handleSpecSelectorChange: ReturnType<typeof vi.fn>
  handleWeightChange: ReturnType<typeof vi.fn>
  handleTopKOverrideChange: ReturnType<typeof vi.fn>
  handleFallbackWeightChange: ReturnType<typeof vi.fn>
  handleRunnerChange: ReturnType<typeof vi.fn>
  handleAggregatorChange: ReturnType<typeof vi.fn>
  handleRunnerConfigChange: ReturnType<typeof vi.fn>
  handleAggregatorConfigChange: ReturnType<typeof vi.fn>
  handleDiagnosticsChange: ReturnType<typeof vi.fn>
}

const buildConfig = (overrides: Partial<ConfigResult> = {}): ConfigResult => {
  const inputs = overrides.inputs ?? buildPayload()
  const runners = overrides.runners ?? [
    buildRunner(),
    buildRunner({
      name: 'token_step_alt',
      i18n_key_prefix: 'parallelEnsemble.runners.tokenStepAlt',
      aggregator_scope: 'token',
      ui_schema: { top_k: { control: 'number_input', min: 1 } },
    }),
  ]
  const aggregators = overrides.aggregators ?? [
    buildAggregator(),
    buildAggregator({
      name: 'max_score',
      i18n_key_prefix: 'parallelEnsemble.aggregators.maxScore',
      scope: 'token',
      ui_schema: { use_weights: { control: 'switch' } },
    }),
  ]
  return {
    readOnly: false,
    inputs,
    runners,
    aggregators,
    selectedRunner: overrides.selectedRunner ?? runners.find(r => r.name === inputs.ensemble?.runner_name),
    selectedAggregator: overrides.selectedAggregator ?? aggregators.find(a => a.name === inputs.ensemble?.aggregator_name),
    isLoadingRunners: overrides.isLoadingRunners ?? false,
    isLoadingAggregators: overrides.isLoadingAggregators ?? false,
    filterSpecVar: overrides.filterSpecVar ?? (() => true),
    filterNumericVar: overrides.filterNumericVar ?? (() => true),
    validationIssues: overrides.validationIssues ?? [],
    handleAddTokenSource: overrides.handleAddTokenSource ?? vi.fn(),
    handleRemoveTokenSource: overrides.handleRemoveTokenSource ?? vi.fn(),
    handleSourceIdChange: overrides.handleSourceIdChange ?? vi.fn(),
    handleSpecSelectorChange: overrides.handleSpecSelectorChange ?? vi.fn(),
    handleWeightChange: overrides.handleWeightChange ?? vi.fn(),
    handleTopKOverrideChange: overrides.handleTopKOverrideChange ?? vi.fn(),
    handleFallbackWeightChange: overrides.handleFallbackWeightChange ?? vi.fn(),
    handleRunnerChange: overrides.handleRunnerChange ?? vi.fn(),
    handleAggregatorChange: overrides.handleAggregatorChange ?? vi.fn(),
    handleRunnerConfigChange: overrides.handleRunnerConfigChange ?? vi.fn(),
    handleAggregatorConfigChange: overrides.handleAggregatorConfigChange ?? vi.fn(),
    handleDiagnosticsChange: overrides.handleDiagnosticsChange ?? vi.fn(),
  }
}

const renderPanel = (data: ParallelEnsembleNodeType = buildPayload()) => {
  const props: NodePanelProps<ParallelEnsembleNodeType> = {
    id: 'parallel-ensemble-1',
    data,
  } as unknown as NodePanelProps<ParallelEnsembleNodeType>
  return render(<Panel {...props} />)
}

describe('parallel-ensemble/panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Rendering — three-axis composition', () => {
    // The panel's job is to compose the three SPI axes (sources / runner
    // / aggregator) plus the diagnostics + output sections. Each child
    // must surface — missing sections silently break the user flow
    // without raising.
    it('renders all configuration sections for a populated payload', () => {
      mockUseConfig.mockReturnValue(buildConfig())
      renderPanel()

      expect(screen.getByTestId('token-source-list')).toBeInTheDocument()
      expect(screen.getByTestId('runner-selector')).toBeInTheDocument()
      expect(screen.getByTestId('aggregator-selector')).toBeInTheDocument()
      expect(screen.getByTestId('output-vars')).toBeInTheDocument()

      // The diagnostics section uses ``supportFold``, so it ships
      // collapsed by default — expand it before asserting the child
      // is mounted.
      fireEvent.click(screen.getByText(/diagnostics\.title/))
      expect(screen.getByTestId('diagnostics-config')).toBeInTheDocument()
    })

    it('forwards the active aggregator scope from the runner descriptor', () => {
      mockUseConfig.mockReturnValue(buildConfig())
      renderPanel()

      expect(screen.getByTestId('aggregator-selector-scope')).toHaveTextContent('token')
    })

    it('renders one row per token source', () => {
      mockUseConfig.mockReturnValue(buildConfig({
        inputs: buildPayload({
          ensemble: {
            token_sources: [
              buildSource({ source_id: 'a' }),
              buildSource({ source_id: 'b' }),
            ],
            runner_name: 'token_step',
            runner_config: {},
            aggregator_name: 'sum_score',
            aggregator_config: {},
            diagnostics: { ...DEFAULT_DIAGNOSTICS },
          },
        }),
      }))
      renderPanel()

      expect(screen.getByTestId('token-source-list-count')).toHaveTextContent('2')
      expect(screen.getByTestId('token-source-list-ids')).toHaveTextContent('a,b')
    })
  })

  describe('Defensive guards', () => {
    // DSL imports / saved snapshots could land without the nested
    // ``ensemble`` block — the panel must render nothing rather than
    // crash on undefined access. ``checkValid`` raises the structured
    // error in that case (see default.ts).
    it('renders nothing when the ensemble block is missing', () => {
      mockUseConfig.mockReturnValue(buildConfig({
        inputs: { ...buildPayload(), ensemble: undefined as never },
      }))

      const { container } = renderPanel({ ...buildPayload(), ensemble: undefined as never })
      expect(container.firstChild).toBeNull()
    })

    // ``inline`` storage adds a ``trace`` output variable — the runner
    // emits it under the same key. ``metadata`` storage routes the
    // trace to ``process_data.ensemble_trace`` which is *not* a
    // variable-pool selector, so the slot must hide.
    it('shows the trace output var only when storage=inline', () => {
      mockUseConfig.mockReturnValue(buildConfig({
        inputs: buildPayload({
          ensemble: {
            token_sources: [buildSource()],
            runner_name: 'token_step',
            runner_config: {},
            aggregator_name: 'sum_score',
            aggregator_config: {},
            diagnostics: { ...DEFAULT_DIAGNOSTICS, storage: 'inline' },
          },
        }),
      }))
      renderPanel()

      expect(screen.getByTestId('var-item-text')).toBeInTheDocument()
      expect(screen.getByTestId('var-item-tokens_count')).toBeInTheDocument()
      expect(screen.getByTestId('var-item-elapsed_ms')).toBeInTheDocument()
      expect(screen.getByTestId('var-item-trace')).toBeInTheDocument()
    })

    it('hides the trace output var when storage=metadata', () => {
      mockUseConfig.mockReturnValue(buildConfig())
      renderPanel()

      expect(screen.queryByTestId('var-item-trace')).not.toBeInTheDocument()
    })
  })

  describe('Dynamic config forms', () => {
    it('hides the runner_config form when the runner ui_schema is empty', () => {
      mockUseConfig.mockReturnValue(buildConfig())
      renderPanel()
      expect(
        screen.queryByTestId('dynamic-config-form:parallelEnsemble.runners.tokenStep'),
      ).not.toBeInTheDocument()
    })

    it('renders the runner_config form for a runner with a non-empty ui_schema', () => {
      const runners = [
        buildRunner({
          ui_schema: { top_k: { control: 'number_input' } },
        }),
      ]
      mockUseConfig.mockReturnValue(buildConfig({
        runners,
        inputs: buildPayload(),
      }))
      renderPanel()
      expect(
        screen.getByTestId('dynamic-config-form:parallelEnsemble.runners.tokenStep'),
      ).toBeInTheDocument()
    })
  })

  describe('Routing — child callbacks fire the right handlers', () => {
    it('forwards add-source clicks to handleAddTokenSource', () => {
      const config = buildConfig()
      mockUseConfig.mockReturnValue(config)
      renderPanel()

      fireEvent.click(screen.getByText('add-source'))
      expect(config.handleAddTokenSource).toHaveBeenCalledTimes(1)
    })

    it('forwards source-level mutations to their handlers', () => {
      const config = buildConfig()
      mockUseConfig.mockReturnValue(config)
      renderPanel()

      fireEvent.click(screen.getByText('rename-source'))
      expect(config.handleSourceIdChange).toHaveBeenCalledWith(0, 'renamed')

      fireEvent.click(screen.getByText('pick-spec'))
      expect(config.handleSpecSelectorChange).toHaveBeenCalledWith(0, ['source_node', 'spec'])

      fireEvent.click(screen.getByText('set-weight'))
      expect(config.handleWeightChange).toHaveBeenCalledWith(0, 2.5)

      fireEvent.click(screen.getByText('set-top-k'))
      expect(config.handleTopKOverrideChange).toHaveBeenCalledWith(0, 8)

      fireEvent.click(screen.getByText('set-fallback'))
      expect(config.handleFallbackWeightChange).toHaveBeenCalledWith(0, 0.5)
    })

    it('forwards runner switch to handleRunnerChange with the descriptor', () => {
      const config = buildConfig()
      mockUseConfig.mockReturnValue(config)
      renderPanel()

      fireEvent.click(screen.getByText('change-runner'))
      expect(config.handleRunnerChange).toHaveBeenCalledTimes(1)
      expect(config.handleRunnerChange.mock.calls[0]![0]).toMatchObject({
        name: 'token_step',
        aggregator_scope: 'token',
      })
    })

    it('forwards aggregator switch to handleAggregatorChange', () => {
      const config = buildConfig()
      mockUseConfig.mockReturnValue(config)
      renderPanel()

      fireEvent.click(screen.getByText('change-aggregator'))
      expect(config.handleAggregatorChange).toHaveBeenCalledTimes(1)
    })

    it('forwards diagnostics patches to handleDiagnosticsChange', () => {
      const config = buildConfig()
      mockUseConfig.mockReturnValue(config)
      renderPanel()

      // Expand the diagnostics fold-section first.
      fireEvent.click(screen.getByText(/diagnostics\.title/))
      fireEvent.click(screen.getByText('change-diagnostics'))
      expect(config.handleDiagnosticsChange).toHaveBeenCalledWith({ storage: 'inline' })
    })
  })
})
