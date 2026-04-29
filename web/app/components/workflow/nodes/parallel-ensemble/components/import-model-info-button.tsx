'use client'
import type { FC } from 'react'
import { Button } from '@langgenius/dify-ui/button'
import { toast } from '@langgenius/dify-ui/toast'
import * as React from 'react'
import { useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'

type Props = {
  readonly: boolean
  // ``isLoading`` mirrors the model-registry fetch state. While the
  // registry is still in flight ``knownAliases`` is the empty array,
  // and *every* imported id would be filtered out — the user would see
  // a misleading "noneMatched" toast. Disabling the button until the
  // registry resolves is the cleanest fix.
  isLoading?: boolean
  // Aliases registered in the backend yaml — used to filter the
  // imported list down to *known* aliases. An entry from the user's
  // hand-edited ``model_info.json`` that doesn't exist in the registry
  // is silently dropped and the count surfaced in the toast so the
  // user can see how many entries didn't match.
  knownAliases: ReadonlyArray<string>
  onImport: (aliases: string[]) => void
}

// model_info.json shape (legacy from PN.py): a flat object whose values
// each carry an ``id`` field. Other keys (``url``, ``api_key``,
// ``EOS``, ``stop_think``, etc.) are intentionally **not** read here —
// the SSRF / credential boundary documented in EXTENSIBILITY_SPEC §4.4
// applies to imports too. Pulling only ``id`` is the strictest possible
// read that still satisfies the user's "import my existing model list"
// ask.
type ModelInfoEntry = { id?: unknown }
type ModelInfoFile = Record<string, ModelInfoEntry | unknown>

const extractIds = (parsed: unknown): string[] => {
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
    return []
  const out: string[] = []
  for (const value of Object.values(parsed as ModelInfoFile)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const id = (value as ModelInfoEntry).id
      if (typeof id === 'string' && id.length > 0)
        out.push(id)
    }
  }
  return out
}

const ImportModelInfoButton: FC<Props> = ({ readonly, isLoading = false, knownAliases, onImport }) => {
  const { t } = useTranslation()
  const inputRef = useRef<HTMLInputElement | null>(null)

  const handleClick = useCallback(() => {
    inputRef.current?.click()
  }, [])

  const handleFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      // Reset the input value so picking the *same* file twice fires
      // change again — without this the second click would be a no-op
      // and the user would think the import silently failed.
      if (e.target)
        e.target.value = ''
      if (!file)
        return

      try {
        const text = await file.text()
        const parsed: unknown = JSON.parse(text)
        const ids = extractIds(parsed)
        const known = new Set(knownAliases)
        const matched = Array.from(new Set(ids.filter(id => known.has(id))))
        const dropped = ids.length - matched.length

        if (matched.length === 0) {
          toast.error(
            t('nodes.parallelEnsemble.importToast.noneMatched', {
              ns: 'workflow',
              defaultValue: 'No alias in the imported file matches the registry.',
            }),
          )
          return
        }

        onImport(matched)
        toast.success(
          t('nodes.parallelEnsemble.importToast.matched', {
            ns: 'workflow',
            count: matched.length,
            dropped,
            defaultValue: `Imported ${matched.length} alias(es); ${dropped} dropped`,
          }),
        )
      }
      catch (err) {
        toast.error(
          t('nodes.parallelEnsemble.importToast.parseFailed', {
            ns: 'workflow',
            defaultValue: 'Failed to parse model_info.json',
          }),
        )
        // Console-log the actual exception so the user can debug a
        // malformed file without the panel needing to surface raw
        // parser output. The toast keeps the user-facing message
        // non-technical.
        console.warn('[parallel-ensemble] model_info.json import failed', err)
      }
    },
    [knownAliases, onImport, t],
  )

  return (
    <>
      <Button
        size="small"
        variant="secondary"
        disabled={readonly || isLoading}
        onClick={handleClick}
      >
        {t('nodes.parallelEnsemble.importModelInfo', {
          ns: 'workflow',
          defaultValue: 'Import model_info.json',
        })}
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept="application/json"
        className="hidden"
        onChange={handleFileChange}
      />
    </>
  )
}

export default React.memo(ImportModelInfoButton)
