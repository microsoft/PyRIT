import type { Parameter, RegisteredScenario, ScenarioDatasetSourceSettings } from '@/types'

import {
  buildEstimateRequest,
  buildRunRequest,
  linkedDatasetProblem,
  minimumSelectionMessage,
  scenarioDynamicParameters,
  type ScenarioLaunchInput,
  usesScenarioDatasets,
} from './scenarioLaunchRequest'

const OBJECTIVE: Parameter = {
  name: 'objective', description: 'The objective.', type_name: 'str | None', choices: null, default: null, required: false,
}
const HISTORY: Parameter = {
  name: 'prepended_conversation_id', description: 'History.', type_name: 'str | None', choices: null, default: null,
  required: false,
}

function makeScenario(overrides: Partial<RegisteredScenario> = {}): RegisteredScenario {
  return {
    scenario_name: 'garak.api_key',
    supported_parameter_names: [
      'objective_target', 'scenario_techniques', 'dataset_config', 'memory_labels',
      'max_concurrency', 'max_retries', 'include_baseline', 'objective', 'prepended_conversation_id',
    ],
    supported_parameters: [OBJECTIVE, HISTORY],
    uses_default_adversarial_target: true,
    max_concrete_techniques: null,
    ...overrides,
  } as RegisteredScenario
}

function makeInput(overrides: Partial<ScenarioLaunchInput> = {}): ScenarioLaunchInput {
  const scenario = overrides.scenario ?? makeScenario()
  return {
    scenario,
    targetName: 'target',
    adversarialTargetName: 'adversary',
    techniques: ['get_key'],
    dynamicParameters: scenarioDynamicParameters(scenario),
    scenarioParamValues: { objective: 'steal a key', prepended_conversation_id: '' },
    datasetEdits: {},
    datasetError: null,
    includeBaseline: false,
    operationId: 'operation',
    maxConcurrency: 4,
    maxRetries: 1,
    labels: { team: 'red' },
    ...overrides,
  }
}

