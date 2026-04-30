import type { BackendInfo } from '../../../parallel-ensemble/types'
import { fireEvent, render, screen } from '@testing-library/react'
import * as React from 'react'
import { describe, expect, it, vi } from 'vitest'
import ModelAliasSelect from '../model-alias-select'

// dify-ui Tooltip / DropdownMenu ship with a portal; the test
// environment renders them inline so jsdom queries can find the
// items without managing the portal root.

const buildModels = (): BackendInfo[] => [
  {
    id: 'llama3-local',
    backend: 'llama_cpp',
    model_name: 'llama3-8b',
    capabilities: ['response_level', 'token_step'],
    metadata: {},
  },
  {
    id: 'gpt-4o-mini',
    backend: 'openai_compat',
    model_name: 'gpt-4o-mini',
    // No ``token_step`` — incompatible alias. Stays visible (greyed)
    // so the user understands *why* it can't be picked.
    capabilities: ['response_level'],
    metadata: {},
  },
]

const renderSelect = (overrides: Partial<{
  readonly: boolean
  isLoading: boolean
  models: BackendInfo[]
  selected: string
  onChange: (next: string) => void
}> = {}) => {
  const onChange = overrides.onChange ?? vi.fn()
  const result = render(
    <ModelAliasSelect
      readonly={overrides.readonly ?? false}
      isLoading={overrides.isLoading ?? false}
      models={overrides.models ?? buildModels()}
      selected={overrides.selected ?? ''}
      onChange={onChange}
    />,
  )
  return { ...result, onChange }
}

const openMenu = () => {
  // The trigger is the only ``<button>`` rendered before the menu
  // opens; clicking it surfaces the items in jsdom.
  const trigger = document.querySelector('button') as HTMLButtonElement
  fireEvent.click(trigger)
}

describe('token-model-source/model-alias-select', () => {
  describe('Trigger label', () => {
    it('renders the placeholder when nothing is selected', () => {
      renderSelect()
      // i18n is stubbed to return ``${ns}.${key}:${jsonParams}`` —
      // assert by substring rather than exact text since the params
      // suffix carries the defaultValue blob too.
      expect(
        screen.getByText(/modelAliasPlaceholder/),
      ).toBeInTheDocument()
    })

    it('renders the selected alias when one is bound', () => {
      renderSelect({ selected: 'llama3-local' })
      expect(screen.getByText('llama3-local')).toBeInTheDocument()
    })

    it('renders "Loading…" while the local-models query is in flight', () => {
      renderSelect({ isLoading: true })
      expect(screen.getByText(/common\.loading/)).toBeInTheDocument()
    })

    it('disables the trigger when readonly', () => {
      renderSelect({ readonly: true, selected: 'llama3-local' })
      const trigger = document.querySelector('button') as HTMLButtonElement
      expect(trigger.disabled).toBe(true)
    })

    it('disables the trigger while loading', () => {
      renderSelect({ isLoading: true })
      const trigger = document.querySelector('button') as HTMLButtonElement
      expect(trigger.disabled).toBe(true)
    })
  })

  describe('Menu — empty state', () => {
    it('renders the "no models registered" hint when models list is empty', () => {
      renderSelect({ models: [] })
      openMenu()
      expect(screen.getByText(/noModelsAvailable/)).toBeInTheDocument()
    })
  })

  describe('Menu — selection', () => {
    it('emits the picked alias when a compatible row is clicked', () => {
      const { onChange } = renderSelect()
      openMenu()
      fireEvent.click(screen.getByText('llama3-local'))
      expect(onChange).toHaveBeenCalledWith('llama3-local')
    })

    it('does not emit when an incompatible row is newly clicked', () => {
      // ``gpt-4o-mini`` lacks token_step → the tooltip-wrapped row
      // is greyed and clicks are gated. ``onChange`` must stay
      // unfired so the user can't smuggle an incompatible alias in
      // via the dropdown alone.
      const { onChange } = renderSelect()
      openMenu()
      fireEvent.click(screen.getByText('gpt-4o-mini'))
      expect(onChange).not.toHaveBeenCalled()
    })

    it('still emits when an already-selected incompatible alias is clicked (deselect path)', () => {
      // If ``model_net.yaml`` drops a capability between sessions
      // and an existing DSL is now incompatible, the user must be
      // able to deselect it. Mirrors parallel-ensemble policy.
      const { onChange } = renderSelect({ selected: 'gpt-4o-mini' })
      openMenu()
      // The trigger label *also* renders the selected alias, so
      // ``getByText`` would match twice. Pick the menu row by
      // walking from the secondary-text sibling that only the menu
      // items render (``backend · model_name``).
      const subline = screen.getByText(/openai_compat · gpt-4o-mini/)
      const row = subline.closest('[role="menuitem"]') as HTMLElement | null
      expect(row).not.toBeNull()
      fireEvent.click(row!)
      expect(onChange).toHaveBeenCalledWith('gpt-4o-mini')
    })
  })
})
