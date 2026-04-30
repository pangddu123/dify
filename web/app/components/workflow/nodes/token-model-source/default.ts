import type { NodeDefault } from '../../types'
import type { TokenModelSourceNodeType } from './types'
import { BlockClassificationEnum } from '@/app/components/workflow/block-selector/types'
import { BlockEnum } from '@/app/components/workflow/types'
import { genNodeMetaData } from '@/app/components/workflow/utils'
import { DEFAULT_SAMPLING_PARAMS } from './types'

const i18nPrefix = 'nodes.tokenModelSource'

const metaData = genNodeMetaData({
  author: 'xianghe',
  // ``Transform`` keeps the node grouped with parallel-ensemble — they
  // are typically dropped in pairs (multiple sources → one ensemble),
  // and the picker reads top-to-bottom in the same Transform section.
  classification: BlockClassificationEnum.Transform,
  // Sort 6 places it directly after parallel-ensemble (sort 5). The
  // canvas picker reads top-to-bottom, so the order matches the typical
  // build flow: drop a parallel-ensemble first, then add 2+ token
  // sources to feed it.
  sort: 6,
  type: BlockEnum.TokenModelSource,
})

const nodeDefault: NodeDefault<TokenModelSourceNodeType> = {
  metaData,
  defaultValue: {
    model_alias: '',
    prompt_template: '',
    sampling_params: { ...DEFAULT_SAMPLING_PARAMS },
    extra: {},
  },
  checkValid(payload, t) {
    let errorMessage = ''

    // ── model_alias: required, non-blank string ──────────────────
    // Mirrors backend ``TokenModelSourceNodeData.model_alias``
    // (entities.py): ``Field(..., min_length=1)`` + a ``strip``
    // validator. Catching this here means the DSL save flow never
    // produces a payload pydantic will reject — same defence-in-depth
    // pattern parallel-ensemble's checkValid uses.
    const alias = payload?.model_alias
    if (typeof alias !== 'string' || alias.trim().length === 0) {
      errorMessage = t('errorMsg.fieldRequired', {
        ns: 'workflow',
        field: t(`${i18nPrefix}.modelAlias`, { ns: 'workflow' }),
      })
    }

    // ── sampling_params: shape + bounds ──────────────────────────
    // The backend pydantic layer guards every bound (``top_k > 0``,
    // ``temperature >= 0``, ``max_tokens > 0``, ``top_p in (0, 1]``).
    // Replay the same checks here so the panel red-lines before save
    // — a saved-then-rejected payload corrupts the workflow's draft
    // state and forces a refresh.
    const sp = payload?.sampling_params
    if (!errorMessage) {
      if (!sp || typeof sp !== 'object' || Array.isArray(sp)) {
        errorMessage = t(`${i18nPrefix}.errorMsg.samplingParamsMissing`, {
          ns: 'workflow',
        })
      }
      else {
        if (
          typeof sp.top_k !== 'number'
          || !Number.isInteger(sp.top_k)
          || sp.top_k <= 0
        ) {
          // ``Number.isInteger`` rather than ``Number.isFinite``:
          // backend ``SamplingParams.top_k`` is ``int`` (entities.py),
          // so 1.5 round-trips to a Pydantic ValidationError. Catch
          // it here so the panel red-lines pre-save instead of
          // surfacing as a runtime "422" the user can't trace.
          errorMessage = t(`${i18nPrefix}.errorMsg.topKPositive`, { ns: 'workflow' })
        }
        else if (
          typeof sp.temperature !== 'number'
          || !Number.isFinite(sp.temperature)
          || sp.temperature < 0
        ) {
          errorMessage = t(`${i18nPrefix}.errorMsg.temperatureNonNegative`, {
            ns: 'workflow',
          })
        }
        else if (
          typeof sp.max_tokens !== 'number'
          || !Number.isInteger(sp.max_tokens)
          || sp.max_tokens <= 0
        ) {
          // Same int-only contract as top_k — backend
          // ``SamplingParams.max_tokens`` is an integer.
          errorMessage = t(`${i18nPrefix}.errorMsg.maxTokensPositive`, {
            ns: 'workflow',
          })
        }
        else if (
          sp.top_p !== null
          && sp.top_p !== undefined
          && (
            typeof sp.top_p !== 'number'
            || !Number.isFinite(sp.top_p)
            || sp.top_p <= 0
            || sp.top_p > 1
          )
        ) {
          errorMessage = t(`${i18nPrefix}.errorMsg.topPRange`, { ns: 'workflow' })
        }
        else if (
          sp.seed !== null
          && sp.seed !== undefined
          && (typeof sp.seed !== 'number' || !Number.isInteger(sp.seed))
        ) {
          errorMessage = t(`${i18nPrefix}.errorMsg.seedInteger`, { ns: 'workflow' })
        }
        else if (!Array.isArray(sp.stop) || sp.stop.some(s => typeof s !== 'string')) {
          errorMessage = t(`${i18nPrefix}.errorMsg.stopList`, { ns: 'workflow' })
        }
      }
    }

    // ── extra: plain object only ─────────────────────────────────
    // ``extra`` is the documented escape hatch for backend-private
    // sampling knobs (vLLM ``repetition_penalty``, research_tag, ...).
    // The backend's ``Field(default_factory=dict)`` will reject a list
    // / scalar at validate time; pre-empt it here so the panel doesn't
    // need to ship round-trip recovery.
    const extra = payload?.extra
    if (!errorMessage && extra !== undefined && extra !== null) {
      if (typeof extra !== 'object' || Array.isArray(extra)) {
        errorMessage = t(`${i18nPrefix}.errorMsg.extraMustBeObject`, {
          ns: 'workflow',
        })
      }
    }

    return {
      isValid: !errorMessage,
      errorMessage,
    }
  },
}

export default nodeDefault
