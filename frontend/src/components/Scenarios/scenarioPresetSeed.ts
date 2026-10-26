import { getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import type { Parameter, RegisteredScenario } from '@/types'

import { techniqueSetMembers } from './scenarioTechniqueSets'

export interface PresetTechniqueSeed {
  readonly techniques: string[]
  /** Seeded tokens the launch form has no checkbox for; they are sent verbatim. */
  readonly tokensWithoutCheckbox: string[]
}

/**
 * Seeds the launch form's technique selection from a preset's pinned tokens.
 *
 * An aggregate expands into its concrete members so they show as checked boxes. Every
 * other token — converter-qualified, an aggregate whose members are not all offered
 * here, or a technique this deployment dropped — is kept verbatim for the server to
 * resolve, because dropping it would quietly run less than the preset says.
 */
export function seedPresetTechniques(
  scenario: RegisteredScenario,
  presetTechniques: string[] | null | undefined,
  concreteNames: ReadonlySet<string>,
  defaultTechniques: string[],
): PresetTechniqueSeed {
  if (!presetTechniques || presetTechniques.length === 0) {
    return { techniques: defaultTechniques, tokensWithoutCheckbox: [] }
  }
  const aggregateNames = new Set(scenario.aggregate_techniques)
  if (!concreteNames.has(scenario.default_technique)) {
    aggregateNames.add(scenario.default_technique)
  }
  const techniques: string[] = []
  const tokensWithoutCheckbox: string[] = []
  const seen = new Set<string>()
  const select = (name: string): void => {
    if (!seen.has(name)) {
      seen.add(name)
      techniques.push(name)
    }
  }
  for (const token of presetTechniques) {
    if (concreteNames.has(token)) {
      select(token)
      continue
    }
    const members = aggregateNames.has(token) ? techniqueSetMembers(scenario, token) : []
    if (members.length > 0 && members.every((name) => concreteNames.has(name))) {
      for (const name of members) {
        select(name)
      }
    } else if (!seen.has(token)) {
      select(token)
      tokensWithoutCheckbox.push(token)
    }
  }
  return { techniques, tokensWithoutCheckbox }
}

/**
 * Preset scenario parameters the launch form renders no control for. They travel to
 * the run unchanged, as they would if the preset ran as saved.
 */
export function carriedPresetScenarioParams(
  presetParams: Record<string, unknown> | null | undefined,
  renderedParameters: Parameter[],
): Record<string, unknown> {
  if (!presetParams) {
    return {}
  }
  const rendered = new Set(renderedParameters.map((parameter) => parameter.name))
  return Object.fromEntries(Object.entries(presetParams).filter(([name]) => !rendered.has(name)))
}

/**
 * Preset scenario parameters whose rendered field still shows the value the preset
 * seeded. The form's text round trip is lossy — a list entry containing a comma, an
 * explicit empty list, or surrounding whitespace would change — so an untouched field
 * launches exactly as saved.
 */
export function untouchedPresetScenarioParams(
  presetParams: Record<string, unknown> | null | undefined,
  renderedParameters: Parameter[],
  currentValues: Record<string, ParameterFormValue>,
): Record<string, unknown> {
  if (!presetParams) {
    return {}
  }
  const seededParameters = renderedParameters.filter(
    (parameter) => Object.prototype.hasOwnProperty.call(presetParams, parameter.name),
  )
  const seededValues = getInitialFormValues(seededParameters, presetParams)
  return Object.fromEntries(
    seededParameters
      .filter((parameter) => sameFormValue(currentValues[parameter.name], seededValues[parameter.name]))
      .map((parameter) => [parameter.name, presetParams[parameter.name]]),
  )
}

function sameFormValue(left: ParameterFormValue | undefined, right: ParameterFormValue | undefined): boolean {
  if (typeof left === 'string' || typeof right === 'string' || left === undefined || right === undefined) {
    return left === right
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((entry, index) => entry === right[index])
  }
  const names = new Set([...Object.keys(left.values), ...Object.keys(right.values)])
  return left.type === right.type
    && [...names].every((name) => sameFormValue(left.values[name], right.values[name]))
}
