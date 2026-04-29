import type {
  AggregatorMeta,
  ParallelEnsembleNodeType,
  RunnerMeta,
  ValidationIssue,
} from '../types'
import type { NodePanelProps } from '@/app/components/workflow/types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BlockEnum } from '@/app/components/workflow/types'
import Panel from '../panel'
import {
  DEFAULT_AGGREGATOR_NAME,
  DEFAULT_DIAGNOSTICS,
  DEFAULT_RUNNER_NAME,
} from '../types'

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

// VarReferencePicker: emit a value through the panel's selector
// callback. The Panel's filter only surfaces array values (rejecting
// constants), so the stub returns an array.
vi.mock('@/app/components/workflow/nodes/_base/components/variable/var-reference-picker', () => ({
  __esModule: true,
  default: ({ onChange }: { onChange: (value: unknown) => void }) => (
    <button
      type="button"
      data-testid="var-picker"
      onClick={() => onChange(['start', 'question'])}
    >
      pick-question
    </button>
  ),
}))

// All four child surfaces are tested independently — keep them as
// simple stubs that surface the props the panel passes them so we can
// assert routing (which child receives which configuration slice).
type SelectorMockProps = {
  selected?: ReadonlyArray<string>
  selectedName?: string
  requiredCapabilities?: ReadonlyArray<string>
  requiredScope?: string
  models?: ReadonlyArray<{ id: string }>
  runners?: ReadonlyArray<{ name: string }>
  aggregators?: ReadonlyArray<{ name: string }>
  onChange: (next: unknown) => void
}

