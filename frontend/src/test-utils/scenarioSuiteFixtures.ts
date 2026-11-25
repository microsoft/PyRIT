import type {
  PresetIssue,
  ScenarioPreset,
  ScenarioPresetResponse,
  ScenarioRunSizeEstimateResponse,
  ScenarioSuiteEntryStatus,
  ScenarioSuiteResponse,
} from '../types'

interface SuiteOptions {
  description?: string | null
  author?: string | null
  origin?: ScenarioSuiteResponse['origin']
  /** Issues for each entry, by position. */
  entryIssues?: PresetIssue[][]
  /** Entry origins by position; `null` marks a scenario preset this deployment lacks. */
  entryOrigins?: ScenarioSuiteEntryStatus['origin'][]
}

/** A stored suite (a GUI "preset") whose entries reference the named scenario presets. */
export function makeSuite(name: string, presetNames: string[], options: SuiteOptions = {}): ScenarioSuiteResponse {
  const entries = presetNames.map((presetName, index) => ({
    entry_id: `${name}-entry-${index + 1}`,
    preset_name: presetName,
  }))
  const origin = options.origin ?? 'stored'
  return {
    suite: {
      name,
      schema_version: 1,
      description: options.description ?? null,
      author: options.author ?? null,
      entries,
    },
    origin,
    version: origin === 'stored' ? `${name}-v1` : null,
    entry_statuses: entries.map((entry, index): ScenarioSuiteEntryStatus => {
      const entryOrigin = options.entryOrigins?.[index] === undefined ? 'stored' : options.entryOrigins[index]
      return {
        ...entry,
        origin: entryOrigin,
        preset_version: entryOrigin === 'stored' ? 'v1' : null,
        issues: options.entryIssues?.[index] ?? [],
      }
    }),
  }
}

/** A stored scenario preset (a GUI "scenario" inside a preset). */
export function makePresetResponse(
  preset: ScenarioPreset,
  overrides: Partial<Omit<ScenarioPresetResponse, 'preset'>> = {},
): ScenarioPresetResponse {
  return { preset, origin: 'stored', version: 'v1', issues: [], ...overrides }
}

export function makeRunSize(
  attacks: number,
  status: ScenarioRunSizeEstimateResponse['status'] = 'exact',
): ScenarioRunSizeEstimateResponse {
  return {
    status,
    dataset_size: { kind: 'bounded', value: attacks },
    dataset_limit: { state: 'scenario_default' },
    estimated_attack_count: attacks,
    components: [{ label: 'Configured attacks', count: attacks, is_baseline: false, note: null }],
    datasets: [],
    note: null,
  }
}
