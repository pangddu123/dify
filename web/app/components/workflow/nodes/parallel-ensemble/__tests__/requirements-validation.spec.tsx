import type {
  AggregatorMeta,
  BackendInfo,
  ParallelEnsembleNodeType,
  RunnerMeta,
  ValidationIssue,
} from '../types'
import type { NodePanelProps } from '@/app/components/workflow/types'
import { render, renderHook, screen } from '@testing-library/react'
import * as React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BlockEnum } from '@/app/components/workflow/types'
import Panel from '../panel'
import {
  DEFAULT_AGGREGATOR_NAME,
  DEFAULT_DIAGNOSTICS,
  DEFAULT_RUNNER_NAME,
} from '../types'
import useConfig from '../use-config'

// ── Hoisted mocks ───────────────────────────────────────────────────
//
// ``useNodesReadOnly`` reads from the workflow store; the real one
// requires the full provider stack — we don't need read-only behaviour
// in this spec.
vi.mock('@/app/components/workflow/hooks', () => ({
  useNodesReadOnly: () => ({ nodesReadOnly: false }),
}))

const mockSetInputs = vi.hoisted(() => vi.fn())
vi.mock('@/app/components/workflow/nodes/_base/hooks/use-node-crud', () => ({
  __esModule: true as const,
  default: <T,>(_id: string, data: T) => ({ inputs: data, setInputs: mockSetInputs }),
}))

// ``./use-registries`` calls into TanStack Query against ``console
// Query.parallelEnsemble.*``. Replace with deterministic results so
// the validation surface can be driven without standing up the query
// client and contract pipeline.
const registryState = vi.hoisted(() => ({
  models: [] as BackendInfo[],
  runners: [] as RunnerMeta[],
  aggregators: [] as AggregatorMeta[],
}))

vi.mock('../use-registries', () => ({
  useLocalModels: () => ({
    data: { models: registryState.models },
    isLoading: false,
  }),
  useRunners: () => ({
    data: { runners: registryState.runners },
    isLoading: false,
  }),
  useAggregators: () => ({
    data: { aggregators: registryState.aggregators },
    isLoading: false,
  }),
}))

// Panel-level integration uses simple stubs for the children — this
// spec focuses on validation surface rendering, not selector internals.
vi.mock('@/app/components/workflow/nodes/_base/components/variable/var-reference-picker', () => ({
  __esModule: true,
  default: () => <div data-testid="var-picker" />,
}))
vi.mock('../components/model-selector', () => ({
  __esModule: true,
  default: () => <div data-testid="model-selector" />,
}))
vi.mock('../components/runner-selector', () => ({
  __esModule: true,
  default: () => <div data-testid="runner-selector" />,
}))
vi.mock('../components/aggregator-selector', () => ({
  __esModule: true,
  default: () => <div data-testid="aggregator-selector" />,
}))
vi.mock('../components/dynamic-config-form', () => ({
  __esModule: true,
  default: () => <div data-testid="dynamic-config-form" />,
}))
vi.mock('../components/diagnostics-config', () => ({
  __esModule: true,
  default: () => <div data-testid="diagnostics-config" />,
}))
vi.mock('../components/import-model-info-button', () => ({
  __esModule: true,
  default: () => <button type="button" data-testid="import-button">import</button>,
}))
vi.mock('@/app/components/workflow/nodes/_base/components/output-vars', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  VarItem: ({ name }: { name: string }) => <div>{name}</div>,
}))

// ── Builders ────────────────────────────────────────────────────────

const buildModel = (overrides: Partial<BackendInfo> = {}): BackendInfo => ({
  id: 'llama3-local',
  backend: 'llama_cpp',
  model_name: 'llama3-8b',
  capabilities: ['response_level'],
  metadata: {},
  ...overrides,
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
    question_variable: ['start', 'q'],
    model_aliases: [],
    runner_name: DEFAULT_RUNNER_NAME,
    runner_config: {},
    aggregator_name: DEFAULT_AGGREGATOR_NAME,
    aggregator_config: {},
    diagnostics: { ...DEFAULT_DIAGNOSTICS },
  },
  ...overrides,
})

