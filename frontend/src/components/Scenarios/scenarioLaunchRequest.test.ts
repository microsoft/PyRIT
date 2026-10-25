import type { Parameter, RegisteredScenario } from '@/types'

import {
  buildEstimateRequest,
  buildRunRequest,
  scenarioDynamicParameters,
  type ScenarioLaunchInput,
} from './scenarioLaunchRequest'

const OBJECTIVE: Parameter = {
  name: 'objective', description: 'The objective.', param_type: 'str', choices: null, default: null, required: false,
} as unknown as Parameter
const HISTORY: Parameter = {
  name: 'prepended_conversation_id', description: 'History.', param_type: 'str', choices: null, default: null,
  required: false,
} as unknown as Parameter

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

  it('omits unchanged datasets and an unset operation', () => {
    const run = buildRunRequest(makeInput({ operationId: null }))

    expect(run.ok && 'datasets' in run.request).toBe(false)
    expect(run.ok && 'operation_id' in run.request).toBe(false)
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

  it('keeps run-only limits out of the estimate', () => {
    expect(buildEstimateRequest(makeInput({ maxConcurrency: 0, maxRetries: -1 })).ok).toBe(true)
  })
})
