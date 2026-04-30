import type {
  AggregatorMeta,
  ParallelEnsembleNodeType,
  RunnerMeta,
  TokenSourceRef,
} from '../types'
import type { NodePanelProps, Var } from '@/app/components/workflow/types'
import { render, renderHook, screen } from '@testing-library/react'
import * as React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BlockEnum, VarType } from '@/app/components/workflow/types'
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

// ``./use-registries`` calls into TanStack Query against
// ``consoleQuery.parallelEnsemble.*``. Replace with deterministic
// results so the validation surface can be driven without standing up
// the query client and contract pipeline. After ADR-v3-16 the panel no
// longer needs the local-models registry — capability checks moved to
// the §9 startup pipeline server-side, where the spec can be resolved.
const registryState = vi.hoisted(() => ({
  runners: [] as RunnerMeta[],
  aggregators: [] as AggregatorMeta[],
}))

vi.mock('../use-registries', () => ({
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
vi.mock('../components/token-source-list', () => ({
  __esModule: true,
  default: () => <div data-testid="token-source-list" />,
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
vi.mock('@/app/components/workflow/nodes/_base/components/output-vars', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  VarItem: ({ name }: { name: string }) => <div>{name}</div>,
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
    runner_name: DEFAULT_RUNNER_NAME,
    runner_config: {},
    aggregator_name: DEFAULT_AGGREGATOR_NAME,
    aggregator_config: {},
    diagnostics: { ...DEFAULT_DIAGNOSTICS },
  },
  ...overrides,
})

const setRegistry = (state: Partial<typeof registryState>) => {
  registryState.runners = state.runners ?? []
  registryState.aggregators = state.aggregators ?? []
}

describe('parallel-ensemble/requirements-validation', () => {
  beforeEach(() => {
    setRegistry({})
  })

  describe('useConfig.validationIssues — scope match', () => {
    // The §9 startup pipeline rejects mismatched runner / aggregator
    // pairs server-side; the panel mirrors the same check so saved
    // DSL never lands in a state that later fails at run time. With
    // ADR-v3-16 this is the only static check the panel can run —
    // capability-mismatch checks moved server-side because the panel
    // can't resolve spec selectors against a live variable pool.
    it('flags a scope mismatch between selected runner and aggregator', () => {
      setRegistry({
        runners: [
          buildRunner({
            name: 'token_step',
            aggregator_scope: 'token',
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
          token_sources: [buildSource()],
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
        runners: [
          buildRunner({ name: 'token_step', aggregator_scope: 'token' }),
        ],
        aggregators: [
          buildAggregator({ name: 'sum_score', scope: 'token' }),
        ],
      })

      const payload = buildPayload({
        ensemble: {
          token_sources: [buildSource()],
          runner_name: 'token_step',
          runner_config: {},
          aggregator_name: 'sum_score',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })

      const { result } = renderHook(() => useConfig('node-1', payload))
      expect(result.current.validationIssues).toHaveLength(0)
    })

    it('returns no issues when no runner is selected', () => {
      setRegistry({ runners: [], aggregators: [] })

      const payload = buildPayload({
        ensemble: {
          token_sources: [buildSource()],
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

  describe('useConfig.validationIssues — registry membership', () => {
    // Registry-existence checks guard against stale DSL pasted from
    // another deployment whose registry has different runner /
    // aggregator names — without this guard the user would only see
    // the failure at run time.
    it('flags an unknown runner when the registry has loaded', () => {
      setRegistry({
        runners: [buildRunner({ name: 'token_step' })],
        aggregators: [buildAggregator({ name: 'sum_score' })],
      })
      const payload = buildPayload({
        ensemble: {
          token_sources: [buildSource()],
          runner_name: 'unknown_runner',
          runner_config: {},
          aggregator_name: 'sum_score',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })
      const { result } = renderHook(() => useConfig('node-1', payload))
      expect(result.current.validationIssues).toContainEqual(
        expect.objectContaining({
          severity: 'error',
          field: 'runner_name',
          i18n_key: 'parallelEnsemble.errors.runnerNotRegistered',
        }),
      )
    })

    it('flags an unknown aggregator when the registry has loaded', () => {
      setRegistry({
        runners: [buildRunner({ name: 'token_step' })],
        aggregators: [buildAggregator({ name: 'sum_score' })],
      })
      const payload = buildPayload({
        ensemble: {
          token_sources: [buildSource()],
          runner_name: 'token_step',
          runner_config: {},
          aggregator_name: 'unknown_aggregator',
          aggregator_config: {},
          diagnostics: { ...DEFAULT_DIAGNOSTICS },
        },
      })
      const { result } = renderHook(() => useConfig('node-1', payload))
      expect(result.current.validationIssues).toContainEqual(
        expect.objectContaining({
          severity: 'error',
          field: 'aggregator_name',
          i18n_key: 'parallelEnsemble.errors.aggregatorNotRegistered',
        }),
      )
    })
  })

  describe('useConfig.filterSpecVar — outputs.spec only', () => {
    // The token-mode contract requires every source to reference an
    // upstream ``token-model-source`` node's ``outputs.spec`` field —
    // any other object variable would crash at run time inside
    // ``ModelInvocationSpec.model_validate``. Filter must reject those
    // at edit time so the picker can't surface them.
    it('accepts an object variable selector ending in "spec"', () => {
      const payload = buildPayload()
      const { result } = renderHook(() => useConfig('node-1', payload))
      const v: Var = { variable: 'spec', type: VarType.object } as Var
      expect(result.current.filterSpecVar(v, ['source_node', 'spec'])).toBe(true)
    })

    it('rejects an object variable whose selector does not end in "spec"', () => {
      const payload = buildPayload()
      const { result } = renderHook(() => useConfig('node-1', payload))
      const v: Var = { variable: 'body', type: VarType.object } as Var
      expect(result.current.filterSpecVar(v, ['http_request', 'body'])).toBe(false)
    })

    it('rejects non-object variables even if the selector tail is "spec"', () => {
      const payload = buildPayload()
      const { result } = renderHook(() => useConfig('node-1', payload))
      const v: Var = { variable: 'spec', type: VarType.string } as Var
      expect(result.current.filterSpecVar(v, ['source_node', 'spec'])).toBe(false)
    })
  })

  describe('Panel rendering — ValidationIssue surface', () => {
    it('renders the scope-mismatch issue under the aggregator_name field', () => {
      setRegistry({
        runners: [
          buildRunner({ name: 'token_step', aggregator_scope: 'token' }),
        ],
        aggregators: [
          buildAggregator({ name: 'majority_vote', scope: 'response' }),
        ],
      })
      const payload = buildPayload({
        ensemble: {
          token_sources: [buildSource()],
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
      render(<Panel {...props} />)

      const scopeIssue = screen.getByText(/parallelEnsemble\.errors\.aggregatorScopeMismatch/)
      expect(scopeIssue).toBeInTheDocument()
      expect(scopeIssue.className).toContain('text-text-warning-secondary')
    })
  })
})
