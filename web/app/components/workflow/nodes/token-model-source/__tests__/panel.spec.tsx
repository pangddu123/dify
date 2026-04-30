import type { TokenModelSourceNodeType } from '../types'
import type { NodePanelProps } from '@/app/components/workflow/types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BlockEnum } from '@/app/components/workflow/types'
import Panel from '../panel'
import { DEFAULT_SAMPLING_PARAMS } from '../types'

// ── Hoisted mocks ───────────────────────────────────────────────────
//
// The panel composes ``ModelAliasSelect`` + ``SamplingParamsForm`` and
// wires them to ``useConfig``. We stub each child as a thin
// pass-through so the test asserts the panel's *wiring* (which child
// gets which slice) rather than re-testing the children's internals.

const mockUseConfig = vi.hoisted(() => vi.fn())

vi.mock('../use-config', () => ({
  __esModule: true,
  default: (...args: unknown[]) => mockUseConfig(...args),
}))

type AliasMockProps = {
  selected: string
  models: ReadonlyArray<{ id: string }>
  isLoading?: boolean
  readonly?: boolean
  onChange: (next: string) => void
}

vi.mock('../components/model-alias-select', () => ({
  __esModule: true,
  default: ({ selected, models, isLoading, readonly, onChange }: AliasMockProps) => (
    <div data-testid="model-alias-select">
      <span data-testid="alias-active">{selected}</span>
      <span data-testid="alias-count">{models.length}</span>
      {/* Surface forwarded props so the panel test can assert wiring
          end-to-end (real selector behaviour is in
          ``model-alias-select.spec.tsx``). */}
      <span data-testid="alias-loading">{String(Boolean(isLoading))}</span>
      <span data-testid="alias-readonly">{String(Boolean(readonly))}</span>
      <button type="button" onClick={() => onChange('qwen3-4b')}>
        change-alias
      </button>
    </div>
  ),
}))

type SamplingMockProps = {
  value: typeof DEFAULT_SAMPLING_PARAMS
  readonly: boolean
  onChange: (patch: Partial<typeof DEFAULT_SAMPLING_PARAMS>) => void
}

vi.mock('../components/sampling-params-form', () => ({
  __esModule: true,
  default: ({ value, readonly, onChange }: SamplingMockProps) => (
    <div data-testid="sampling-form">
      <span data-testid="sampling-top-k">{value.top_k}</span>
      <span data-testid="sampling-readonly">{String(readonly)}</span>
      <button type="button" onClick={() => onChange({ top_k: 5 })}>
        change-sampling
      </button>
    </div>
  ),
}))

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

const buildPayload = (
  overrides: Partial<TokenModelSourceNodeType> = {},
): TokenModelSourceNodeType => ({
  title: 'Token Model Source',
  desc: '',
  type: BlockEnum.TokenModelSource,
  model_alias: 'llama3-local',
  prompt_template: 'Answer: {{#start.q#}}',
  sampling_params: { ...DEFAULT_SAMPLING_PARAMS },
  extra: {},
  ...overrides,
})

const buildModels = () => [
  { id: 'llama3-local', backend: 'llama_cpp', model_name: 'llama3-8b', capabilities: ['token_step'], metadata: {} },
  { id: 'qwen3-4b', backend: 'llama_cpp', model_name: 'qwen3-4b', capabilities: ['token_step'], metadata: {} },
]

const buildConfig = (overrides: Partial<{
  inputs: TokenModelSourceNodeType
  models: ReturnType<typeof buildModels>
  isLoadingModels: boolean
  readOnly: boolean
  handleModelAliasChange: ReturnType<typeof vi.fn>
  handlePromptTemplateChange: ReturnType<typeof vi.fn>
  handleSamplingParamsChange: ReturnType<typeof vi.fn>
  handleExtraChange: ReturnType<typeof vi.fn>
}> = {}) => ({
  readOnly: overrides.readOnly ?? false,
  inputs: overrides.inputs ?? buildPayload(),
  models: overrides.models ?? buildModels(),
  isLoadingModels: overrides.isLoadingModels ?? false,
  handleModelAliasChange: overrides.handleModelAliasChange ?? vi.fn(),
  handlePromptTemplateChange: overrides.handlePromptTemplateChange ?? vi.fn(),
  handleSamplingParamsChange: overrides.handleSamplingParamsChange ?? vi.fn(),
  handleExtraChange: overrides.handleExtraChange ?? vi.fn(),
})

const renderPanel = (data: TokenModelSourceNodeType = buildPayload()) => {
  const props: NodePanelProps<TokenModelSourceNodeType> = {
    id: 'token-model-source-1',
    data,
  } as unknown as NodePanelProps<TokenModelSourceNodeType>
  return render(<Panel {...props} />)
}

