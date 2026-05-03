import type { NodeDefault } from '../../types'
import type { EnsembleAggregatorNodeType, EnsembleStrategyName } from './types'
import { BlockClassificationEnum } from '@/app/components/workflow/block-selector/types'
import { BlockEnum } from '@/app/components/workflow/types'
import { genNodeMetaData } from '@/app/components/workflow/utils'
import { ENSEMBLE_STRATEGY_NAMES } from './types'

const i18nPrefix = 'nodes.ensembleAggregator'

const metaData = genNodeMetaData({
  author: 'xianghe',
  classification: BlockClassificationEnum.Transform,
  sort: 4,
  type: BlockEnum.EnsembleAggregator,
})

// Allowed config keys per strategy — mirrors each backend strategy's
// ``model_config = ConfigDict(extra="forbid")`` declared field set
// (api/core/workflow/nodes/ensemble_aggregator/strategies/*.py).
// Without this, a DSL import with a stray key passes the frontend and
// only fails at run time inside ``StrategyConfigError``.
const ALLOWED_KEYS_BY_STRATEGY: Record<EnsembleStrategyName, readonly string[]> = {
  concat: ['separator', 'include_source_label', 'order_by_weight'],
}

const isFiniteNumber = (v: unknown): v is number =>
  // Reject ``bool`` explicitly: ``true``/``false`` are ``number``-coercible
  // via ``Number(true) === 1`` and ``typeof true !== 'number'`` already
  // protects us, but mirroring backend's bool guard makes the intent
  // visible to readers and survives a future refactor.
  typeof v === 'number' && Number.isFinite(v) && typeof v !== 'boolean'

const isVariableSelectorShape = (v: unknown): v is string[] => {
  if (!Array.isArray(v))
    return false
  if (v.length < 2)
    return false
  return v.every(seg => typeof seg === 'string' && seg.trim().length > 0)
}

const nodeDefault: NodeDefault<EnsembleAggregatorNodeType> = {
  metaData,
  defaultValue: {
    inputs: [],
    strategy_name: 'concat',
    strategy_config: {},
  },
  checkValid(payload: EnsembleAggregatorNodeType, t: (key: string, options?: Record<string, unknown>) => string) {
    const { inputs, strategy_name, strategy_config } = payload
    let errorMessages = ''

    if (!inputs || inputs.length < 2) {
      errorMessages = t('errorMsg.fieldRequired', {
        ns: 'workflow',
        field: t(`${i18nPrefix}.inputs`, { ns: 'workflow' }),
      })
    }

    if (!errorMessages && inputs) {
      const seenSourceIds = new Set<string>()
      for (const ref of inputs) {
        const sid = (ref.source_id || '').trim()
        if (!sid) {
          errorMessages = t('errorMsg.fieldRequired', {
            ns: 'workflow',
            field: t(`${i18nPrefix}.sourceId`, { ns: 'workflow' }),
          })
          break
        }
        if (seenSourceIds.has(sid)) {
          errorMessages = t(`${i18nPrefix}.errorMsg.duplicateSourceId`, {
            ns: 'workflow',
            sourceId: sid,
          })
          break
        }
        seenSourceIds.add(sid)
        if (!ref.variable_selector || ref.variable_selector.length < 2) {
          errorMessages = t('errorMsg.fieldRequired', {
            ns: 'workflow',
            field: t(`${i18nPrefix}.variableSelector`, { ns: 'workflow' }),
          })
          break
        }

        // Weight: static finite number OR ``VariableSelector``-shaped
        // ``list[str]`` (≥2 segments, all non-blank). Mirrors backend
        // ``AggregationInputRef._weight_selector_well_formed``.
        // ``undefined`` is the legacy-DSL path (v2.4 inputs had no
        // ``weight`` field); backend pydantic fills the default ``1.0``,
        // so we accept absence here rather than fail validation on an
        // older snapshot the user has not yet edited.
        const w: unknown = ref.weight
        const weightOk
          = w === undefined || isFiniteNumber(w) || isVariableSelectorShape(w)
        if (!weightOk) {
          errorMessages = t(`${i18nPrefix}.errorMsg.weightInvalid`, {
            ns: 'workflow',
            sourceId: sid,
          })
          break
        }

        // Fallback weight: ``null`` (= fail-fast, default) or finite number.
        // Mirrors backend ``_fallback_weight_finite``.
        const fb = ref.fallback_weight
        if (fb !== null && fb !== undefined && !isFiniteNumber(fb)) {
          errorMessages = t(`${i18nPrefix}.errorMsg.fallbackWeightInvalid`, {
            ns: 'workflow',
            sourceId: sid,
          })
          break
        }

        if (ref.extra !== undefined && (typeof ref.extra !== 'object' || ref.extra === null || Array.isArray(ref.extra))) {
          errorMessages = t(`${i18nPrefix}.errorMsg.extraMustBeObject`, {
            ns: 'workflow',
            sourceId: sid,
          })
          break
        }
      }
    }

    // Defense-in-depth: runtime payloads from DSL import or legacy
    // snapshots are not TypeScript-checked, so an unknown strategy_name
    // must be surfaced here before the config-key guard runs (which would
    // otherwise silently fall through to ``[]`` and accept an empty config).
    if (!errorMessages) {
      const known = new Set<EnsembleStrategyName>(ENSEMBLE_STRATEGY_NAMES)
      if (!known.has(strategy_name as EnsembleStrategyName)) {
        errorMessages = t(`${i18nPrefix}.errorMsg.unknownStrategyName`, {
          ns: 'workflow',
          strategy: String(strategy_name),
        })
      }
    }

    // Mirror backend ``extra="forbid"`` on each strategy's config schema;
    // without this, DSL imports with stray keys pass the frontend and
    // only fail at run time inside StrategyConfigError.
    if (!errorMessages) {
      const cfg = (strategy_config ?? {}) as Record<string, unknown>
      const allowed = new Set(ALLOWED_KEYS_BY_STRATEGY[strategy_name])
      const unknownKey = Object.keys(cfg).find(k => !allowed.has(k))
      if (unknownKey) {
        errorMessages = t(`${i18nPrefix}.errorMsg.unknownStrategyConfigKey`, {
          ns: 'workflow',
          key: unknownKey,
          strategy: strategy_name,
        })
      }
    }

    if (!errorMessages && strategy_name === 'concat') {
      const cfg = (strategy_config ?? {}) as {
        separator?: unknown
        include_source_label?: unknown
        order_by_weight?: unknown
      }
      if (cfg.separator !== undefined && typeof cfg.separator !== 'string') {
        errorMessages = t(`${i18nPrefix}.errorMsg.separatorMustBeString`, {
          ns: 'workflow',
        })
      }
      if (
        !errorMessages
        && cfg.include_source_label !== undefined
        && typeof cfg.include_source_label !== 'boolean'
      ) {
        errorMessages = t(`${i18nPrefix}.errorMsg.labelMustBeBoolean`, {
          ns: 'workflow',
        })
      }
      if (
        !errorMessages
        && cfg.order_by_weight !== undefined
        && typeof cfg.order_by_weight !== 'boolean'
      ) {
        errorMessages = t(`${i18nPrefix}.errorMsg.orderByWeightMustBeBoolean`, {
          ns: 'workflow',
        })
      }
    }

    return {
      isValid: !errorMessages,
      errorMessage: errorMessages,
    }
  },
}

export default nodeDefault
