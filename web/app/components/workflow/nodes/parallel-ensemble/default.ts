import type { NodeDefault } from '../../types'
import type {
  DiagnosticsConfig,
  ParallelEnsembleNodeType,
  TokenSourceRef,
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

const isFiniteNumber = (v: unknown): v is number =>
  typeof v === 'number' && Number.isFinite(v) && typeof v !== 'boolean'

const isSelectorTuple = (v: unknown): v is string[] =>
  Array.isArray(v)
  && v.length >= 2
  && v.every(seg => typeof seg === 'string' && seg.trim().length > 0)

const nodeDefault: NodeDefault<ParallelEnsembleNodeType> = {
  metaData,
  defaultValue: {
    ensemble: {
      // Empty list signals "user hasn't added any source yet". Backend
      // ``ParallelEnsembleConfig.token_sources`` enforces ``min_length=1``;
      // ``checkValid`` below mirrors that bound so an unconfigured node
      // never saves silently.
      token_sources: [],
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
      token_sources,
      runner_name,
      runner_config,
      aggregator_name,
      aggregator_config,
      diagnostics,
    } = ensemble

    // ── token_sources: floor + per-source shape + uniqueness ────────
    // Backend ``ParallelEnsembleConfig.token_sources`` enforces
    // ``min_length=1``; the v0.3 ``token_step`` runner additionally
    // requires ≥ 2 voters in ``validate_selection``. Mirror both bounds
    // so a saved DSL never gets past the panel only to fail at run time.
    const BUILT_IN_RUNNERS_REQUIRING_TWO: ReadonlySet<string> = new Set([
      'token_step',
    ])
    const minSources = BUILT_IN_RUNNERS_REQUIRING_TWO.has(runner_name) ? 2 : 1
    if (!Array.isArray(token_sources) || token_sources.length < minSources) {
      if (minSources === 2) {
        errorMessages = t(`${i18nPrefix}.errorMsg.runnerNeedsTwoSources`, {
          ns: 'workflow',
          runner: runner_name,
        })
      }
      else {
        errorMessages = t('errorMsg.fieldRequired', {
          ns: 'workflow',
          field: t(`${i18nPrefix}.tokenSources.title`, { ns: 'workflow' }),
        })
      }
    }
    else {
      const seen = new Set<string>()
      for (let i = 0; i < token_sources.length; i++) {
        const ref = token_sources[i] as TokenSourceRef | undefined
        if (!ref || typeof ref !== 'object') {
          errorMessages = t(`${i18nPrefix}.errorMsg.tokenSourceMalformed`, {
            ns: 'workflow',
            index: i + 1,
          })
          break
        }
        const sid = typeof ref.source_id === 'string' ? ref.source_id.trim() : ''
        if (!sid) {
          errorMessages = t(`${i18nPrefix}.errorMsg.sourceIdRequired`, {
            ns: 'workflow',
            index: i + 1,
          })
          break
        }
        if (seen.has(sid)) {
          errorMessages = t(`${i18nPrefix}.errorMsg.duplicateSourceId`, {
            ns: 'workflow',
            sourceId: sid,
          })
          break
        }
        seen.add(sid)

        if (!isSelectorTuple(ref.spec_selector)) {
          errorMessages = t(`${i18nPrefix}.errorMsg.specSelectorRequired`, {
            ns: 'workflow',
            sourceId: sid,
          })
          break
        }

        // weight: finite > 0, OR a ≥ 2-segment selector tuple. Mirrors
        // backend ``TokenSourceRef._weight_well_formed``.
        const w = ref.weight
        if (Array.isArray(w)) {
          if (!isSelectorTuple(w)) {
            errorMessages = t(`${i18nPrefix}.errorMsg.weightSelectorMalformed`, {
              ns: 'workflow',
              sourceId: sid,
            })
            break
          }
        }
        else if (typeof w === 'boolean' || !isFiniteNumber(w) || w <= 0) {
          errorMessages = t(`${i18nPrefix}.errorMsg.weightMustBePositive`, {
            ns: 'workflow',
            sourceId: sid,
          })
          break
        }

        // top_k_override: ``null`` (inherit upstream) or a positive
        // integer. The runner caps top_k server-side; this is the
        // shape-only guard.
        const tk = ref.top_k_override
        if (tk !== null && tk !== undefined) {
          if (typeof tk === 'boolean' || !Number.isInteger(tk) || (tk as number) <= 0) {
            errorMessages = t(`${i18nPrefix}.errorMsg.topKOverrideInvalid`, {
              ns: 'workflow',
              sourceId: sid,
            })
            break
          }
        }

        // fallback_weight: ``null`` (fail-fast) or a finite > 0 number.
        const fw = ref.fallback_weight
        if (fw !== null && fw !== undefined) {
          if (typeof fw === 'boolean' || !isFiniteNumber(fw) || fw <= 0) {
            errorMessages = t(`${i18nPrefix}.errorMsg.fallbackWeightInvalid`, {
              ns: 'workflow',
              sourceId: sid,
            })
            break
          }
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