const setRegistry = (state: Partial<typeof registryState>) => {
  registryState.models = state.models ?? []
  registryState.runners = state.runners ?? []
  registryState.aggregators = state.aggregators ?? []
}

describe('parallel-ensemble/requirements-validation', () => {
  beforeEach(() => {
    setRegistry({})
  })

  describe('useConfig.validationIssues — capability subset', () => {
    // P2.12 spec: mirror the runner's ``required_capabilities`` ⊆
    // model.capabilities check that ``runner_cls.validate_selection``
    // does at run time. The panel surfaces issues field-by-field.
    it('flags an alias whose backend lacks a required capability', () => {
      setRegistry({
        models: [
          buildModel({ id: 'llama3-local', capabilities: ['response_level', 'token_step'] }),
          buildModel({ id: 'claude-cloud', backend: 'anthropic', capabilities: ['response_level'] }),
        ],
        runners: [
          buildRunner({
            name: 'token_step',
            aggregator_scope: 'token',
            required_capabilities: ['token_step'],
          }),
        ],
        aggregators: [],
      })

      const payload = buildPayload({
        ensemble: {
          question_variable: ['start', 'q'],
          model_aliases: ['llama3-local', 'claude-cloud'],
          runner_name: 'token_step',
          runner_config: {},
          aggregator_name: '',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })

      const { result } = renderHook(() => useConfig('node-1', payload))

      const issues = result.current.validationIssues
      expect(issues).toHaveLength(1)
      expect(issues[0]).toMatchObject({
        severity: 'error',
        field: 'model_aliases',
        i18n_key: 'parallelEnsemble.errors.modelMissingCapability',
      })
      expect(issues[0]!.message).toMatch(/claude-cloud/)
    })

    it('does not flag aliases when no capability is required', () => {
      setRegistry({
        models: [buildModel({ id: 'llama3-local' })],
        runners: [buildRunner({ name: 'response_level' })],
        aggregators: [],
      })

      const payload = buildPayload({
        ensemble: {
          question_variable: ['start', 'q'],
          model_aliases: ['llama3-local'],
          runner_name: 'response_level',
          runner_config: {},
          aggregator_name: '',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })

      const { result } = renderHook(() => useConfig('node-1', payload))
      expect(result.current.validationIssues).toHaveLength(0)
    })

    it('skips aliases that are not registered in the local-models projection', () => {
      // An alias not in the registry yields no model object — the
      // capability check skips it (the missing-alias error is the
      // §9 startup pipeline's responsibility, not this static check).
      setRegistry({
        models: [],
        runners: [
          buildRunner({
            name: 'token_step',
            aggregator_scope: 'token',
            required_capabilities: ['token_step'],
          }),
        ],
        aggregators: [],
      })

      const payload = buildPayload({
        ensemble: {
          question_variable: ['start', 'q'],
          model_aliases: ['phantom-model'],
          runner_name: 'token_step',
          runner_config: {},
          aggregator_name: '',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })

      const { result } = renderHook(() => useConfig('node-1', payload))
      expect(result.current.validationIssues).toHaveLength(0)
    })
  })

  describe('useConfig.validationIssues — scope match', () => {
    // The §9 startup pipeline rejects mismatched runner / aggregator
    // pairs server-side; the panel mirrors the same check so saved
    // DSL never lands in a state that later fails at run time.
    it('flags a scope mismatch between selected runner and aggregator', () => {
      setRegistry({
        models: [],
        runners: [
          buildRunner({
            name: 'token_step',
            aggregator_scope: 'token',
            required_capabilities: [],
          }),
        ],
        aggregators: [
          buildAggregator({
            name: 'majority_vote',
            scope: 'response',
          }),
        ],
      })

      const payload = buildPayload({
        ensemble: {
          question_variable: ['start', 'q'],
          model_aliases: [],
          runner_name: 'token_step',
          runner_config: {},
          aggregator_name: 'majority_vote',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })

      const { result } = renderHook(() => useConfig('node-1', payload))
      const issues = result.current.validationIssues
      expect(issues).toHaveLength(1)
      expect(issues[0]).toMatchObject({
        severity: 'error',
        field: 'aggregator_name',
        i18n_key: 'parallelEnsemble.errors.aggregatorScopeMismatch',
      })
    })

    it('does not flag a matching scope pairing', () => {
      setRegistry({
        models: [],
        runners: [
          buildRunner({
            name: 'token_step',
            aggregator_scope: 'token',
          }),
        ],
        aggregators: [
          buildAggregator({
            name: 'token_logit_mean',
            scope: 'token',
          }),
        ],
      })

      const payload = buildPayload({
        ensemble: {
          question_variable: ['start', 'q'],
          model_aliases: [],
          runner_name: 'token_step',
          runner_config: {},
          aggregator_name: 'token_logit_mean',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })

      const { result } = renderHook(() => useConfig('node-1', payload))
      expect(result.current.validationIssues).toHaveLength(0)
    })

    it('returns no issues when no runner is selected', () => {
      // Without a selectedRunner, neither check can run — the panel
      // already prevents save via the per-field placeholder + the
      // required runner_name field in default.checkValid.
      setRegistry({
        models: [buildModel({ id: 'llama3-local' })],
        runners: [],
        aggregators: [],
      })

      const payload = buildPayload({
        ensemble: {
          question_variable: ['start', 'q'],
          model_aliases: ['llama3-local'],
          runner_name: 'judge',
          runner_config: {},
          aggregator_name: '',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })

      const { result } = renderHook(() => useConfig('node-1', payload))
      expect(result.current.validationIssues).toHaveLength(0)
    })
  })

  describe('Panel rendering — ValidationIssue surface', () => {
    // The panel's ``renderIssue`` helper renders error issues with
    // ``text-text-warning-secondary`` (the design-token red surface
    // referenced by the P2.12 spec). The text resolves through the
    // i18n stub which returns the raw key — sufficient for asserting
    // routing.
    const renderPanelWith = (issues: ValidationIssue[]) => {
      // Drive the issues through the real ``useConfig`` by setting up
      // the registry to produce them via the static rules.
      vi.doUnmock('../use-config')

      // Stub the panel's local ``useConfig`` ref via the registry so
      // we exercise the actual issue-routing branch.
      // Capability mismatch → model_aliases field; scope mismatch →
      // aggregator_name field. Fixture below targets one of each.
      setRegistry({
        models: [
          buildModel({ id: 'incompatible-model', capabilities: ['response_level'] }),
        ],
        runners: [
          buildRunner({
            name: 'token_step',
            aggregator_scope: 'token',
            required_capabilities: ['token_step'],
          }),
        ],
        aggregators: [
          buildAggregator({
            name: 'majority_vote',
            scope: 'response',
          }),
        ],
      })
      const payload = buildPayload({
        ensemble: {
          question_variable: ['start', 'q'],
          model_aliases: ['incompatible-model'],
          runner_name: 'token_step',
          runner_config: {},
          aggregator_name: 'majority_vote',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })

      const props = {
        id: 'node-1',
        data: payload,
      } as unknown as NodePanelProps<ParallelEnsembleNodeType>
      const out = render(<Panel {...props} />)
      return { out, issues }
    }

    it('renders capability and scope issues under their respective field surfaces', () => {
      renderPanelWith([])

      // The capability mismatch issue resolves under the
      // ``model_aliases`` field; the scope mismatch under
      // ``aggregator_name``. Both keys render through the i18n stub
      // which returns "<ns>.<key>:<paramsJSON>".
      const capIssue = screen.getByText(/parallelEnsemble\.errors\.modelMissingCapability/)
      const scopeIssue = screen.getByText(/parallelEnsemble\.errors\.aggregatorScopeMismatch/)

      expect(capIssue).toBeInTheDocument()
      expect(scopeIssue).toBeInTheDocument()

      // Both issues use the warning-text token — that's the visual
      // hook the design system exposes for "field has an error".
      expect(capIssue.className).toContain('text-text-warning-secondary')
      expect(scopeIssue.className).toContain('text-text-warning-secondary')
    })
  })
})
