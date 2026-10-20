import type {
  DatasetFetchPolicy,
  DatasetInfo,
  RegisteredScenario,
  ScenarioDatasetOptions,
  ScenarioDatasetRequest,
  ScenarioDatasetSettings,
  ScenarioDatasetSourceInput,
  ScenarioDatasetSourceSettings,
} from '@/types'

/**
 * The scenario's declared dataset defaults, or a stand-in built from its legacy fields.
 *
 * A run's estimate projects these per selection; a form with no estimate in hand (the
 * preset editor) still needs the same baseline, so both read it from here rather than
 * each carrying its own copy of the fallback.
 */
export function scenarioDatasetDefaults(scenario: RegisteredScenario): ScenarioDatasetSettings {
  return scenario.dataset_defaults ?? {
    sources: scenario.default_datasets.map(
      (name) => ({ name, max_size: 'all', fetch: 'if_missing', configured_provider: false }),
    ),
    max_per_dataset: 'all',
    max_total: scenario.default_run_size.dataset_limit.value ?? 'all',
    fetch: 'if_missing',
    filters: {},
  }
}

/** The scenario's declared dataset capabilities, or a permissive stand-in when it declares none. */
export function scenarioDatasetOptions(scenario: RegisteredScenario): ScenarioDatasetOptions {
  return scenario.dataset_options ?? {
    mode: 'any',
    allowed_names: [],
    per_dataset_limit: true,
    total_limit: scenario.default_run_size.dataset_limit.state !== 'not_applicable',
    supports_overrides: true,
    filters: true,
  }
}

function explicitDatasetPolicy(value: ScenarioDatasetRequest['fetch']): DatasetFetchPolicy | undefined {
  return value === 'never' || value === 'if_missing' || value === 'always' ? value : undefined
}

export function canEditDatasetLoading(effective: ScenarioDatasetSettings, options: ScenarioDatasetOptions): boolean {
  return options.supports_overrides && (options.source_settings !== false
    || effective.sources.every((source) => source.fetch === effective.fetch))
}

export function datasetPolicies(
  request: ScenarioDatasetRequest, defaults: ScenarioDatasetSettings,
  effective: ScenarioDatasetSettings, options: ScenarioDatasetOptions,
): Array<{ name: string; fetch: DatasetFetchPolicy }> {
  const selected: ScenarioDatasetSourceInput[] = request.sources ?? defaults.sources.map((source) => ({ name: source.name }))
  const canEditSources = options.supports_overrides && options.source_settings !== false
  const globalDraft = canEditDatasetLoading(effective, options) ? explicitDatasetPolicy(request.fetch) : undefined
  return selected.map((source) => {
    const settings = effective.sources.find((item) => item.name === source.name)
      ?? defaults.sources.find((item) => item.name === source.name)
    return {
      name: source.name,
      fetch: canEditSources ? explicitDatasetPolicy(source.fetch) ?? settings?.fetch
        ?? explicitDatasetPolicy(request.fetch) ?? defaults.fetch
        : globalDraft ?? settings?.fetch ?? defaults.fetch,
    }
  })
}

export function parseCaseLimit(text: string): number | null {
  const value = text.trim()
  const number = Number(value)
  return /^\d+$/.test(value) && Number.isSafeInteger(number) && number > 0 ? number : null
}

export function datasetSize(
  dataset: DatasetInfo | undefined, limit: ScenarioDatasetSettings['max_total'],
): string {
  const count = dataset?.loaded ? dataset.logical_examples : null
  if (limit === 'all') return count != null
    ? `${count.toLocaleString()} examples`
    : dataset?.loaded ? 'Loaded' : 'Not loaded'
  return count != null
    ? `Up to ${Math.min(limit, count).toLocaleString()} of ${count.toLocaleString()}`
    : `Up to ${limit.toLocaleString()}`
}

export function datasetChoices(
  datasets: DatasetInfo[], defaults: ScenarioDatasetSourceSettings[], options: ScenarioDatasetOptions,
): string[] {
  const names = new Set([...datasets.map((item) => item.name), ...defaults.map((item) => item.name)])
  return [...names].filter((name) => options.mode === 'any' || options.allowed_names.includes(name)).sort()
}

export function datasetNameWithOrigin(name: string, dataset: DatasetInfo | undefined): string {
  const origins = [...new Set(dataset?.origins ?? [])]
  return `${name} (${origins.length > 0 ? origins.join(', ') : 'unknown'})`
}

export function datasetFacts(datasets: DatasetInfo[], names: string[]) {
  const loaded = datasets.filter((item) => item.loaded && names.includes(item.name))
  return {
    loaded,
    allLoaded: names.length > 0 && loaded.length === names.length,
  }
}