describe('token-model-source/panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Rendering — section composition', () => {
    it('renders all configuration sections for a populated payload', () => {
      mockUseConfig.mockReturnValue(buildConfig())
      renderPanel()

      expect(screen.getByTestId('model-alias-select')).toBeInTheDocument()
      expect(screen.getByTestId('sampling-form')).toBeInTheDocument()
      expect(screen.getByTestId('output-vars')).toBeInTheDocument()
    })

    it('passes the selected alias and the model list count to the alias selector', () => {
      mockUseConfig.mockReturnValue(buildConfig())
      renderPanel()

      expect(screen.getByTestId('alias-active').textContent).toBe('llama3-local')
      expect(screen.getByTestId('alias-count').textContent).toBe('2')
    })

    it('passes the current sampling slice to SamplingParamsForm', () => {
      mockUseConfig.mockReturnValue(buildConfig({
        inputs: buildPayload({
          sampling_params: { ...DEFAULT_SAMPLING_PARAMS, top_k: 7 },
        }),
      }))
      renderPanel()

      expect(screen.getByTestId('sampling-top-k').textContent).toBe('7')
    })

    it('renders both output-var items declared by the backend node', () => {
      mockUseConfig.mockReturnValue(buildConfig())
      renderPanel()

      // ``spec`` (object) and ``model_alias`` (string) — the two
      // outputs ``TokenModelSourceNode._run`` emits.
      expect(screen.getByTestId('var-item-spec')).toBeInTheDocument()
      expect(screen.getByTestId('var-item-model_alias')).toBeInTheDocument()
    })

    it('renders the prompt textarea with the current template', () => {
      mockUseConfig.mockReturnValue(buildConfig({
        inputs: buildPayload({ prompt_template: 'Hello {{#start.name#}}' }),
      }))
      renderPanel()
      const textarea = document.querySelector('textarea') as HTMLTextAreaElement
      expect(textarea).not.toBeNull()
      expect(textarea.value).toBe('Hello {{#start.name#}}')
    })
  })

  describe('Wiring — event → handler', () => {
    it('invokes handleModelAliasChange when the selector emits a new alias', () => {
      const handleModelAliasChange = vi.fn()
      mockUseConfig.mockReturnValue(buildConfig({ handleModelAliasChange }))
      renderPanel()

      fireEvent.click(screen.getByText('change-alias'))
      expect(handleModelAliasChange).toHaveBeenCalledTimes(1)
      expect(handleModelAliasChange).toHaveBeenCalledWith('qwen3-4b')
    })

    it('invokes handleSamplingParamsChange when the sampling form emits a patch', () => {
      const handleSamplingParamsChange = vi.fn()
      mockUseConfig.mockReturnValue(buildConfig({ handleSamplingParamsChange }))
      renderPanel()

      fireEvent.click(screen.getByText('change-sampling'))
      expect(handleSamplingParamsChange).toHaveBeenCalledTimes(1)
      expect(handleSamplingParamsChange).toHaveBeenCalledWith({ top_k: 5 })
    })

    it('invokes handlePromptTemplateChange when the textarea changes', () => {
      const handlePromptTemplateChange = vi.fn()
      mockUseConfig.mockReturnValue(buildConfig({ handlePromptTemplateChange }))
      renderPanel()

      const textarea = document.querySelector('textarea') as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: 'New prompt' } })
      expect(handlePromptTemplateChange).toHaveBeenCalledWith('New prompt')
    })
  })

  describe('Forwarded flags', () => {
    it('forwards isLoadingModels into the alias selector', () => {
      // Real selector behaviour ("Loading…" label, disabled trigger)
      // is covered by ``model-alias-select.spec.tsx``; here we just
      // pin the wiring so the panel can't accidentally hold the flag
      // back from the child.
      mockUseConfig.mockReturnValue(buildConfig({ isLoadingModels: true }))
      renderPanel()
      expect(screen.getByTestId('alias-loading').textContent).toBe('true')
    })

    it('forwards readOnly into both the alias selector and the sampling form', () => {
      mockUseConfig.mockReturnValue(buildConfig({ readOnly: true }))
      renderPanel()
      expect(screen.getByTestId('alias-readonly').textContent).toBe('true')
      expect(screen.getByTestId('sampling-readonly').textContent).toBe('true')
      // The prompt textarea is owned by the panel itself — assert
      // it disables in the same readonly state.
      const textarea = document.querySelector('textarea') as HTMLTextAreaElement
      expect(textarea.disabled).toBe(true)
    })

    it('forwards an empty models list to the alias selector', () => {
      // Empty registry is the "no aliases in model_net.yaml" case —
      // the panel must pass it through verbatim so the child can
      // render its empty-state hint.
      mockUseConfig.mockReturnValue(buildConfig({ models: [] }))
      renderPanel()
      expect(screen.getByTestId('alias-count').textContent).toBe('0')
    })
  })
})
