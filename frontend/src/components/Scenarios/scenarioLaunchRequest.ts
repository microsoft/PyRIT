import { buildParametersFromForm, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import { datasetPolicies, explicitDatasetPolicy, scenarioDatasetDefaults, scenarioDatasetOptions } from './datasetPresentation'
import type {
  Parameter,
  RegisteredComponentReference,
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioDatasetOptions,
  ScenarioDatasetRequest,
  ScenarioDatasetSettings,
  ScenarioRunSizeEstimateRequest,
  TechniqueOverride,
} from '@/types'

/**
 * Common/opaque parameters the base scenario declares via
 * `Scenario._common_scenario_parameters` — the launch form exposes a
 * purpose-built control for each of these (target, techniques, datasets,
 * labels, concurrency, retries, baseline). `technique_converters` and the
 * configured `scoring_target` have no control. They're hidden from the dynamic
 * scenario-specific parameter list.
 */
export const COMMON_SCENARIO_PARAMETER_NAMES = new Set([
  'objective_target',
  'scoring_target',
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

/** Whether a newly generated dataset can be selected: the scenario takes datasets and accepts any dataset name. */
export function acceptsAnyNamedDataset(scenario: RegisteredScenario, options: ScenarioDatasetOptions): boolean {
  return supportsParameter(scenario, 'dataset_config') && options.supports_overrides && options.mode === 'any'
}

/** A scenario that checks stored contents offers only the server's compatible choices, without general dataset actions. */
export function usesCompatibleDatasetPicker(scenario: RegisteredScenario): boolean {
  return scenario.dataset_options?.compatible_only === true
}

/** Whether the scenario declares that a launch needs datasets selected, such as one without defaults. */
export function requiresDatasetSelection(scenario: RegisteredScenario): boolean {
  return (scenario.dataset_options?.min_selected ?? 0) > 0
}

/**
 * The library's message for fewer datasets than a declared minimum, worded as ``validate_minimum_selection``
 * in pyrit/scenario/core/dataset_overrides.py words it. The server enforces the minimum; the form shows this
 * on the dataset picker, since the server checks other fields first and its rejection can be about one of them.
 */
export function minimumSelectionMessage(minimum: number): string {
  return minimum === 1 ? 'Select a dataset.' : `Select at least ${minimum} datasets.`
}

function linkedDatasetLabel(name: string): string {
  return name !== '' && name === name.trim() ? name : JSON.stringify(name)
}

/**
 * Why a link's dataset names can't launch, or null. A scenario without dataset input, or with fixed
 * sources, can't take them; other names, including blank ones, are left for the server to validate.
 */
export function linkedDatasetProblem(names: readonly string[], takesLinkedDatasets: boolean): string | null {
  if (names.length === 0) return null
  if (!takesLinkedDatasets) {
    return `This scenario can't take the linked datasets: ${names.map(linkedDatasetLabel).join(', ')}.`
  }
  return null
}

/** Why the form left out linked names that a compatible-only scenario's listing omits. */
export function omittedLinkedDatasetsMessage(names: readonly string[]): string {
  const labels = names.map(linkedDatasetLabel).join(', ')
  return names.length === 1
    ? `The linked dataset ${labels} isn't a stored dataset this scenario can use, so it wasn't selected.`
    : `The linked datasets ${labels} aren't stored datasets this scenario can use, so they weren't selected.`
}

/** Fixed required sources still need preparation even when the scenario exposes no dataset overrides. */
export function usesScenarioDatasets(
  scenario: RegisteredScenario, settings: ScenarioDatasetSettings = scenarioDatasetDefaults(scenario),
): boolean {
  return supportsParameter(scenario, 'dataset_config') || scenario.default_datasets.length > 0 || settings.sources.length > 0
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
  scoringTargetName?: string
  techniques: string[]
  dynamicParameters: Parameter[]
  scenarioParamValues: Record<string, ParameterFormValue>
  /**
   * Preset values sent verbatim over the built form values: parameters the form has no
   * control for, and rendered ones whose field is untouched.
   */
  presetScenarioParams?: Record<string, unknown>
  datasetEdits: ScenarioDatasetRequest
  datasetError: string | null
  /** Why the linked dataset names can't launch; see `linkedDatasetProblem`. */
  linkedDatasetError?: string | null
  datasetDefaults?: ScenarioDatasetSettings
  datasetOptions?: ScenarioDatasetOptions
  /** Read-only projection requests preserve input policies; launch and preload normalize them. */
  normalizeDatasetLoading?: boolean
  includeBaseline: boolean
  techniqueOverride?: TechniqueOverride | undefined
  techniqueEditError?: string | undefined
  supportsPrependedConversation?: boolean
  supportsPrompt?: boolean
  objectiveScorer?: RegisteredComponentReference | null
  scorerError?: string | undefined
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
  scoringTargetName,
  techniques,
  dynamicParameters,
  scenarioParamValues,
  presetScenarioParams = {},
  datasetEdits,
  datasetError,
  datasetDefaults,
  datasetOptions,
  normalizeDatasetLoading = true,
  includeBaseline,
  techniqueOverride,
  techniqueEditError,
  supportsPrependedConversation = true,
  supportsPrompt = true,
  objectiveScorer,
  scorerError,
  linkedDatasetError,
}: ScenarioLaunchInput): BuildEstimateRequestResult {
  if (linkedDatasetError) return { ok: false, error: linkedDatasetError }
  if (techniqueEditError) return { ok: false, error: techniqueEditError }
  if (scorerError) return { ok: false, error: scorerError }
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
    const prompt = scenarioParamValues.prompt
    if (!supportsPrompt && typeof prompt === 'string' && prompt.trim()) {
      return { ok: false, error: 'This technique cannot send a separate prompt. Clear the prompt or select a compatible technique.' }
    }
    if (!supportsPrependedConversation && typeof history === 'string' && history.trim()) {
      return { ok: false, error: 'This technique does not support a prepended conversation. Clear the saved conversation before launch.' }
    }
    const result = buildParametersFromForm(
      dynamicParameters.filter((parameter) =>
        (supportsPrependedConversation || !CONVERSATION_PARAMETER_NAMES.has(parameter.name))
        && (supportsPrompt || parameter.name !== 'prompt')),
      scenarioParamValues,
    )
    if (!result.ok) {
      return result
    }
    scenarioParams = result.parameters
  }

  const supportsDatasets = supportsParameter(scenario, 'dataset_config')
  if (normalizeDatasetLoading && supportsDatasets && datasetError) return { ok: false, error: datasetError }
  const defaults = datasetDefaults ?? scenarioDatasetDefaults(scenario)
  const options = datasetOptions ?? scenarioDatasetOptions(scenario)
  const sources = datasetEdits.sources ?? defaults.sources
  const policies = usesScenarioDatasets(scenario, defaults) ? datasetPolicies(datasetEdits, defaults, defaults) : []
  const protectedPolicies = policies.filter((source) => {
    const settings = defaults.sources.find((item) => item.name === source.name)
    const explicit = explicitDatasetPolicy(datasetEdits.sources?.find((item) => item.name === source.name)?.fetch)
    return source.fetch !== 'if_missing' && (!supportsDatasets || !options.supports_overrides
      || (options.source_settings === false && (explicit != null || settings?.fetch_inherited !== true)))
  })
  const unknownPolicy = options.supports_overrides && protectedPolicies.some((source) =>
    options.source_settings === false
    && explicitDatasetPolicy(datasetEdits.sources?.find((item) => item.name === source.name)?.fetch) === undefined
    && defaults.sources.find((item) => item.name === source.name)?.fetch_inherited == null)
  if (normalizeDatasetLoading && protectedPolicies.length > 0) return {
    ok: false,
    error: unknownPolicy
      ? 'Source loading-policy inheritance is unknown. Reload scenario metadata or use the SDK/API; this form cannot safely override it.'
      : 'This scenario protects source-specific loading policies. Use the SDK/API to preserve them; scans here only load missing data.',
  }
  const request: ScenarioRunSizeEstimateRequest = {}
  if (scoringTargetName) request.scoring_target_name = scoringTargetName
  if (objectiveScorer) request.objective_scorer = objectiveScorer
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
  if (!normalizeDatasetLoading && supportsDatasets && Object.keys(datasetEdits).length > 0) {
    request.datasets = datasetEdits
  }
  if (normalizeDatasetLoading && supportsDatasets && options.supports_overrides && (
    Object.keys(datasetEdits).length > 0 || defaults.fetch !== 'if_missing'
    || sources.some((source) => source.fetch && source.fetch !== 'if_missing')
  )) {
    request.datasets = {
      ...datasetEdits,
      fetch: 'if_missing',
      ...(options.source_settings === false || sources.length === 0
        || (!datasetEdits.sources && !sources.some((source) => source.fetch && source.fetch !== 'if_missing')) ? {} : {
        sources: sources.map((source) => ({
          ...datasetEdits.sources?.find((item) => item.name === source.name),
          name: source.name, fetch: 'if_missing',
        })),
      }),
    }
  }
  const sentPresetParams = Object.fromEntries(Object.entries(presetScenarioParams).filter(
    ([name]) => supportsPrependedConversation || !CONVERSATION_PARAMETER_NAMES.has(name),
  ))
  const mergedScenarioParams = Object.keys(sentPresetParams).length > 0
    ? { ...(scenarioParams ?? {}), ...sentPresetParams }
    : scenarioParams
  if (mergedScenarioParams) {
    request.scenario_params = mergedScenarioParams
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
  if (usesScenarioDatasets(input.scenario, input.datasetDefaults)) request.prepare_in_background = true
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