vi.mock('../components/model-selector', () => ({
  __esModule: true,
  default: ({ selected, requiredCapabilities, models, onChange }: SelectorMockProps) => (
    <div data-testid="model-selector">
      <span data-testid="model-selector-required">{requiredCapabilities?.join(',')}</span>
      <span data-testid="model-selector-selected">{selected?.join(',')}</span>
      <span data-testid="model-selector-count">{models?.length}</span>
      <button type="button" onClick={() => onChange(['llama3-local', 'qwen-local'])}>
        change-models
      </button>
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
          name: 'self_consistency',
          scope: 'response',
          ui_schema: {},
          i18n_key_prefix: 'parallelEnsemble.aggregators.selfConsistency',
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

vi.mock('../components/import-model-info-button', () => ({
  __esModule: true,
  default: ({ isLoading, knownAliases, onImport }: { isLoading?: boolean, knownAliases: ReadonlyArray<string>, onImport: (a: string[]) => void }) => (
    <button
      type="button"
      data-testid="import-button"
      data-known={knownAliases.join(',')}
      data-loading={isLoading ? 'true' : 'false'}
      disabled={isLoading}
      onClick={() => onImport(['imported-model'])}
    >
      import
    </button>
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

const buildModels = () => [
  { id: 'llama3-local', backend: 'llama_cpp', model_name: 'llama3-8b', capabilities: ['response_level', 'token_step'], metadata: {} },
  { id: 'qwen-local', backend: 'llama_cpp', model_name: 'qwen2-7b', capabilities: ['response_level', 'token_step'], metadata: {} },
]

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

const buildAggregator = (overrides: Partial<AggregatorMeta> = {}): AggregatorMeta => ({
  name: 'majority_vote',
  i18n_key_prefix: 'parallelEnsemble.aggregators.majorityVote',
  ui_schema: {},
  config_schema: {},
  scope: 'response',
  ...overrides,
})

const buildPayload = (overrides: Partial<ParallelEnsembleNodeType> = {}): ParallelEnsembleNodeType => ({
  title: 'Parallel Ensemble',
  desc: '',
  type: BlockEnum.ParallelEnsemble,
  ensemble: {
    question_variable: ['start', 'question'],
    model_aliases: ['llama3-local'],
    runner_name: DEFAULT_RUNNER_NAME,
    runner_config: {},
    aggregator_name: DEFAULT_AGGREGATOR_NAME,
    aggregator_config: {},
    diagnostics: { ...DEFAULT_DIAGNOSTICS },
  },
  ...overrides,
})

type ConfigResult = {
  readOnly: boolean
  inputs: ParallelEnsembleNodeType
  models: ReadonlyArray<ReturnType<typeof buildModels>[number]>
  runners: ReadonlyArray<RunnerMeta>
  aggregators: ReadonlyArray<AggregatorMeta>
  selectedRunner?: RunnerMeta
  selectedAggregator?: AggregatorMeta
  isLoadingModels: boolean
  isLoadingRunners: boolean
  isLoadingAggregators: boolean
  validationIssues: ValidationIssue[]
  handleQuestionVariableChange: ReturnType<typeof vi.fn>
  handleModelAliasesChange: ReturnType<typeof vi.fn>
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
      name: 'token_step',
      i18n_key_prefix: 'parallelEnsemble.runners.tokenStep',
      aggregator_scope: 'token',
      required_capabilities: ['token_step'],
      ui_schema: { top_k: { control: 'number_input', min: 1 } },
    }),
  ]
  const aggregators = overrides.aggregators ?? [
    buildAggregator(),
    buildAggregator({
      name: 'self_consistency',
      i18n_key_prefix: 'parallelEnsemble.aggregators.selfConsistency',
      scope: 'response',
      ui_schema: { temperature: { control: 'number_input' } },
    }),
  ]
  return {
    readOnly: false,
    inputs,
    models: overrides.models ?? buildModels(),
    runners,
    aggregators,
    selectedRunner: overrides.selectedRunner ?? runners.find(r => r.name === inputs.ensemble?.runner_name),
    selectedAggregator: overrides.selectedAggregator ?? aggregators.find(a => a.name === inputs.ensemble?.aggregator_name),
    isLoadingModels: overrides.isLoadingModels ?? false,
    isLoadingRunners: overrides.isLoadingRunners ?? false,
    isLoadingAggregators: overrides.isLoadingAggregators ?? false,
    validationIssues: overrides.validationIssues ?? [],
    handleQuestionVariableChange: overrides.handleQuestionVariableChange ?? vi.fn(),
    handleModelAliasesChange: overrides.handleModelAliasesChange ?? vi.fn(),
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
    // The panel's job is to compose the three SPI axes (model / runner
    // / aggregator) plus the diagnostics + question + output sections.
    // Each child must surface — missing sections silently break the
    // user flow without raising.
    it('renders all configuration sections for a populated payload', () => {
      mockUseConfig.mockReturnValue(buildConfig())
      renderPanel()

      expect(screen.getByTestId('var-picker')).toBeInTheDocument()
      expect(screen.getByTestId('model-selector')).toBeInTheDocument()
      expect(screen.getByTestId('runner-selector')).toBeInTheDocument()
      expect(screen.getByTestId('aggregator-selector')).toBeInTheDocument()
      expect(screen.getByTestId('import-button')).toBeInTheDocument()
      expect(screen.getByTestId('output-vars')).toBeInTheDocument()

      // The diagnostics section uses ``supportFold``, so it ships
      // collapsed by default — expand it before asserting the child
      // is mounted.
      fireEvent.click(screen.getByText(/diagnostics\.title/))
      expect(screen.getByTestId('diagnostics-config')).toBeInTheDocument()
    })

    it('passes the selected runner capabilities and the current model selection to ModelSelector', () => {
      mockUseConfig.mockReturnValue(buildConfig({
        inputs: buildPayload({
          ensemble: {
            question_variable: ['start', 'q'],
            model_aliases: ['llama3-local'],
            runner_name: 'token_step',
            runner_config: {},
            aggregator_name: '',
            aggregator_config: {},
            diagnostics: { ...DEFAULT_DIAGNOSTICS },
          },
        }),
      }))
      renderPanel()

      expect(screen.getByTestId('model-selector-required')).toHaveTextContent('token_step')
      expect(screen.getByTestId('model-selector-selected')).toHaveTextContent('llama3-local')
      expect(screen.getByTestId('model-selector-count')).toHaveTextContent('2')
    })

    it('forwards the active aggregator scope from the runner descriptor', () => {
      mockUseConfig.mockReturnValue(buildConfig({
        inputs: buildPayload({
          ensemble: {
            question_variable: ['start', 'q'],
            model_aliases: ['llama3-local'],
            runner_name: 'token_step',
            runner_config: {},
            aggregator_name: '',
            aggregator_config: {},
            diagnostics: { ...DEFAULT_DIAGNOSTICS },
          },
        }),
      }))
      renderPanel()

      expect(screen.getByTestId('aggregator-selector-scope')).toHaveTextContent('token')
    })

    // While the model registry is loading, ``knownAliases`` is empty;
    // letting the user click "Import" would surface a misleading
    // "noneMatched" toast for every imported id. The panel must wire
    // the registry's loading flag through so the button stays disabled
    // until the fetch resolves.
    it('disables the import button while the model registry is loading', () => {
      mockUseConfig.mockReturnValue(buildConfig({ isLoadingModels: true }))
      renderPanel()

      expect(screen.getByTestId('import-button')).toBeDisabled()
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
            question_variable: ['start', 'q'],
            model_aliases: ['llama3-local'],
            runner_name: DEFAULT_RUNNER_NAME,
            runner_config: {},
            aggregator_name: DEFAULT_AGGREGATOR_NAME,
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
    // Runner config form only renders when the runner declares a
    // non-empty ui_schema. ``response_level`` ships an empty schema so
    // the section disappears for the default config.
    it('hides the runner_config form when the runner ui_schema is empty', () => {
      mockUseConfig.mockReturnValue(buildConfig())
      renderPanel()
      expect(
        screen.queryByTestId('dynamic-config-form:parallelEnsemble.runners.responseLevel'),
      ).not.toBeInTheDocument()
    })

    it('renders the runner_config form for a runner with a non-empty ui_schema', () => {
      const runners = [
        buildRunner({
          name: 'token_step',
          i18n_key_prefix: 'parallelEnsemble.runners.tokenStep',
          aggregator_scope: 'token',
          ui_schema: { top_k: { control: 'number_input' } },
        }),
      ]
      mockUseConfig.mockReturnValue(buildConfig({
        runners,
        inputs: buildPayload({
          ensemble: {
            question_variable: ['start', 'q'],
            model_aliases: ['llama3-local'],
            runner_name: 'token_step',
            runner_config: {},
            aggregator_name: '',
            aggregator_config: {},
            diagnostics: { ...DEFAULT_DIAGNOSTICS },
          },
        }),
      }))
      renderPanel()
      expect(
        screen.getByTestId('dynamic-config-form:parallelEnsemble.runners.tokenStep'),
      ).toBeInTheDocument()
    })
  })

  describe('Routing — child callbacks fire the right handlers', () => {
    it('forwards model alias mutations to handleModelAliasesChange', () => {
      const config = buildConfig()
      mockUseConfig.mockReturnValue(config)
      renderPanel()

      fireEvent.click(screen.getByText('change-models'))
      expect(config.handleModelAliasesChange).toHaveBeenCalledWith([
        'llama3-local',
        'qwen-local',
      ])
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

    it('forwards question variable picks to handleQuestionVariableChange', () => {
      const config = buildConfig()
      mockUseConfig.mockReturnValue(config)
      renderPanel()

      fireEvent.click(screen.getByText('pick-question'))
      expect(config.handleQuestionVariableChange).toHaveBeenCalledWith(['start', 'question'])
    })

    it('merges the imported aliases with the existing selection', () => {
      // The panel's import handler concatenates ``existingAliases``
      // with the file's payload before forwarding — never replaces.
      // ``handleModelAliasesChange`` de-dupes downstream.
      const config = buildConfig()
      mockUseConfig.mockReturnValue(config)
      renderPanel()

      fireEvent.click(screen.getByTestId('import-button'))
      expect(config.handleModelAliasesChange).toHaveBeenCalledWith([
        'llama3-local',
        'imported-model',
      ])
    })
  })
})
