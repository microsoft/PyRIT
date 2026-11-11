import type { Parameter, RegisteredScenario, ScenarioDatasetSettings } from '@/types'

import {
  buildEstimateRequest,
  buildRunRequest,
  linkedDatasetProblem,
  scenarioDynamicParameters,
  usesScenarioDatasets,
  type ScenarioLaunchInput,
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
    default_datasets: [],
    dataset_defaults: { sources: [], max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {} },
    dataset_options: { mode: 'any', allowed_names: [], supports_overrides: true, per_dataset_limit: true, total_limit: true, filters: true },
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
  it('can resolve new parameter defaults despite a stale dataset-table validation error', () => {
    const input = makeInput({ datasetError: 'Select a permitted source.' })
    expect(buildEstimateRequest({ ...input, normalizeDatasetLoading: false }).ok).toBe(true)
    expect(buildRunRequest(input)).toEqual({ ok: false, error: 'Select a permitted source.' })
  })

  it.each([true, false, undefined])('distinguishes inherited and protected compound policies (%s)', (inherited) => {
    const defaults: ScenarioDatasetSettings = {
      sources: [{ name: 'compound', max_size: 5, fetch: 'never', fetch_inherited: inherited, configured_provider: false }],
      max_per_dataset: 5, max_total: 'all', fetch: 'never', filters: {},
    }
    const input = makeInput({
      datasetDefaults: defaults,
      datasetOptions: {
        mode: 'fixed', allowed_names: [], supports_overrides: true, source_settings: false,
        per_dataset_limit: true, total_limit: true, filters: true,
      },
    })
    const result = buildRunRequest(input)
    if (inherited === true) {
      expect(result.ok && result.request.datasets).toEqual({ fetch: 'if_missing' })
    } else {
      expect(result.ok).toBe(false)
      expect(!result.ok && result.error).toMatch(inherited === false ? /protects source-specific/ : /inheritance is unknown/)
    }
  })

  it.each(['if_missing', 'always'] as const)('uses resolved defaults instead of static %s sources', (policy) => {
    const scenario = makeScenario({
      dataset_defaults: {
        sources: [{ name: 'static', max_size: 5, fetch: policy, configured_provider: false }],
        max_per_dataset: 5, max_total: 'all', fetch: policy, filters: {},
      },
    })
    const input = makeInput({ scenario })
    const projection = buildEstimateRequest({ ...input, normalizeDatasetLoading: false })
    expect(projection.ok && projection.request.datasets).toBeUndefined()
    const result = buildRunRequest({
      ...input,
      datasetDefaults: {
        sources: [{ name: 'resolved', max_size: 5, fetch: 'never', fetch_inherited: false, configured_provider: false }],
        max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {},
      },
    })
    expect(result.ok && result.request.datasets).toEqual({
      fetch: 'if_missing', sources: [{ name: 'resolved', fetch: 'if_missing' }],
    })
    expect(input.datasetEdits).toEqual({})
  })

  it('builds the run request from the same input as the estimate, plus run-only settings', () => {
    const input = makeInput({ datasetEdits: { max_total: 3 }, includeBaseline: true })

    const estimate = buildEstimateRequest(input)
    const run = buildRunRequest(input)

    expect(estimate.ok && run.ok).toBe(true)
    if (!estimate.ok || !run.ok) return
    expect(run.request).toEqual({
      scenario_name: 'garak.api_key',
      prepare_in_background: true,
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
      datasets: { max_total: 3, fetch: 'if_missing' },
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

  it.each([false, true])('keeps fixed required sources applicable without dataset overrides (legacy names: %s)', (legacyNames) => {
    const scenario = makeScenario({
      scenario_name: 'garak.exploitation', supported_parameter_names: ['objective_target'], supported_parameters: [],
      default_datasets: legacyNames ? ['required'] : [],
      dataset_defaults: {
        sources: [{ name: 'required', max_size: 'all', fetch: 'if_missing', configured_provider: false }],
        max_per_dataset: 'all', max_total: 'all', fetch: 'if_missing', filters: {},
      },
      dataset_options: {
        mode: 'fixed', allowed_names: [], supports_overrides: false, per_dataset_limit: false,
        total_limit: false, filters: false, source_settings: false,
      },
    })
    expect(usesScenarioDatasets(scenario)).toBe(true)
    const request = buildRunRequest(makeInput({ scenario }))
    expect(request.ok && request.request.prepare_in_background).toBe(true)
    const protectedRequest = buildRunRequest(makeInput({
      scenario,
      datasetDefaults: {
        ...scenario.dataset_defaults,
        sources: [{ name: 'required', max_size: 'all', fetch: 'never', fetch_inherited: false, configured_provider: false }],
        max_per_dataset: 'all', max_total: 'all', fetch: 'never', filters: {},
      },
    }))
    expect(!protectedRequest.ok && protectedRequest.error).toMatch(/protects source-specific loading policies/)
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

  it('keeps already missing-only defaults and omits an unset operation', () => {
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
    ['linked datasets that cannot launch', { linkedDatasetError: 'Linked problem.' }, 'Linked problem.'],
    ['an ASSERT launch without a dataset', {
      scenario: makeScenario({
        scenario_name: 'foundry.assert_policy', default_datasets: [],
        dataset_defaults: { sources: [], max_per_dataset: 'all', max_total: 'all', fetch: 'if_missing', filters: {} },
      }),
    }, 'Select an ASSERT dataset.'],
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
      'This scenario does not take datasets, so it can\'t launch with the linked datasets: ds_a, " returns ", "".',
    )
    expect(linkedDatasetProblem(['ds_a', ' '], true))
      .toBe("The link includes a blank dataset name, so this scan can't launch with it.")
  })

  it('keeps run-only limits out of the estimate', () => {
    const before = buildEstimateRequest(makeInput())
    const after = buildEstimateRequest(makeInput({ maxConcurrency: 0, maxRetries: -1 }))

    expect(after.ok).toBe(true)
    // An identical payload keeps the estimate key, so editing launch-only fields sends no new estimate.
    expect(after).toEqual(before)
  })

  it('uses default ASSERT scoring and rejects saved custom scoring instead of replaying it as defaults', () => {
    const samples: Parameter = {
      name: 'judge_samples', description: 'Samples.', type_name: 'int', choices: null, default: '1', required: false,
    }
    const presets: Parameter = {
      name: 'judge_presets', description: 'Presets.', type_name: 'list[str] | None', choices: null, default: null,
      required: false, is_list: true,
    }
    const prompt: Parameter = {
      name: 'target_system_prompt', description: 'Instructions.', type_name: 'str | None', choices: null,
      default: null, required: false,
    }
    const scenario = makeScenario({
      scenario_name: 'foundry.assert_policy',
      supported_parameter_names: [
        'objective_target', 'scenario_techniques', 'dataset_config', 'judge_samples', 'scoring_target',
        'judge_presets', 'target_system_prompt',
      ],
      supported_parameters: [samples, presets, prompt, {
        name: 'scoring_target', description: 'Judge.', type_name: 'PromptTarget | None', choices: null,
        default: null, required: false,
      }],
    })
    const input = makeInput({
      scenario, scenarioParamValues: {}, presetScenarioParams: { judge_samples: 1 },
      datasetEdits: { sources: [{ name: 'returns_policy' }] },
    })
    expect(scenarioDynamicParameters(scenario)).toEqual([])
    const result = buildRunRequest(input)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.request.scenario_params).toBeUndefined()
    expect(buildRunRequest({ ...input, presetScenarioParams: { judge_samples: 3 } })).toEqual({
      ok: false,
      error: 'This saved ASSERT configuration has custom judge settings. Use the SDK to preserve them; this form uses default scoring.',
    })
    for (const saved of [{ scoring_target: null }, { judge_presets: [] }, { judge_samples: '1' }]) {
      const defaults = buildRunRequest({ ...input, presetScenarioParams: saved })
      expect(defaults.ok).toBe(true)
      if (defaults.ok) expect(defaults.request.scenario_params).toBeUndefined()
    }
    for (const saved of [
      { scoring_target: 'registered-judge' },
      { judge_presets: ['safety-extended'] },
      { target_system_prompt: '' },
      { target_system_prompt: [] },
    ]) {
      expect(buildRunRequest({ ...input, presetScenarioParams: saved })).toEqual({
        ok: false,
        error: 'This saved ASSERT configuration has custom judge settings. Use the SDK to preserve them; this form uses default scoring.',
      })
    }
    for (const saved of [
      { objectiveScorer: { registry_name: 'saved', identifier_hash: 'saved-identity' } },
      { scoringTargetName: 'rubric' },
    ]) {
      expect(buildRunRequest({ ...input, ...saved })).toEqual({
        ok: false,
        error: 'This saved ASSERT configuration uses a custom scorer. Use the SDK to preserve it; this form uses default scoring.',
      })
    }
  })

  it('keeps ASSERT trace settings out of the launch form and rejects saved non-default ones', () => {
    const parameters: Parameter[] = [
      { name: 'trace_source', description: 'Source.', type_name: 'str', choices: null, default: null, required: false },
      {
        name: 'trace_requirement', description: 'Requirement.', type_name: 'str',
        choices: ['none', 'available', 'complete'], default: 'none', required: false,
      },
    ]
    const scenario = makeScenario({
      scenario_name: 'foundry.assert_policy',
      supported_parameter_names: ['objective_target', 'scenario_techniques', 'dataset_config', 'trace_source', 'trace_requirement'],
      supported_parameters: parameters,
    })
    const input = makeInput({
      scenario, scenarioParamValues: {}, presetScenarioParams: { trace_source: null, trace_requirement: 'none' },
      datasetEdits: { sources: [{ name: 'returns_policy' }] },
    })
    expect(scenarioDynamicParameters(scenario)).toEqual([])
    const result = buildRunRequest(input)
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.request.scenario_params).toBeUndefined()
    for (const saved of [{ trace_source: 'travel_agent' }, { trace_requirement: 'complete' }]) {
      expect(buildRunRequest({ ...input, presetScenarioParams: saved })).toEqual({
        ok: false,
        error: 'This saved ASSERT configuration has custom judge settings. Use the SDK to preserve them; this form uses default scoring.',
      })
    }
  })
})
