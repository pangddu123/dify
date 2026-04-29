import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import * as React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ImportModelInfoButton from '../import-model-info-button'

const toastSuccess = vi.hoisted(() => vi.fn())
const toastError = vi.hoisted(() => vi.fn())

vi.mock('@langgenius/dify-ui/toast', () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
  },
}))

// dify-ui Button is a styled wrapper — replace with a plain button
// so the test can drive it via getByRole('button', ...) without
// pulling in Radix internals.
type MockButtonProps = {
  children: React.ReactNode
  onClick?: React.MouseEventHandler<HTMLButtonElement>
  disabled?: boolean
}
vi.mock('@langgenius/dify-ui/button', () => ({
  Button: ({ children, onClick, disabled }: MockButtonProps) => (
    <button type="button" onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
}))

const buildModelInfoFile = () => ({
  // Realistic ``model_info.json`` payload from PN.py — each entry
  // carries id + non-id metadata. The non-id keys are the SSRF /
  // credential surface that must NEVER reach the panel.
  llama3: {
    id: 'llama3-local',
    url: 'http://192.168.1.10:8080',
    api_key: 'super-secret',
    api_key_env: 'LLAMA_KEY',
    EOS: '<|eot_id|>',
    stop_think: '</think>',
  },
  qwen: {
    id: 'qwen-local',
    url: 'http://10.0.0.5:8080',
    api_key: 'another-secret',
  },
  unknown: {
    id: 'phantom-model',
    url: 'http://example.com/api',
  },
  malformed_no_id: {
    url: 'http://nope/',
  },
  // Non-object values must be ignored without crashing the parser.
  noise: 'random string',
})

const knownAliases = ['llama3-local', 'qwen-local']

const fileFromObject = (value: unknown) =>
  new File([JSON.stringify(value)], 'model_info.json', {
    type: 'application/json',
  })

describe('parallel-ensemble/import-model-info-button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    // Restore native console.warn — the component logs parse failures
    // so the spy in the catch-branch test doesn't leak.
    vi.restoreAllMocks()
  })

  describe('Click → file picker', () => {
    it('forwards the click to the hidden file input', () => {
      render(
        <ImportModelInfoButton
          readonly={false}
          knownAliases={knownAliases}
          onImport={vi.fn()}
        />,
      )

      const button = screen.getByRole('button')
      const fileInput = button.parentElement!.querySelector('input[type="file"]') as HTMLInputElement
      const inputClick = vi.spyOn(fileInput, 'click')

      fireEvent.click(button)
      expect(inputClick).toHaveBeenCalledTimes(1)
    })

    it('disables the button when readonly is true', () => {
      render(
        <ImportModelInfoButton
          readonly
          knownAliases={knownAliases}
          onImport={vi.fn()}
        />,
      )

      expect(screen.getByRole('button')).toBeDisabled()
    })

    // Without the loading-disable, an early click before the model
    // registry resolves would see ``knownAliases === []`` and surface a
    // misleading "noneMatched" toast for every imported id.
    it('disables the button while the model registry is loading', () => {
      render(
        <ImportModelInfoButton
          readonly={false}
          isLoading
          knownAliases={[]}
          onImport={vi.fn()}
        />,
      )

      expect(screen.getByRole('button')).toBeDisabled()
    })
  })

  describe('Parsing — id-only surface (P2.12 SSRF boundary)', () => {
    // Core P2.12 spec: only the ``id`` field of each entry is
    // forwarded. urls / api_keys / api_key_envs / EOS / stop_think
    // are stripped client-side before the alias list reaches NodeData.
    it('imports only the id field of matching aliases', async () => {
      const onImport = vi.fn()
      render(
        <ImportModelInfoButton
          readonly={false}
          knownAliases={knownAliases}
          onImport={onImport}
        />,
      )

      const fileInput = screen.getByRole('button').parentElement!.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(fileInput, {
        target: { files: [fileFromObject(buildModelInfoFile())] },
      })

      await waitFor(() => expect(onImport).toHaveBeenCalled())

      // Only the two ids that match ``knownAliases`` survive — the
      // ``phantom-model`` id is dropped because it isn't registered,
      // and ``malformed_no_id`` / ``noise`` produce no ids at all.
      expect(onImport).toHaveBeenCalledWith(['llama3-local', 'qwen-local'])
    })

    it('drops aliases not registered in the backend yaml', async () => {
      const onImport = vi.fn()
      render(
        <ImportModelInfoButton
          readonly={false}
          knownAliases={['llama3-local']}
          onImport={onImport}
        />,
      )

      const fileInput = screen.getByRole('button').parentElement!.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(fileInput, {
        target: { files: [fileFromObject(buildModelInfoFile())] },
      })

      await waitFor(() => expect(onImport).toHaveBeenCalled())
      expect(onImport).toHaveBeenCalledWith(['llama3-local'])
    })

    it('reports the dropped count via a success toast', async () => {
      const onImport = vi.fn()
      render(
        <ImportModelInfoButton
          readonly={false}
          knownAliases={['llama3-local']}
          onImport={onImport}
        />,
      )

      const fileInput = screen.getByRole('button').parentElement!.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(fileInput, {
        target: { files: [fileFromObject(buildModelInfoFile())] },
      })

      await waitFor(() => expect(toastSuccess).toHaveBeenCalled())
      const callTitle = toastSuccess.mock.calls[0]![0] as string
      // i18n stub serialises the count + dropped params into the
      // returned text — assert the values are right.
      expect(callTitle).toMatch(/"count":1/)
      expect(callTitle).toMatch(/"dropped":2/)
    })
  })

  describe('Edge cases', () => {
    it('shows an error toast when no alias in the file matches the registry', async () => {
      const onImport = vi.fn()
      render(
        <ImportModelInfoButton
          readonly={false}
          knownAliases={['no-overlap']}
          onImport={onImport}
        />,
      )

      const fileInput = screen.getByRole('button').parentElement!.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(fileInput, {
        target: { files: [fileFromObject(buildModelInfoFile())] },
      })

      await waitFor(() => expect(toastError).toHaveBeenCalled())
      expect(toastError.mock.calls[0]![0]).toMatch(/noneMatched/)
      expect(onImport).not.toHaveBeenCalled()
    })

    it('reports a parse error toast when the file is not valid JSON', async () => {
      // Suppress the deliberate console.warn from the catch branch so
      // the test output stays clean; the component's job is to fail
      // gracefully without crashing the panel.
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)

      render(
        <ImportModelInfoButton
          readonly={false}
          knownAliases={knownAliases}
          onImport={vi.fn()}
        />,
      )

      const fileInput = screen.getByRole('button').parentElement!.querySelector('input[type="file"]') as HTMLInputElement
      const badFile = new File(['not valid json{'], 'model_info.json', {
        type: 'application/json',
      })
      fireEvent.change(fileInput, { target: { files: [badFile] } })

      await waitFor(() => expect(toastError).toHaveBeenCalled())
      expect(toastError.mock.calls[0]![0]).toMatch(/parseFailed/)
      expect(warnSpy).toHaveBeenCalled()
    })

    it('handles non-object top-level JSON without throwing', async () => {
      const onImport = vi.fn()
      render(
        <ImportModelInfoButton
          readonly={false}
          knownAliases={knownAliases}
          onImport={onImport}
        />,
      )

      const fileInput = screen.getByRole('button').parentElement!.querySelector('input[type="file"]') as HTMLInputElement
      // Top-level array is structurally valid JSON but not the
      // ``Record`` shape — the component must produce zero ids.
      fireEvent.change(fileInput, {
        target: { files: [fileFromObject(['llama3-local', 'qwen-local'])] },
      })

      await waitFor(() => expect(toastError).toHaveBeenCalled())
      expect(toastError.mock.calls[0]![0]).toMatch(/noneMatched/)
      expect(onImport).not.toHaveBeenCalled()
    })

    it('clears the file input value so re-uploading the same file fires change again', async () => {
      const onImport = vi.fn()
      render(
        <ImportModelInfoButton
          readonly={false}
          knownAliases={knownAliases}
          onImport={onImport}
        />,
      )

      const fileInput = screen.getByRole('button').parentElement!.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(fileInput, {
        target: { files: [fileFromObject(buildModelInfoFile())] },
      })

      await waitFor(() => expect(onImport).toHaveBeenCalled())
      // Without the manual value-reset the second pick of the same
      // file would be a no-op — the assertion guards against a
      // regression that would silently swallow the second click.
      expect(fileInput.value).toBe('')
    })
  })
})
