import { buildParametersFromForm, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import type {
  Parameter,
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioDatasetRequest,
  ScenarioRunSizeEstimateRequest,
  TechniqueOverride,
} from '@/types'

/**
 * Common/opaque parameters the base scenario declares via
 * `Scenario._common_scenario_parameters` — the launch form exposes a
 * purpose-built control for each of these (target, techniques, datasets,
 * labels, concurrency, retries, baseline), and `technique_converters` has no
 * UI at all. They're hidden from the dynamic scenario-specific parameter list.
 */
export const COMMON_SCENARIO_PARAMETER_NAMES = new Set([
  'objective_target',
  'scenario_techniques',
  'technique_converters',
  'local_technique_factory',
  'dataset_config',
  'dataset_overrides',
  'memory_labels',
  'max_concurrency',
  'max_retries',
  'include_baseline',
])

export const MIN_MAX_CONCURRENCY = 1
export const MAX_MAX_CONCURRENCY = 100
export const MIN_MAX_RETRIES = 0
export const MAX_MAX_RETRIES = 20
export const DEFAULT_MAX_CONCURRENCY = 10
export const DEFAULT_MAX_RETRIES = 0
export const CONVERSATION_PARAMETER_NAMES = new Set(['prepended_conversation_id', 'apply_converters_to_prepended_history'])
export const OBJECTIVE_PARAMETER_NAMES = new Set(['objective', 'harm_categories'])

export function supportsParameter(scenario: RegisteredScenario, name: string): boolean {
  return scenario.supported_parameter_names.includes(name)
}

/** The scenario-specific parameters the launch form renders, without the common ones. */
export function scenarioDynamicParameters(scenario: RegisteredScenario): Parameter[] {
  return scenario.supported_parameters.filter(
    (parameter) => supportsParameter(scenario, parameter.name)
      && !COMMON_SCENARIO_PARAMETER_NAMES.has(parameter.name),
  )
}

/**
 * The one effective launch configuration. The estimate and the run request are
 * both built from it, so the preview always describes what gets launched.
 */
export interface ScenarioLaunchInput {
  scenario: RegisteredScenario
  targetName: string
  adversarialTargetName: string
  techniques: string[]
  dynamicParameters: Parameter[]
  scenarioParamValues: Record<string, ParameterFormValue>
  datasetEdits: ScenarioDatasetRequest
  datasetError: string | null
  includeBaseline: boolean
  techniqueOverride?: TechniqueOverride
  techniqueEditError?: string
  supportsPrependedConversation?: boolean
  operationId: string | null
  maxConcurrency: number
  maxRetries: number
  labels: Record<string, string>
}

export type BuildEstimateRequestResult =
  | { ok: true, request: ScenarioRunSizeEstimateRequest }
  | { ok: false, error: string }

export type BuildRunRequestResult =
  | { ok: true, request: RunScenarioRequest }
  | { ok: false, error: string }

export function buildEstimateRequest({
  scenario,
  targetName,
  adversarialTargetName,
  techniques,
  dynamicParameters,
  scenarioParamValues,
  datasetEdits,
  datasetError,
  includeBaseline,
  techniqueOverride,
  techniqueEditError,
  supportsPrependedConversation = true,
}: ScenarioLaunchInput): BuildEstimateRequestResult {
  if (techniqueEditError) return { ok: false, error: techniqueEditError }
  if (supportsParameter(scenario, 'scenario_techniques') && techniques.length === 0) {
    return { ok: false, error: 'Select at least one technique.' }
  }
  if (supportsParameter(scenario, 'scenario_techniques')
    && scenario.max_concrete_techniques != null && techniques.length > scenario.max_concrete_techniques) {
    return { ok: false, error: `Select at most ${scenario.max_concrete_techniques} concrete technique(s).` }
  }

  let scenarioParams: Record<string, unknown> | null = null
  if (dynamicParameters.length > 0) {
    const history = scenarioParamValues.prepended_conversation_id
    if (!supportsPrependedConversation && typeof history === 'string' && history.trim()) {
      return { ok: false, error: 'This technique does not support a prepended conversation. Clear the saved conversation before launch.' }
    }
    const result = buildParametersFromForm(
      dynamicParameters.filter((parameter) => supportsPrependedConversation || !CONVERSATION_PARAMETER_NAMES.has(parameter.name)),
      scenarioParamValues,
    )
    if (!result.ok) {
      return result
    }
    scenarioParams = result.parameters
  }

  const supportsDatasets = supportsParameter(scenario, 'dataset_config')
  if (supportsDatasets && datasetError) return { ok: false, error: datasetError }
  const request: ScenarioRunSizeEstimateRequest = {}
  if (techniqueOverride) request.technique_override = techniqueOverride
  if (supportsParameter(scenario, 'scenario_techniques')) {
    request.techniques = techniques
  }
  if (supportsParameter(scenario, 'include_baseline')) {
    request.include_baseline = includeBaseline
  }
  if (supportsParameter(scenario, 'objective_target') && targetName) {
    request.target_name = targetName
  }
  if (scenario.uses_default_adversarial_target && adversarialTargetName) {
    request.adversarial_target_name = adversarialTargetName
  }
  if (supportsDatasets && Object.keys(datasetEdits).length > 0) {
    request.datasets = datasetEdits
  }
  if (scenarioParams) {
    request.scenario_params = scenarioParams
  }
  return { ok: true, request }
}

/** The run request is the estimate request plus the run-only settings, never a separate interpretation. */
export function buildRunRequest(input: ScenarioLaunchInput): BuildRunRequestResult {
  if (supportsParameter(input.scenario, 'objective_target') && !input.targetName) {
    return { ok: false, error: 'Select a target.' }
  }
  const estimateResult = buildEstimateRequest(input)
  if (!estimateResult.ok) {
    return estimateResult
  }
  if (
    supportsParameter(input.scenario, 'max_concurrency') && (
      !Number.isInteger(input.maxConcurrency)
      || input.maxConcurrency < MIN_MAX_CONCURRENCY
      || input.maxConcurrency > MAX_MAX_CONCURRENCY
    )
  ) {
    return {
      ok: false,
      error: `Max concurrency must be an integer from ${MIN_MAX_CONCURRENCY} to ${MAX_MAX_CONCURRENCY}.`,
    }
  }
  if (
    supportsParameter(input.scenario, 'max_retries') && (
      !Number.isInteger(input.maxRetries)
      || input.maxRetries < MIN_MAX_RETRIES
      || input.maxRetries > MAX_MAX_RETRIES
    )
  ) {
    return {
      ok: false,
      error: `Max retries must be an integer from ${MIN_MAX_RETRIES} to ${MAX_MAX_RETRIES}.`,
    }
  }

  const request: RunScenarioRequest = {
    scenario_name: input.scenario.scenario_name,
    ...estimateResult.request,
  }
  if (input.operationId) request.operation_id = input.operationId
  if (supportsParameter(input.scenario, 'max_concurrency')) {
    request.max_concurrency = input.maxConcurrency
  }
  if (supportsParameter(input.scenario, 'max_retries')) {
    request.max_retries = input.maxRetries
  }
  if (supportsParameter(input.scenario, 'memory_labels')) {
    request.labels = input.labels
  }
  return { ok: true, request }
}
