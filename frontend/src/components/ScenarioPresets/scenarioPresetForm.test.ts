import {
  buildScenarioConfig,
  initialScenarioConfigState,
} from '@/components/Scenarios/scenarioConfigForm'
import { makeScenario } from '@/test-utils/scenarioFixtures'
import type { Parameter, ScenarioPreset } from '@/types'

import {
  configToPreset,
  presetToConfigState,
  uneditableScenarioParams,
  validatePresetName,
} from './scenarioPresetForm'

function makePreset(overrides: Partial<ScenarioPreset> = {}): ScenarioPreset {
  return {
    name: 'nightly_probe',
    scenario_name: 'foundry.red_team_agent',
    ...overrides,
  }
}

/** A scenario whose dataset cap resolves to a concrete number, i.e. one this deployment could pin. */
function makeCappedScenario() {
  return makeScenario({
    default_run_size: {
      dataset_size: { kind: 'bounded', value: 40 },
      dataset_limit: { state: 'value', value: 40 },
      estimated_attack_count: 40,
      components: [],
      datasets: [{
        name: 'harmbench',
        kind: 'dataset',
        logical_seed_group_count: 400,
        selected_seed_group_count: 40,
        configured_caps: [{ label: 'cap', count: 40, configured_on: 'dataset', dataset_name: 'harmbench' }],
        selection_note: null,
      }],
      note: null,
    },
  })
}

const IDENTITY = {
  name: 'nightly_probe',
  scenarioName: 'foundry.red_team_agent',
  description: '',
  showInScanner: true,
}
const ITERATION_PARAMETER: Parameter = {
  name: 'max_turns',
  type_name: 'int',
  required: false,
  default: '5',
  description: 'Turn budget.',
}

const CONCURRENCY_PARAMETER: Parameter = {
  name: 'max_concurrency',
  type_name: 'int',
  required: false,
  default: '1',
  description: 'Owned by the launch form, never rendered as a preset field.',
}

describe('validatePresetName', () => {
  it('rejects an empty name', () => {
    expect(validatePresetName('')).toBe('Name is required.')
  })

  it.each([
    ['nightly_probe'],
    ['a'],
    ['a1_b2'],
    [`a${'b'.repeat(63)}`],
  ])('accepts %s', (name) => {
    expect(validatePresetName(name)).toBeNull()
  })

  it.each([
    ['Nightly'],
    ['1nightly'],
    ['nightly-probe'],
    ['nightly probe'],
    ['nightly.probe'],
    [`a${'b'.repeat(64)}`],
  ])('rejects %s', (name) => {
    expect(validatePresetName(name)).not.toBeNull()
  })
})

describe('uneditableScenarioParams', () => {
  it('returns nothing when the preset stores no parameters', () => {
    expect(uneditableScenarioParams(makeScenario(), makePreset())).toEqual({})
    expect(uneditableScenarioParams(makeScenario(), null)).toEqual({})
  })

  it('names the stored keys that have no field, whether undeclared or owned by the launch form', () => {
    const scenario = makeScenario({
      supported_parameters: [ITERATION_PARAMETER, CONCURRENCY_PARAMETER],
    })
    const preset = makePreset({
      scenario_params: { max_turns: 9, retired_knob: 'x', max_concurrency: 4 },
    })

    expect(uneditableScenarioParams(scenario, preset)).toEqual({ retired_knob: 'x', max_concurrency: 4 })
  })
})

