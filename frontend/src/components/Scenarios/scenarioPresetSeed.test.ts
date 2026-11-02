import { getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import { makeScenario } from '@/test-utils/scenarioFixtures'
import type { Parameter, RegisteredScenario } from '@/types'

import {
  carriedPresetScenarioParams,
  type PresetTechniqueSeed,
  seedPresetTechniques,
  untouchedPresetScenarioParams,
} from './scenarioPresetSeed'

const DEFAULTS = ['default_technique']

function concreteNames(scenario: RegisteredScenario): ReadonlySet<string> {
  const aggregates = new Set(scenario.aggregate_techniques)
  return new Set(scenario.all_techniques.filter((name) => !aggregates.has(name)))
}

function seed(scenario: RegisteredScenario, techniques: string[] | null | undefined): PresetTechniqueSeed {
  return seedPresetTechniques(scenario, techniques, concreteNames(scenario), DEFAULTS)
}

function makeParameter(name: string): Parameter {
  return { name, type_name: 'int', required: false, default: null }
}

describe('seedPresetTechniques', () => {
  it.each([
    ['absent', undefined],
    ['null', null],
    ['empty', []],
  ])('should fall back to the form defaults when the preset techniques are %s', (
    _label: string, techniques: string[] | null | undefined,
  ) => {
    expect(seed(makeScenario(), techniques)).toEqual({ techniques: DEFAULTS, tokensWithoutCheckbox: [] })
  })

  it('should pin concrete techniques in preset order', () => {
    expect(seed(makeScenario(), ['crescendo', 'default_technique'])).toEqual({
      techniques: ['crescendo', 'default_technique'],
      tokensWithoutCheckbox: [],
    })
  })

  it('should expand an aggregate into its concrete members, deduplicated and in order', () => {
    const scenario = makeScenario({
      aggregate_technique_expansions: { all: ['default_technique', 'crescendo'], default: ['default_technique'] },
    })

    expect(seed(scenario, ['crescendo', 'all', 'default'])).toEqual({
      techniques: ['crescendo', 'default_technique'],
      tokensWithoutCheckbox: [],
    })
  })

  it('should expand the default technique set when it is not a concrete option', () => {
    const scenario = makeScenario({
      default_technique: 'recommended',
      aggregate_techniques: ['all'],
      default_techniques: ['crescendo'],
      aggregate_technique_expansions: { all: [] },
    })

    expect(seed(scenario, ['recommended'])).toEqual({ techniques: ['crescendo'], tokensWithoutCheckbox: [] })
  })

  it('should keep an aggregate verbatim when its members are unknown or not all offered here', () => {
    const scenario = makeScenario({
      aggregate_techniques: ['all', 'default', 'extra'],
      aggregate_technique_expansions: { all: [], default: ['default_technique'], extra: ['crescendo', 'retired'] },
    })

    expect(seed(scenario, ['all', 'extra'])).toEqual({
      techniques: ['all', 'extra'],
      tokensWithoutCheckbox: ['all', 'extra'],
    })
  })

  it('should keep converter-qualified and unknown tokens verbatim alongside checkbox techniques', () => {
    expect(seed(makeScenario(), [
      'role_play:converter.translation_spanish', 'crescendo', 'retired', 'role_play:converter.translation_spanish',
    ])).toEqual({
      techniques: ['role_play:converter.translation_spanish', 'crescendo', 'retired'],
      tokensWithoutCheckbox: ['role_play:converter.translation_spanish', 'retired'],
    })
  })
})

describe('carriedPresetScenarioParams', () => {
  it.each([
    ['null', null],
    ['undefined', undefined],
  ])('should return no parameters when the preset parameters are %s', (
    _label: string, params: Record<string, unknown> | null | undefined,
  ) => {
    expect(carriedPresetScenarioParams(params, [makeParameter('iterations')])).toEqual({})
  })

  it('should keep only the parameters the form renders no control for', () => {
    expect(carriedPresetScenarioParams(
      { iterations: 5, custom_key: 'x', nested: { depth: 2 } },
      [makeParameter('iterations')],
    )).toEqual({ custom_key: 'x', nested: { depth: 2 } })
  })
})

describe('untouchedPresetScenarioParams', () => {
  const GOAL_TEXTS: Parameter = { name: 'goal_texts', type_name: 'list[str]', required: false, default: null, is_list: true }
  const SKIP_TEXTS: Parameter = { name: 'skip_texts', type_name: 'list[str]', required: false, default: null, is_list: true }
  const SUFFIX: Parameter = { name: 'suffix', type_name: 'str', required: false, default: null }
  const SCORERS: Parameter = {
    name: 'scorers', type_name: 'list[str]', required: false, default: null, is_list: true, choices: ['refusal', 'harm'],
  }
  const SCORER: Parameter = {
    name: 'scorer',
    type_name: 'ScorerConfig',
    required: false,
    default: null,
    variants: { substring: [GOAL_TEXTS, SUFFIX] },
  }
  const RENDERED = [GOAL_TEXTS, SKIP_TEXTS, SUFFIX, SCORERS, SCORER]
  const PRESET_PARAMS: Record<string, unknown> = {
    goal_texts: ['Hello, world'],
    skip_texts: [],
    suffix: '  padded  ',
    scorers: ['harm', 'refusal'],
    scorer: { type: 'substring', parameters: { goal_texts: ['a, b'], suffix: ' ' } },
  }

  function seededValues(): Record<string, ParameterFormValue> {
    return getInitialFormValues(RENDERED, PRESET_PARAMS)
  }

  it.each([
    ['null', null],
    ['undefined', undefined],
  ])('should return no parameters when the preset parameters are %s', (
    _label: string, params: Record<string, unknown> | null | undefined,
  ) => {
    expect(untouchedPresetScenarioParams(params, RENDERED, seededValues())).toEqual({})
  })

  it('should send every untouched field exactly as saved, even when the form text is lossy', () => {
    expect(untouchedPresetScenarioParams(PRESET_PARAMS, RENDERED, seededValues())).toEqual(PRESET_PARAMS)
  })

  it('should drop edited fields so their built form values apply', () => {
    const scorer = seededValues().scorer as { type: string; values: Record<string, ParameterFormValue> }
    const current: Record<string, ParameterFormValue> = {
      ...seededValues(),
      goal_texts: 'Hello',
      skip_texts: 'extra',
      scorers: ['refusal', 'harm'],
      scorer: { ...scorer, values: { ...scorer.values, suffix: 'edited' } },
    }

    expect(untouchedPresetScenarioParams(PRESET_PARAMS, RENDERED, current)).toEqual({ suffix: '  padded  ' })
  })

  it('should treat a field reverted to its seeded text as untouched', () => {
    const current: Record<string, ParameterFormValue> = { ...seededValues(), goal_texts: 'Hello, world' }

    expect(untouchedPresetScenarioParams(PRESET_PARAMS, RENDERED, current)).toHaveProperty('goal_texts', ['Hello, world'])
  })

  it('should ignore preset parameters without a rendered field', () => {
    expect(untouchedPresetScenarioParams(
      { suffix: 'x', custom_key: 1 },
      [SUFFIX],
      getInitialFormValues([SUFFIX], { suffix: 'x' }),
    )).toEqual({ suffix: 'x' })
  })

  it('should not treat a value seeded from elsewhere as untouched', () => {
    expect(untouchedPresetScenarioParams(
      { suffix: 'preset' },
      [SUFFIX],
      getInitialFormValues([SUFFIX], { suffix: 'conversation-link' }),
    )).toEqual({})
  })
})
