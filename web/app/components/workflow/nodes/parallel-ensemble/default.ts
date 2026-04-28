import type { NodeDefault } from '../../types'
import type {
  DiagnosticsConfig,
  ParallelEnsembleNodeType,
} from './types'
import { BlockClassificationEnum } from '@/app/components/workflow/block-selector/types'
import { BlockEnum } from '@/app/components/workflow/types'
import { genNodeMetaData } from '@/app/components/workflow/utils'
import {
  DEFAULT_AGGREGATOR_NAME,
  DEFAULT_DIAGNOSTICS,
  DEFAULT_RUNNER_NAME,
  FORBIDDEN_DSL_KEYS,
} from './types'

const i18nPrefix = 'nodes.parallelEnsemble'

const metaData = genNodeMetaData({
  author: 'xianghe',
  classification: BlockClassificationEnum.Transform,
  // Sit immediately after the response-level sibling EnsembleAggregator
  // (sort 4) — the two share the picker section "Transform" and users
  // pick between them based on whether they need token-level voting.
  sort: 5,
  type: BlockEnum.ParallelEnsemble,
})

const ALLOWED_STORAGE_VALUES: ReadonlyArray<DiagnosticsConfig['storage']> = [
  'inline',
  'metadata',
]

const nodeDefault: NodeDefault<ParallelEnsembleNodeType> = {
  metaData,
  defaultValue: {
    ensemble: {
      // ``question_variable`` must be a ≥ 2-segment selector; an empty
      // array signals "user hasn't picked yet" so the field renders
      // empty rather than guessing at e.g. ``["start", "user_input"]``
      // which would silently bind to whatever variable lives there.
      question_variable: [],
      model_aliases: [],
      runner_name: DEFAULT_RUNNER_NAME,
      runner_config: {},
      aggregator_name: DEFAULT_AGGREGATOR_NAME,
      aggregator_config: {},
      diagnostics: { ...DEFAULT_DIAGNOSTICS },
    },
  },
  checkValid(payload, t) {
    const ensemble = payload?.ensemble
    let errorMessages = ''

    // Defense-in-depth — DSL imports / saved snapshots aren't TS-checked,
    // so the nested ``ensemble`` block could be missing entirely. Match
    // backend ``ParallelEnsembleConfig`` required-field shape and bail
    // before reading individual sub-fields.
    if (!ensemble) {
      return {
        isValid: false,
        errorMessage: t('errorMsg.fieldRequired', {
          ns: 'workflow',
          field: t(`${i18nPrefix}.title`, { ns: 'workflow' }),
        }),
      }
    }

    const {
      question_variable,
      model_aliases,
      runner_name,
      runner_config,
      aggregator_name,
      aggregator_config,
      diagnostics,
    } = ensemble

    // ── question_variable: ≥ 2 segments (mirrors entities.py) ───────
    if (!Array.isArray(question_variable) || question_variable.length < 2) {
      errorMessages = t('errorMsg.fieldRequired', {
        ns: 'workflow',
        field: t(`${i18nPrefix}.questionVariable`, { ns: 'workflow' }),
      })
    }

    // ── model_aliases: floor + uniqueness + per-runner minimum ───────
    // Backend ``ParallelEnsembleConfig.model_aliases`` enforces
    // ``min_length=1``; the v0.2 built-in runners ``response_level``
    // and ``token_step`` both raise in ``validate_selection`` when
    // ``len(model_aliases) < 2``. We mirror that minimum here for the
    // built-ins so a saved DSL never gets past the panel only to fail
    // at run time. Third-party / unknown runners keep the looser ≥ 1
    // bound — a hypothetical ``judge`` runner with one contestant +
    // a separate ``judge_alias`` field is legitimate at length 1, and
    // its own ``validate_selection`` is the right place for the
    // runner-specific rule.
    const BUILT_IN_RUNNERS_REQUIRING_TWO: ReadonlySet<string> = new Set([
      'response_level',
      'token_step',
    ])
    const minAliases = BUILT_IN_RUNNERS_REQUIRING_TWO.has(runner_name) ? 2 : 1
    if (!errorMessages) {
      if (!Array.isArray(model_aliases) || model_aliases.length < minAliases) {
        if (minAliases === 2) {
          errorMessages = t(`${i18nPrefix}.errorMsg.runnerNeedsTwoAliases`, {
            ns: 'workflow',
            runner: runner_name,
          })
        }
        else {
          errorMessages = t('errorMsg.fieldRequired', {
            ns: 'workflow',
            field: t(`${i18nPrefix}.models`, { ns: 'workflow' }),
          })
        }
      }
      else {
        const seen = new Set<string>()
        for (const alias of model_aliases) {
          if (typeof alias !== 'string' || alias.length === 0) {
            errorMessages = t(`${i18nPrefix}.errorMsg.modelAliasMustBeString`, {
              ns: 'workflow',
            })
            break
          }
          if (seen.has(alias)) {
            errorMessages = t(`${i18nPrefix}.errorMsg.duplicateModelAlias`, {
              ns: 'workflow',
              alias,
            })
            break
          }
          seen.add(alias)
        }
      }
    }

    // ── runner_name + aggregator_name: non-empty strings ────────────
    if (!errorMessages && (!runner_name || typeof runner_name !== 'string')) {
      errorMessages = t('errorMsg.fieldRequired', {
        ns: 'workflow',
        field: t(`${i18nPrefix}.runner`, { ns: 'workflow' }),
      })
    }
    if (!errorMessages && (!aggregator_name || typeof aggregator_name !== 'string')) {
      errorMessages = t('errorMsg.fieldRequired', {
        ns: 'workflow',
        field: t(`${i18nPrefix}.aggregator`, { ns: 'workflow' }),
      })
    }

    // ── runner_config / aggregator_config: plain object ─────────────
    // Backend ``runner_config`` / ``aggregator_config`` are
    // ``dict[str, object]`` (the runner's own ``config_class`` is the
    // real schema). We can't reflect against that schema here without
    // the live RunnerMeta fetch, so the static check is "must be a
    // plain object" — anything stronger needs the runner descriptor and
    // is enforced inside ``DynamicConfigForm``.
    const isPlainObject = (v: unknown) =>
      typeof v === 'object' && v !== null && !Array.isArray(v)
    if (!errorMessages && !isPlainObject(runner_config)) {
      errorMessages = t(`${i18nPrefix}.errorMsg.configMustBeObject`, {
        ns: 'workflow',
        field: t(`${i18nPrefix}.runnerConfig`, { ns: 'workflow' }),
      })
    }
    if (!errorMessages && !isPlainObject(aggregator_config)) {
      errorMessages = t(`${i18nPrefix}.errorMsg.configMustBeObject`, {
        ns: 'workflow',
        field: t(`${i18nPrefix}.aggregatorConfig`, { ns: 'workflow' }),
      })
    }

    // ── DSL smuggle guard: forbid sensitive keys on ensemble + nested
    // configs. Mirrors backend ``_FORBIDDEN_TOP_LEVEL_KEYS`` rejection
    // in ``ParallelEnsembleNodeData._reject_sensitive_top_level_fields``
    // and the ``extra="forbid"`` on the inner config blobs. The check
    // runs even if the user pasted a hand-edited DSL — the panel's UI
    // never produces these keys, but DSL imports / clipboard pastes do.
    if (!errorMessages) {
      const offending: string[] = []
      const forbidden = new Set<string>(FORBIDDEN_DSL_KEYS)
      const scan = (cfg: unknown) => {
        if (!isPlainObject(cfg))
          return
        for (const key of Object.keys(cfg as Record<string, unknown>)) {
          if (forbidden.has(key))
            offending.push(key)
        }
      }
      scan(ensemble as unknown as Record<string, unknown>)
      scan(runner_config)
      scan(aggregator_config)
      if (offending.length > 0) {
        errorMessages = t(`${i18nPrefix}.errorMsg.forbiddenDslKey`, {
          ns: 'workflow',
          key: offending[0],
        })
      }
    }

    // ── diagnostics: storage allowlist + max_trace_tokens > 0 ───────
    // Mirrors ``DiagnosticsConfig`` (spi/trace.py): ``storage`` is a
    // closed Literal, ``max_trace_tokens`` is ``Field(default=1000,
    // gt=0)``. Bumping the v0.2 enum to "artifact" requires a backend
    // change first (and lifting this guard).
    if (!errorMessages && diagnostics) {
      if (!ALLOWED_STORAGE_VALUES.includes(diagnostics.storage)) {
        errorMessages = t(`${i18nPrefix}.errorMsg.unknownStorage`, {
          ns: 'workflow',
          storage: String(diagnostics.storage),
        })
      }
      else if (
        typeof diagnostics.max_trace_tokens !== 'number'
        || !Number.isFinite(diagnostics.max_trace_tokens)
        || diagnostics.max_trace_tokens <= 0
      ) {
        errorMessages = t(`${i18nPrefix}.errorMsg.maxTraceTokensPositive`, {
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
