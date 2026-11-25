import type { RegisteredScenario, ScenarioModule } from '@/types'

import { techniqueSetName } from './scenarioTechniqueSets'

/** Card for scenarios registered outside PyRIT's packages. No package name can contain a hyphen. */
export const OTHER_SCENARIOS_KEY = 'other-scenarios'

const OTHER_SCENARIOS_TITLE = 'Other scenarios'
const OTHER_SCENARIOS_DESCRIPTION = "Scenarios added outside PyRIT's built-in modules."

/** One module card on the Launch tab. */
export interface ScenarioModuleGroup {
  /** The module name, or `OTHER_SCENARIOS_KEY`. Open cards are kept in the URL by this key. */
  readonly key: string
  readonly title: string
  readonly description: string
  /** Every scenario in the module, sorted by name. */
  readonly scenarios: RegisteredScenario[]
}

/** A module card with the scenarios that match a search. */
export interface ModuleSearchResult {
  readonly group: ScenarioModuleGroup
  readonly matches: RegisteredScenario[]
}

type SearchTerm = string | null | undefined

function compareNames(left: string, right: string): number {
  if (left < right) {
    return -1
  }
  return left > right ? 1 : 0
}

function scenarioSearchTerms(scenario: RegisteredScenario): SearchTerm[] {
  return [
    scenario.scenario_name,
    scenario.description,
    scenario.description_markdown,
    scenario.scenario_type,
    scenario.default_technique,
    ...scenario.default_techniques,
    ...scenario.aggregate_techniques,
    ...scenario.aggregate_techniques.map(techniqueSetName),
    ...Object.values(scenario.aggregate_technique_expansions).flat(),
    ...scenario.all_techniques,
    ...scenario.default_datasets,
    ...scenario.default_run_size.datasets.flatMap((dataset) => [
      dataset.name,
      dataset.selection_note,
      ...dataset.configured_caps.map((cap) => cap.label),
    ]),
  ]
}

function includesQuery(terms: SearchTerm[], query: string): boolean {
  return terms.join(' ').toLowerCase().includes(query)
}

/**
 * Groups scenarios into module cards in the order the API lists modules, then scenarios
 * registered outside PyRIT's packages last. Modules without scenarios are left out.
 */
export function groupScenariosByModule(
  scenarios: readonly RegisteredScenario[],
  modules: readonly ScenarioModule[],
): ScenarioModuleGroup[] {
  const scenariosByModule = new Map<string, RegisteredScenario[]>()
  const otherScenarios: RegisteredScenario[] = []
  const sortedScenarios = [...scenarios].sort((left, right) => compareNames(left.scenario_name, right.scenario_name))
  for (const scenario of sortedScenarios) {
    if (scenario.module) {
      scenariosByModule.set(scenario.module, [...(scenariosByModule.get(scenario.module) ?? []), scenario])
    } else {
      otherScenarios.push(scenario)
    }
  }

  const groups: ScenarioModuleGroup[] = []
  for (const module of modules) {
    const moduleScenarios = scenariosByModule.get(module.name)
    if (moduleScenarios) {
      groups.push({
        key: module.name,
        title: module.display_name,
        description: module.description,
        scenarios: moduleScenarios,
      })
      scenariosByModule.delete(module.name)
    }
  }
  // A module registered after the module list loaded still shows, named by its package.
  for (const [name, moduleScenarios] of scenariosByModule) {
    groups.push({ key: name, title: name, description: '', scenarios: moduleScenarios })
  }
  if (otherScenarios.length > 0) {
    groups.push({
      key: OTHER_SCENARIOS_KEY,
      title: OTHER_SCENARIOS_TITLE,
      description: OTHER_SCENARIOS_DESCRIPTION,
      scenarios: otherScenarios,
    })
  }
  return groups
}

/** The scenario's name inside its module card, without the module prefix (`garak.encoding` is `encoding`). */
export function shortScenarioName(scenario: RegisteredScenario): string {
  const prefix = `${scenario.module}.`
  return scenario.module && scenario.scenario_name.startsWith(prefix)
    ? scenario.scenario_name.slice(prefix.length)
    : scenario.scenario_name
}

/**
 * Finds the scenarios that match a search in each module, leaving out modules with no match.
 * A query naming the module lists the whole module; module descriptions never match, since a
 * broad description would otherwise list every scenario in it.
 */
export function filterModuleGroups(groups: readonly ScenarioModuleGroup[], query: string): ModuleSearchResult[] {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) {
    return groups.map((group) => ({ group, matches: group.scenarios }))
  }
  return groups.flatMap((group) => {
    const namesModule = group.key !== OTHER_SCENARIOS_KEY
      && [group.key, group.title].some((name) => name.toLowerCase().includes(normalizedQuery))
    const matches = namesModule
      ? group.scenarios
      : group.scenarios.filter((scenario) => includesQuery(scenarioSearchTerms(scenario), normalizedQuery))
    return matches.length > 0 ? [{ group, matches }] : []
  })
}