describe('presetToConfigState', () => {
  it('falls back to the scenario defaults for every omitted field', () => {
    const scenario = makeScenario()
    const state = presetToConfigState(scenario, makePreset())

    expect(state.techniques).toEqual(['default_technique'])
    expect(state.includeBaseline).toBe(true)
    expect(state.datasets).toEqual({})
  })

  it('expands the stored fields a preset does carry', () => {
    const scenario = makeScenario()
    const state = presetToConfigState(scenario, makePreset({
      techniques: ['crescendo'],
      include_baseline: false,
      datasets: {
        sources: [{ name: 'harmbench' }, { name: 'xstest' }],
        max_total: 25,
        filters: { harm_categories: ['violence'], data_types: ['text'] },
      },
    }))

    expect(state.techniques).toEqual(['crescendo'])
    expect(state.includeBaseline).toBe(false)
    expect(state.datasets).toEqual({
      sources: [{ name: 'harmbench' }, { name: 'xstest' }],
      max_total: 25,
      filters: { harm_categories: ['violence'], data_types: ['text'] },
    })
  })

  it('keeps pinned techniques the scenario no longer offers rather than rewriting the selection', () => {
    const scenario = makeScenario({ all_techniques: ['crescendo', 'default_technique'] })
    const state = presetToConfigState(
      scenario,
      makePreset({ techniques: ['crescendo', 'retired_attack'] }),
    )

    expect(state.techniques).toEqual(['crescendo', 'retired_attack'])
  })

  it('keeps a converter-qualified technique the selector renders no checkbox for', () => {
    const scenario = makeScenario({ all_techniques: ['crescendo', 'default_technique'] })
    const state = presetToConfigState(
      scenario,
      makePreset({ techniques: ['crescendo:converter.translation_spanish'] }),
    )

    expect(state.techniques).toEqual(['crescendo:converter.translation_spanish'])
  })

  it('falls back to the scenario defaults only when the preset pins nothing', () => {
    const scenario = makeScenario({ all_techniques: ['crescendo', 'default_technique'] })
    const state = presetToConfigState(scenario, makePreset({ techniques: [] }))

    expect(state.techniques).toEqual(['default_technique'])
  })

  it('keeps a pinned aggregate technique that the selector renders no checkbox for', () => {
    const scenario = makeScenario({ aggregate_techniques: ['all', 'default'] })
    const state = presetToConfigState(scenario, makePreset({ techniques: ['all'] }))

    expect(state.techniques).toEqual(['all'])
  })

  it('leaves the dataset request empty when the preset omits it, even where the scenario has a cap', () => {
    const state = presetToConfigState(makeCappedScenario(), makePreset())

    expect(state.datasets).toEqual({})
  })

  it('keeps baseline off when the scenario forbids it, whatever the preset stored', () => {
    const scenario = makeScenario({ baseline_policy: 'forbidden' })
    const state = presetToConfigState(scenario, makePreset({ include_baseline: true }))

    expect(state.includeBaseline).toBe(false)
  })

  it('seeds dynamic parameter values from the stored scenario params', () => {
    const scenario = makeScenario({ supported_parameters: [ITERATION_PARAMETER] })
    const state = presetToConfigState(
      scenario,
      makePreset({ scenario_params: { max_turns: 9 } }),
    )

    expect(state.scenarioParamValues.max_turns).toBe('9')
  })
})

