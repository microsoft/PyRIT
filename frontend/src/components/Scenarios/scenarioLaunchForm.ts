import { getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import type {
  Parameter,
  RegisteredScenario,
  ScenarioDatasetRequest,
  ScenarioTechniqueSummary,
  TargetInstance,
} from '@/types'
import { sameTarget } from '@/utils/targetIdentity'

import { DEFAULT_MAX_CONCURRENCY, DEFAULT_MAX_RETRIES } from './scenarioLaunchRequest'

export interface TechniqueOptions {
  techniques: ScenarioTechniqueSummary[]
  defaultTechniques: string[]
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

export interface SelectableTechnique extends ScenarioTechniqueSummary {
  isBaseline: boolean
  disabled: boolean
}

export const BASELINE_TECHNIQUE: ScenarioTechniqueSummary = {
  name: 'baseline',
  description: 'Sends each objective directly to the target for comparison.',
  tags: ['baseline', 'single_turn'],
}

/** The related launch-form fields that change together through explicit actions. */
export interface ScenarioLaunchFormState {
  targetName: string
  adversarialTargetName: string
  selectedTechniques: string[]
  baselineChecked: boolean
  datasetEdits: ScenarioDatasetRequest
  datasetError: string | null
  maxConcurrency: number
  maxRetries: number
  scenarioParamValues: Record<string, ParameterFormValue>
}

/** Technique selection rules fixed for one scenario. */
export interface ScenarioLaunchFormRules {
  /** Concrete technique names in display order. */
  techniqueNames: string[]
  maxConcreteTechniques: number | null
  singleTechnique: boolean
}

export type ScenarioLaunchFormAction =
  | { type: 'setTarget', name: string }
  | { type: 'setAdversarialTarget', name: string }
  | { type: 'setTechnique', name: string, checked: boolean }
  | { type: 'replaceTechniques', names: string[] }
  | { type: 'keepAvailableTechniques' }
  | { type: 'setBaseline', checked: boolean }
  | { type: 'toggleTag', memberNames: string[], includesBaseline: boolean, select: boolean }
  | { type: 'setDatasetEdits', edits: ScenarioDatasetRequest }
  | { type: 'setDatasetError', error: string | null }
  | { type: 'setMaxConcurrency', value: number }
  | { type: 'setMaxRetries', value: number }
  | { type: 'setParam', name: string, value: ParameterFormValue }
  | { type: 'clearPrependedConversation' }

interface InitialFormInput {
  scenario: RegisteredScenario
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  adversarialTargets: TargetInstance[]
  defaultAdversarialTarget: TargetInstance | null
  defaultTechniques: string[]
  dynamicParameters: Parameter[]
  initialConversationId: string
}

function defaultTargetName(target: TargetInstance | null, available: TargetInstance[]): string {
  return target && available.some((candidate) => sameTarget(candidate, target)) ? target.target_registry_name : ''
}

export function initialScenarioLaunchForm({
  scenario,
  targets,
  defaultObjectiveTarget,
  adversarialTargets,
  defaultAdversarialTarget,
  defaultTechniques,
  dynamicParameters,
  initialConversationId,
}: InitialFormInput): ScenarioLaunchFormState {
  return {
    targetName: defaultTargetName(defaultObjectiveTarget, targets),
    adversarialTargetName: defaultTargetName(defaultAdversarialTarget, adversarialTargets),
    selectedTechniques: defaultTechniques,
    baselineChecked: scenario.baseline_policy !== 'forbidden' && scenario.include_baseline_by_default,
    datasetEdits: {},
    datasetError: null,
    maxConcurrency: DEFAULT_MAX_CONCURRENCY,
    maxRetries: DEFAULT_MAX_RETRIES,
    scenarioParamValues: getInitialFormValues(
      dynamicParameters,
      initialConversationId ? { prepended_conversation_id: initialConversationId } : undefined,
    ),
  }
}

/** True when selecting every named technique would go over the scenario's technique limit. */
export function exceedsTechniqueLimit(
  selected: string[],
  added: Iterable<string>,
  maxConcreteTechniques: number | null,
): boolean {
  return maxConcreteTechniques != null && new Set([...selected, ...added]).size > maxConcreteTechniques
}

function selectTechnique(
  current: string[],
  name: string,
  checked: boolean,
  rules: ScenarioLaunchFormRules,
): string[] {
  if (!checked) return current.filter((selected) => selected !== name)
  if (rules.singleTechnique) return [name]
  if (rules.maxConcreteTechniques != null && current.length >= rules.maxConcreteTechniques) return current
  return current.includes(name) ? current : [...current, name]
}

export function reduceScenarioLaunchForm(
  state: ScenarioLaunchFormState,
  action: ScenarioLaunchFormAction,
  rules: ScenarioLaunchFormRules,
): ScenarioLaunchFormState {
  switch (action.type) {
    case 'setTarget':
      return { ...state, targetName: action.name }
    case 'setAdversarialTarget':
      return { ...state, adversarialTargetName: action.name }
    case 'setTechnique':
      return { ...state, selectedTechniques: selectTechnique(state.selectedTechniques, action.name, action.checked, rules) }
    case 'replaceTechniques':
      return { ...state, selectedTechniques: action.names }
    case 'keepAvailableTechniques':
      return {
        ...state,
        selectedTechniques: state.selectedTechniques.filter((name) => rules.techniqueNames.includes(name)),
      }
    case 'setBaseline':
      return { ...state, baselineChecked: action.checked }
    case 'toggleTag': {
      if (action.select && exceedsTechniqueLimit(state.selectedTechniques, action.memberNames, rules.maxConcreteTechniques)) {
        return state
      }
      const selected = new Set(state.selectedTechniques)
      for (const name of action.memberNames) {
        if (action.select) selected.add(name)
        else selected.delete(name)
      }
      return {
        ...state,
        selectedTechniques: rules.techniqueNames.filter((name) => selected.has(name)),
        baselineChecked: action.includesBaseline ? action.select : state.baselineChecked,
      }
    }
    case 'setDatasetEdits':
      return { ...state, datasetEdits: action.edits }
    case 'setDatasetError':
      return { ...state, datasetError: action.error }
    case 'setMaxConcurrency':
      return { ...state, maxConcurrency: action.value }
    case 'setMaxRetries':
      return { ...state, maxRetries: action.value }
    case 'setParam':
      return { ...state, scenarioParamValues: { ...state.scenarioParamValues, [action.name]: action.value } }
    case 'clearPrependedConversation':
      return {
        ...state,
        scenarioParamValues: {
          ...state.scenarioParamValues,
          prepended_conversation_id: '',
          apply_converters_to_prepended_history: 'false',
        },
      }
  }
}
