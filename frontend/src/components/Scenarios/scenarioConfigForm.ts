import {
  buildParametersFromForm,
  getInitialFormValues,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import type {
  Parameter,
  RegisteredScenario,
  ScenarioDatasetRequest,
  ScenarioTechniqueSummary,
} from '@/types'

/**
 * Common/opaque parameters every scenario declares via
 * `Scenario._common_scenario_parameters` — the launch form already exposes a
 * purpose-built control for each of these (target, techniques, datasets,
 * labels, concurrency, retries, baseline), and `technique_converters` has no
 * UI at all. They're hidden from the dynamic scenario-specific parameter list.
 */
const COMMON_SCENARIO_PARAMETER_NAMES = new Set([
  'objective_target',
  'scenario_techniques',
  'technique_converters',
  'dataset_config',
  'memory_labels',
  'max_concurrency',
  'max_retries',
  'include_baseline',
])

export const BASELINE_TECHNIQUE: ScenarioTechniqueSummary = {
  name: 'baseline',
  description: 'Sends each objective directly to the target for comparison.',
  tags: ['baseline', 'single_turn'],
}

/** A technique checkbox, including the baseline pseudo-technique the scenario may forbid. */
export interface SelectableTechnique extends ScenarioTechniqueSummary {
  isBaseline: boolean
  disabled: boolean
}

/** The scenario-owned configuration the preset editor collects. */
export interface ScenarioConfigFormState {
  techniques: string[]
  includeBaseline: boolean
  datasets: ScenarioDatasetRequest
  scenarioParamValues: Record<string, ParameterFormValue>
}

/** The resolved scenario-owned fields shared by an estimate request, a run request, and a preset. */
export interface ScenarioConfigFields {
  techniques: string[]
  include_baseline: boolean
  datasets?: ScenarioDatasetRequest
  scenario_params?: Record<string, unknown>
}

export type BuildScenarioConfigResult =
  | { ok: true; config: ScenarioConfigFields }
  | { ok: false; error: string }

interface TechniqueOptions {
  techniques: ScenarioTechniqueSummary[]
  defaultTechniques: string[]
}

/**
 * Whether a dataset request carries nothing the scenario would not already do.
 *
 * The table always hands back a populated object, so without this an untouched editor
 * would write `{max_per_dataset: 'default', max_total: 'default'}` into every stored
 * document. A preset that says nothing about datasets should stay silent about them.
 */
export function datasetRequestIsEmpty(request: ScenarioDatasetRequest): boolean {
  const isDefaultLimit = (value: ScenarioDatasetRequest['max_total']) => value === undefined || value === 'default'
  return (
    (request.sources ?? []).length === 0 &&
    isDefaultLimit(request.max_per_dataset) &&
    isDefaultLimit(request.max_total) &&
    (request.fetch === undefined || request.fetch === null || request.fetch === 'default') &&
    Object.keys(request.filters ?? {}).length === 0
  )
}

export function uniqueTechniqueOptions(scenario: RegisteredScenario): TechniqueOptions {
  const aggregateNames = new Set(scenario.aggregate_techniques)
  const summariesByName = new Map(
    scenario.technique_summaries.map((summary) => [summary.name, summary]),
  )
  const techniques: ScenarioTechniqueSummary[] = []
  const seen = new Set<string>()
  for (const name of scenario.all_techniques) {
    if (!aggregateNames.has(name) && !seen.has(name)) {
      techniques.push(summariesByName.get(name) ?? { name, description: null, tags: [] })
      seen.add(name)
    }
  }
  const concreteNames = new Set(techniques.map((technique) => technique.name))
  const defaultTechniques = scenario.default_techniques.filter((name) => concreteNames.has(name))
  if (defaultTechniques.length === 0 && concreteNames.has(scenario.default_technique)) {
    defaultTechniques.push(scenario.default_technique)
  }
  return { techniques, defaultTechniques }
}

/** Scenario-specific parameters, excluding the common ones that already have a purpose-built control. */
export function dynamicScenarioParameters(scenario: RegisteredScenario): Parameter[] {
  return scenario.supported_parameters.filter(
    (parameter) => !COMMON_SCENARIO_PARAMETER_NAMES.has(parameter.name),
  )
}

export function buildSelectableTechniques(
  techniqueOptions: ScenarioTechniqueSummary[],
  isBaselineForbidden: boolean,
): SelectableTechnique[] {
  return [
    { ...BASELINE_TECHNIQUE, isBaseline: true, disabled: isBaselineForbidden },
    ...techniqueOptions.map((technique) => ({ ...technique, isBaseline: false, disabled: false })),
  ]
}

export function initialScenarioConfigState(scenario: RegisteredScenario): ScenarioConfigFormState {
  return {
    techniques: uniqueTechniqueOptions(scenario).defaultTechniques,
    includeBaseline: scenario.baseline_policy !== 'forbidden' && scenario.include_baseline_by_default,
    datasets: {},
    scenarioParamValues: getInitialFormValues(dynamicScenarioParameters(scenario)),
  }
}

interface BuildScenarioConfigInput {
  techniques: string[]
  dynamicParameters: Parameter[]
  scenarioParamValues: Record<string, ParameterFormValue>
  datasets: ScenarioDatasetRequest
  includeBaseline: boolean
}

/**
 * Resolves the scenario-owned form state into the fields an estimate, a run, or a preset carries.
 * Fields the operator left blank are omitted rather than sent as an empty value, so the scenario's
 * own default still applies downstream.
 */
export function buildScenarioConfig({
  techniques,
  dynamicParameters,
  scenarioParamValues,
  datasets,
  includeBaseline,
}: BuildScenarioConfigInput): BuildScenarioConfigResult {
  if (techniques.length === 0) {
    return { ok: false, error: 'Select at least one technique.' }
  }

  let scenarioParams: Record<string, unknown> | null = null
  if (dynamicParameters.length > 0) {
    const result = buildParametersFromForm(dynamicParameters, scenarioParamValues)
    if (!result.ok) {
      return result
    }
    scenarioParams = result.parameters
  }

  const config: ScenarioConfigFields = { techniques, include_baseline: includeBaseline }
  if (!datasetRequestIsEmpty(datasets)) {
    config.datasets = datasets
  }
  if (scenarioParams) {
    config.scenario_params = scenarioParams
  }
  return { ok: true, config }
}