describe('configToPreset', () => {
  function buildConfig(overrides: Partial<Parameters<typeof buildScenarioConfig>[0]> = {}) {
    const result = buildScenarioConfig({
      techniques: ['crescendo'],
      dynamicParameters: [],
      scenarioParamValues: {},
      datasets: {},
      includeBaseline: false,
      ...overrides,
    })
    if (!result.ok) {
      throw new Error(result.error)
    }
    return result.config
  }

  it('omits a blank description rather than storing an empty string', () => {
    const scenario = makeScenario()
    const preset = configToPreset(
      { ...IDENTITY, description: '   ' },
      buildConfig(),
      { scenario, previous: null },
    )

    expect(preset).not.toHaveProperty('description')
  })

  it('trims a description it does keep', () => {
    const scenario = makeScenario()
    const preset = configToPreset(
      { ...IDENTITY, description: '  nightly  ' },
      buildConfig(),
      { scenario, previous: null },
    )

    expect(preset.description).toBe('nightly')
  })

  it('keeps the author the server stamped at create', () => {
    const scenario = makeScenario()
    const preset = configToPreset(
      IDENTITY,
      buildConfig(),
      {
        scenario,
        previous: { name: 'nightly', scenario_name: scenario.scenario_name, author: 'Ada Lovelace' },
      },
    )

    expect(preset.author).toBe('Ada Lovelace')
  })

  it('leaves the author unset on create so the server can stamp it', () => {
    const scenario = makeScenario()
    const preset = configToPreset(IDENTITY, buildConfig(), { scenario, previous: null })

    expect(preset).not.toHaveProperty('author')
  })

  it('leaves a listed preset without a scanner visibility field', () => {
    const scenario = makeScenario()
    const preset = configToPreset(
      IDENTITY,
      buildConfig(),
      { scenario, previous: makePreset({ show_in_scanner: false }) },
    )

    expect(preset).not.toHaveProperty('show_in_scanner')
  })

  it('writes the scanner visibility of a hidden preset', () => {
    const scenario = makeScenario()
    const preset = configToPreset(
      { ...IDENTITY, showInScanner: false },
      buildConfig(),
      { scenario, previous: null },
    )

    expect(preset.show_in_scanner).toBe(false)
  })

  it('carries only the fields the operator moved off the scenario default', () => {
    const scenario = makeScenario()
    const preset = configToPreset(IDENTITY, buildConfig(), { scenario, previous: null })

    expect(Object.keys(preset).sort()).toEqual(
      ['include_baseline', 'name', 'scenario_name', 'techniques'],
    )
  })

  it('pins nothing when a new preset leaves every scenario-owned field alone', () => {
    const scenario = makeScenario()
    const state = initialScenarioConfigState(scenario)
    const preset = configToPreset(
      IDENTITY,
      buildConfig({
        techniques: state.techniques,
        includeBaseline: state.includeBaseline,
        datasets: state.datasets,
      }),
      { scenario, previous: null },
    )

    expect(preset).toEqual({ name: IDENTITY.name, scenario_name: IDENTITY.scenarioName })
  })

  it('leaves unpinned fields unset when an unrelated edit round-trips a sparse preset', () => {
    const scenario = makeCappedScenario()
    const original = makePreset({ description: 'Nightly smoke test.' })
    const state = presetToConfigState(scenario, original)

    const roundTripped = configToPreset(
      { ...IDENTITY, description: 'Edited.' },
      buildConfig({
        techniques: state.techniques,
        includeBaseline: state.includeBaseline,
        datasets: state.datasets,
      }),
      { scenario, previous: original },
    )

    expect(roundTripped).toEqual({ ...original, description: 'Edited.' })
  })

  it('keeps a field the preset already pinned even where it equals the scenario default', () => {
    const scenario = makeScenario()
    const original = makePreset({ techniques: ['default_technique'], include_baseline: true })
    const state = presetToConfigState(scenario, original)

    const roundTripped = configToPreset(
      IDENTITY,
      buildConfig({ techniques: state.techniques, includeBaseline: state.includeBaseline }),
      { scenario, previous: original },
    )

    expect(roundTripped.techniques).toEqual(['default_technique'])
    expect(roundTripped.include_baseline).toBe(true)
  })

  it('pins nothing when the default techniques come back in a different order', () => {
    const scenario = makeScenario({
      all_techniques: ['default_technique', 'crescendo'],
      default_techniques: ['default_technique', 'crescendo'],
    })
    const preset = configToPreset(
      IDENTITY,
      buildConfig({ techniques: ['crescendo', 'default_technique'], includeBaseline: true }),
      { scenario, previous: null },
    )

    expect(preset).toEqual({ name: IDENTITY.name, scenario_name: IDENTITY.scenarioName })
  })

  it('still pins techniques when the selection swaps a member rather than reordering', () => {
    const scenario = makeScenario({
      all_techniques: ['default_technique', 'crescendo', 'flip'],
      default_techniques: ['default_technique', 'crescendo'],
    })
    const preset = configToPreset(
      IDENTITY,
      buildConfig({ techniques: ['crescendo', 'flip'], includeBaseline: true }),
      { scenario, previous: null },
    )

    expect(preset.techniques).toEqual(['crescendo', 'flip'])
  })

  it('preserves a stored baseline pin the forbidden policy hides from the form', () => {
    const scenario = makeScenario({ baseline_policy: 'forbidden' })
    const original = makePreset({ include_baseline: true })
    const state = presetToConfigState(scenario, original)

    const roundTripped = configToPreset(
      IDENTITY,
      buildConfig({ includeBaseline: state.includeBaseline }),
      { scenario, previous: original },
    )

    expect(roundTripped.include_baseline).toBe(true)
  })

  it('pins no baseline for a new preset the scenario forbids one on', () => {
    const scenario = makeScenario({ baseline_policy: 'forbidden' })
    const preset = configToPreset(IDENTITY, buildConfig(), { scenario, previous: null })

    expect(preset).not.toHaveProperty('include_baseline')
  })

  it('omits a dynamic parameter left at its declared default', () => {
    const scenario = makeScenario({ supported_parameters: [ITERATION_PARAMETER] })
    const preset = configToPreset(
      IDENTITY,
      buildConfig({
        dynamicParameters: [ITERATION_PARAMETER],
        scenarioParamValues: { max_turns: '5' },
      }),
      { scenario, previous: null },
    )

    expect(preset).not.toHaveProperty('scenario_params')
  })

  it('pins a dynamic parameter the operator moved off its declared default', () => {
    const scenario = makeScenario({ supported_parameters: [ITERATION_PARAMETER] })
    const preset = configToPreset(
      IDENTITY,
      buildConfig({
        dynamicParameters: [ITERATION_PARAMETER],
        scenarioParamValues: { max_turns: '9' },
      }),
      { scenario, previous: null },
    )

    expect(preset.scenario_params).toEqual({ max_turns: 9 })
  })

  it('carries stored parameters this editor renders no control for', () => {
    const scenario = makeScenario({ supported_parameters: [ITERATION_PARAMETER] })
    const original = makePreset({
      scenario_params: { max_turns: 5, retired_knob: 'x', max_concurrency: 4 },
    })
    const state = presetToConfigState(scenario, original)

    const roundTripped = configToPreset(
      IDENTITY,
      buildConfig({
        techniques: state.techniques,
        dynamicParameters: [ITERATION_PARAMETER],
        scenarioParamValues: state.scenarioParamValues,
      }),
      { scenario, previous: original },
    )

    expect(roundTripped.scenario_params).toEqual({ max_turns: 5, retired_knob: 'x', max_concurrency: 4 })
  })

  it('round-trips a fully populated preset back through the form state', () => {
    const scenario = makeScenario()
    const original = makePreset({
      techniques: ['crescendo'],
      include_baseline: false,
      datasets: {
        sources: [{ name: 'harmbench' }],
        max_total: 25,
        filters: { harm_categories: ['violence'] },
      },
    })
    const state = presetToConfigState(scenario, original)

    const roundTripped = configToPreset(
      IDENTITY,
      buildConfig({
        techniques: state.techniques,
        includeBaseline: state.includeBaseline,
        datasets: state.datasets,
      }),
      { scenario, previous: original },
    )

    expect(roundTripped).toEqual(original)
  })
})