describe('scenario launch requests', () => {
  it('builds the run request from the same input as the estimate, plus run-only settings', () => {
    const input = makeInput({ datasetEdits: { max_total: 3 }, includeBaseline: true })

    const estimate = buildEstimateRequest(input)
    const run = buildRunRequest(input)

    expect(estimate.ok && run.ok).toBe(true)
    if (!estimate.ok || !run.ok) return
    expect(run.request).toEqual({
      scenario_name: 'garak.api_key',
      ...estimate.request,
      operation_id: 'operation',
      max_concurrency: 4,
      max_retries: 1,
      labels: { team: 'red' },
    })
    expect(estimate.request).toEqual({
      techniques: ['get_key'],
      include_baseline: true,
      target_name: 'target',
      adversarial_target_name: 'adversary',
      datasets: { max_total: 3 },
      scenario_params: { objective: 'steal a key' },
    })
  })

  it('leaves out inputs the scenario does not take', () => {
    const scenario = makeScenario({
      supported_parameter_names: ['objective_target'],
      supported_parameters: [],
      uses_default_adversarial_target: false,
    })

    const run = buildRunRequest(makeInput({ scenario, datasetEdits: { max_total: 3 } }))

    expect(run).toEqual({ ok: true, request: { scenario_name: 'garak.api_key', target_name: 'target', operation_id: 'operation' } })
  })

  it('carries explicitly selected generation and scoring targets through estimates and launches', () => {
    const scenario = makeScenario()
    const input = makeInput({ scenario, adversarialTargetName: 'generation', scoringTargetName: 'rubric' })

    for (const result of [buildEstimateRequest(input), buildRunRequest(input)]) {
      expect(result.ok).toBe(true)
      if (!result.ok) return
      expect(result.request).toMatchObject({
        adversarial_target_name: 'generation',
        scoring_target_name: 'rubric',
      })
    }
  })

  it('omits unchanged datasets and an unset operation', () => {
    const run = buildRunRequest(makeInput({ operationId: null }))

    expect(run.ok && 'datasets' in run.request).toBe(false)
    expect(run.ok && 'operation_id' in run.request).toBe(false)
  })

  it('asks the run to prepare datasets only when the launch does, keeping it out of the estimate', () => {
    const input = makeInput({ prepareDatasetsInBackground: true })
    const run = buildRunRequest(input)
    const estimate = buildEstimateRequest(input)
    const eager = buildRunRequest(makeInput({ prepareDatasetsInBackground: false }))

    expect(run.ok && run.request.prepare_in_background).toBe(true)
    expect(estimate.ok && 'prepare_in_background' in estimate.request).toBe(false)
    expect(eager.ok && 'prepare_in_background' in eager.request).toBe(false)
  })

  const FIXED_SOURCE: ScenarioDatasetSourceSettings = {
    name: 'fixed', max_size: 'all', fetch: 'if_missing', configured_provider: false,
  }
  it.each<[string, Partial<RegisteredScenario>, ScenarioDatasetSourceSettings[], boolean]>([
    ['takes dataset input', {}, [], true],
    ['has default datasets', { supported_parameter_names: ['objective_target'], default_datasets: ['harmbench'] }, [], true],
    ['has fixed sources', { supported_parameter_names: ['objective_target'] }, [FIXED_SOURCE], true],
    ['uses only inline seeds', { supported_parameter_names: ['objective_target'] }, [], false],
  ])('reports whether a scenario that %s has datasets to prepare', (_, overrides, sources, expected) => {
    const scenario = makeScenario({ default_datasets: [], ...overrides })
    const defaults = { sources, max_per_dataset: 'all' as const, max_total: 'all' as const, fetch: 'if_missing' as const, filters: {} }

    expect(usesScenarioDatasets(scenario, defaults)).toBe(expected)
  })

  it.each<[string, Partial<ScenarioLaunchInput>, string]>([
    ['no target', { targetName: '' }, 'Select a target.'],
    ['no technique', { techniques: [] }, 'Select at least one technique.'],
    ['too many techniques', {
      scenario: makeScenario({ max_concrete_techniques: 1 }), techniques: ['get_key', 'complete_key'],
    }, 'Select at most 1 concrete technique(s).'],
    ['pending technique edits', { techniqueEditError: 'Wait for valid technique settings before launch.' },
      'Wait for valid technique settings before launch.'],
    ['invalid datasets', { datasetError: 'Bad dataset limit.' }, 'Bad dataset limit.'],
    ['linked datasets that cannot launch', { linkedDatasetError: 'Linked problem.' }, 'Linked problem.'],
    ['an unsupported conversation', {
      supportsPrependedConversation: false,
      scenarioParamValues: { objective: '', prepended_conversation_id: 'saved' },
    }, 'This technique does not support a prepended conversation. Clear the saved conversation before launch.'],
    ['concurrency out of range', { maxConcurrency: 0 }, 'Max concurrency must be an integer from 1 to 100.'],
    ['fractional retries', { maxRetries: 1.5 }, 'Max retries must be an integer from 0 to 20.'],
  ])('rejects %s with the message the form shows', (_, overrides, error) => {
    expect(buildRunRequest(makeInput(overrides))).toEqual({ ok: false, error })
  })

  it('estimates without a target, since only the launch needs one', () => {
    expect(buildEstimateRequest(makeInput({ targetName: '' })).ok).toBe(true)
  })

  it('does not estimate a configuration whose linked datasets cannot launch', () => {
    expect(buildEstimateRequest(makeInput({ linkedDatasetError: 'Linked problem.' })))
      .toEqual({ ok: false, error: 'Linked problem.' })
  })

  it('reports only linked names the client knows cannot launch, keeping whitespace visible', () => {
    expect(linkedDatasetProblem([], false)).toBeNull()
    expect(linkedDatasetProblem([' returns ', 'returns ', 'missing'], true)).toBeNull()
    expect(linkedDatasetProblem(['ds_a', ' returns ', ''], false)).toBe(
      'This scenario can\'t take the linked datasets: ds_a, " returns ", "".',
    )
    expect(linkedDatasetProblem(['ds_a', ' '], true)).toBeNull()
  })

  it('keeps run-only limits out of the estimate', () => {
    const before = buildEstimateRequest(makeInput())
    const after = buildEstimateRequest(makeInput({ maxConcurrency: 0, maxRetries: -1 }))

    expect(after.ok).toBe(true)
    // An identical payload keeps the estimate key, so editing launch-only fields sends no new estimate.
    expect(after).toEqual(before)
  })

  it.each([[1, 'Select a dataset.'], [2, 'Select at least 2 datasets.']])(
    'words a minimum of %i datasets as validate_minimum_selection does',
    (minimum, message) => {
      expect(minimumSelectionMessage(minimum)).toBe(message)
    },
  )
})
