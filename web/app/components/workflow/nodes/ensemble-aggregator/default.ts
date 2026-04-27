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

const nodeDefault: NodeDefault<EnsembleAggregatorNodeType> = {
  metaData,
  defaultValue: {
    inputs: [],
    strategy_name: 'majority_vote',
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
      }
    }

    // Defense-in-depth: runtime payloads from DSL import or legacy
    // snapshots are not TypeScript-checked, so an unknown strategy_name
    // must be surfaced here before the config-key guard runs (which would
    // otherwise silently fall through to `[]` and accept an empty config).
    if (!errorMessages) {
      const known = new Set<EnsembleStrategyName>(ENSEMBLE_STRATEGY_NAMES)
      if (!known.has(strategy_name as EnsembleStrategyName)) {
        errorMessages = t(`${i18nPrefix}.errorMsg.unknownStrategyName`, {
          ns: 'workflow',
          strategy: String(strategy_name),
        })
      }
    }

    // Mirror backend `extra="forbid"` on each strategy's config schema;
    // without this, DSL imports with stray keys pass the frontend and
    // only fail at run time inside StrategyConfigError.
    if (!errorMessages) {
      const cfg = (strategy_config ?? {}) as Record<string, unknown>
      const allowedKeysByStrategy: Record<EnsembleStrategyName, readonly string[]> = {
        majority_vote: [],
        concat: ['separator', 'include_source_label'],
      }
      const allowed = new Set(allowedKeysByStrategy[strategy_name])
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
    }

    return {
      isValid: !errorMessages,
      errorMessage: errorMessages,
    }
  },
}

export default nodeDefault
