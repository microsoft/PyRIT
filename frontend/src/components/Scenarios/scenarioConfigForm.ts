import {
  buildParametersFromForm,
  getInitialFormValues,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import type { Parameter, RegisteredScenario, ScenarioTechniqueSummary } from '@/types'

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

/** The scenario-owned configuration both the launch form and the preset editor collect. */
export interface ScenarioConfigFormState {
  techniques: string[]
  includeBaseline: boolean
  datasetOverride: string
  maxDatasetSize: string
  harmCategoriesFilter: string
  dataTypesFilter: string
  scenarioParamValues: Record<string, ParameterFormValue>
}

/** The resolved scenario-owned fields shared by an estimate request, a run request, and a preset. */
export interface ScenarioConfigFields {
  techniques: string[]
  include_baseline: boolean
  dataset_names?: string[]
  max_dataset_size?: number
  dataset_filters?: Record<string, string[]>
  scenario_params?: Record<string, unknown>
}

export type BuildScenarioConfigResult =
  | { ok: true; config: ScenarioConfigFields }
  | { ok: false; error: string }

interface TechniqueOptions {
  techniques: ScenarioTechniqueSummary[]
  defaultTechniques: string[]
}

export function parseDatasetNames(datasetOverride: string): string[] {
  return datasetOverride
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
}

/**
 * The scenario's own declared dataset cap, or `''` when it declares none and when the
 * scenario sizes itself by prompt generation instead. Read from the scenario's declared
 * limit rather than counted from the seeds this deployment happens to have loaded, so the
 * value means the same thing everywhere.
 */
export function defaultMaxDatasetSize(scenario: RegisteredScenario): string {
  const limit = scenario.default_run_size.dataset_limit
  return limit.state === 'value' ? String(limit.value) : ''
}

/** A scenario sized by prompt generation has no dataset cap to set. */
export function datasetSizeNotApplicable(scenario: RegisteredScenario): boolean {
  return scenario.default_run_size.dataset_limit.state === 'not_applicable'
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
    datasetOverride: '',
    maxDatasetSize: defaultMaxDatasetSize(scenario),
    harmCategoriesFilter: '',
    dataTypesFilter: '',
    scenarioParamValues: getInitialFormValues(dynamicScenarioParameters(scenario)),
  }
}

interface BuildScenarioConfigInput {
  techniques: string[]
  dynamicParameters: Parameter[]
  scenarioParamValues: Record<string, ParameterFormValue>
  datasetOverride: string
  maxDatasetSize: string
  harmCategoriesFilter: string
  dataTypesFilter: string
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
  datasetOverride,
  maxDatasetSize,
  harmCategoriesFilter,
  dataTypesFilter,
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

  let maxDatasetSizeValue: number | undefined
  const trimmedMaxDatasetSize = maxDatasetSize.trim()
  if (trimmedMaxDatasetSize.length > 0) {
    const parsed = Number(trimmedMaxDatasetSize)
    if (!Number.isInteger(parsed) || parsed < 1) {
      return { ok: false, error: 'Max dataset size must be a positive integer.' }
    }
    maxDatasetSizeValue = parsed
  }

  const config: ScenarioConfigFields = { techniques, include_baseline: includeBaseline }
  const datasetNames = parseDatasetNames(datasetOverride)
  if (datasetNames.length > 0) {
    config.dataset_names = datasetNames
  }
  if (maxDatasetSizeValue !== undefined) {
    config.max_dataset_size = maxDatasetSizeValue
  }
  const harmCategories = parseDatasetNames(harmCategoriesFilter)
  const dataTypes = parseDatasetNames(dataTypesFilter)
  if (harmCategories.length > 0 || dataTypes.length > 0) {
    config.dataset_filters = {
      ...(harmCategories.length > 0 ? { harm_categories: harmCategories } : {}),
      ...(dataTypes.length > 0 ? { data_types: dataTypes } : {}),
    }
  }
  if (scenarioParams) {
    config.scenario_params = scenarioParams
  }
  return { ok: true, config }
}
