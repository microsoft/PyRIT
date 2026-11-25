import { techniqueSetMembers } from '@/components/Scenarios/scenarioTechniqueSets'
import type {
  PresetIssue,
  RegisteredScenario,
  ScenarioPresetResponse,
  ScenarioSuiteEntryStatus,
  ScenarioSuiteResponse,
} from '@/types'

/**
 * One scenario inside a preset, as the GUI shows it. The backend stores a preset as a
 * scenario suite and each of its scenarios as a scenario preset; see `ScenarioSuite`.
 */
export interface SuiteMember {
  readonly entryId: string
  /** The stored scenario preset's name, shown only as secondary text. */
  readonly presetName: string
  readonly status: ScenarioSuiteEntryStatus
  /** Undefined when this deployment has no stored or registered preset by that name. */
  readonly preset: ScenarioPresetResponse | undefined
  /** Undefined when this deployment does not register the preset's scenario. */
  readonly scenario: RegisteredScenario | undefined
}

function uniqueNames(names: string[]): string[] {
  return [...new Set(names)]
}

export function suiteMembers(
  item: ScenarioSuiteResponse,
  presets: ReadonlyMap<string, ScenarioPresetResponse>,
  scenarios: ReadonlyMap<string, RegisteredScenario>,
): SuiteMember[] {
  return item.entry_statuses.map((status) => {
    const preset = presets.get(status.preset_name)
    return {
      entryId: status.entry_id,
      presetName: status.preset_name,
      status,
      preset,
      scenario: preset ? scenarios.get(preset.preset.scenario_name) : undefined,
    }
  })
}

/** What a member shows as its name: the scenario it runs, or its stored name when that is unknown. */
export function memberScenarioName(member: SuiteMember): string {
  return member.preset?.preset.scenario_name ?? member.presetName
}

export function memberDescription(member: SuiteMember): string | null {
  return member.preset?.preset.description || member.scenario?.description || null
}

/** The concrete techniques a member runs, expanding any technique set it pins. */
export function memberTechniqueNames(member: SuiteMember): string[] {
  const { preset, scenario } = member
  const selections = preset?.preset.techniques
  if (!selections || selections.length === 0) {
    return uniqueNames(scenario?.default_techniques ?? [])
  }
  return uniqueNames(selections.flatMap((name) => {
    const members = scenario ? techniqueSetMembers(scenario, name) : []
    return members.length > 0 ? members : [name]
  }))
}

export function memberDatasetNames(member: SuiteMember): string[] {
  const sources = member.preset?.preset.datasets?.sources ?? []
  return sources.length > 0
    ? sources.map((source) => source.name)
    : member.scenario?.default_datasets ?? []
}

function suiteIssues(item: ScenarioSuiteResponse): PresetIssue[] {
  return item.entry_statuses.flatMap((status) => status.issues)
}

/** A preset with issues cannot launch here, so only the Registry lists it. */
export function suiteNeedsAttention(item: ScenarioSuiteResponse): boolean {
  return suiteIssues(item).length > 0
}

/**
 * The preset's total attack count, or null unless every scenario has an exact estimate.
 * An approximate or missing estimate would make the sum look more certain than it is.
 */
export function suiteTotalAttacks(members: SuiteMember[]): number | null {
  if (members.length === 0) {
    return null
  }
  let total = 0
  for (const member of members) {
    const runSize = member.preset?.run_size
    if (!runSize || runSize.status !== 'exact' || runSize.estimated_attack_count === null) {
      return null
    }
    total += runSize.estimated_attack_count
  }
  return total
}

/** Other presets that run the same stored scenario, so an edit there changes them too. */
export function otherSuitesUsing(
  presetName: string,
  suiteName: string,
  suites: ScenarioSuiteResponse[],
): string[] {
  return suites
    .filter((item) => item.suite.name !== suiteName)
    .filter((item) => item.suite.entries.some((entry) => entry.preset_name === presetName))
    .map((item) => item.suite.name)
}

export function formatScenarioCount(count: number): string {
  return `${count} scenario${count === 1 ? '' : 's'}`
}

export function formatAttackCount(count: number): string {
  return `${count.toLocaleString()} attack${count === 1 ? '' : 's'}`
}
