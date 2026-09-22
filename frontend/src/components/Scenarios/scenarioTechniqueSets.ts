import type { RegisteredScenario } from '@/types'

const TECHNIQUE_SET_LABELS: Record<string, string> = {
  all: 'All',
  core: 'Core',
  default: 'Recommended',
  extra: 'Extra',
  light: 'Light',
  multi_turn: 'Multi-turn',
  single_turn: 'Single-turn',
}

function humanizeTechniqueSetName(name: string): string {
  const knownLabel = TECHNIQUE_SET_LABELS[name]
  if (knownLabel) {
    return knownLabel
  }
  const words = name.replace(/_/g, ' ')
  return words.length > 0 ? `${words[0].toUpperCase()}${words.slice(1)}` : name
}

export function techniqueSetMembers(scenario: RegisteredScenario, name: string): string[] {
  const members = scenario.aggregate_technique_expansions[name]
    ?? (name === scenario.default_technique ? scenario.default_techniques : [])
  return [...new Set(members)]
}

export function techniqueSetName(name: string): string {
  return humanizeTechniqueSetName(name)
}

export function techniqueSetDisplayName(scenario: RegisteredScenario, name: string): string {
  const displayName = techniqueSetName(name)
  return name === scenario.default_technique ? `${displayName} (default)` : displayName
}

export function techniqueSetOptionLabel(scenario: RegisteredScenario, name: string): string {
  const count = techniqueSetMembers(scenario, name).length
  const countLabel = `${count.toLocaleString()} technique${count === 1 ? '' : 's'}`
  const displayName = techniqueSetDisplayName(scenario, name)
  return name === scenario.default_technique
    ? `${displayName} — ${countLabel}`
    : `${displayName} (${countLabel})`
}
