import type { RegisteredScenario, TargetInstance } from '@/types'

import {
  initialScenarioLaunchForm,
  reduceScenarioLaunchForm,
  type ScenarioLaunchFormAction,
  type ScenarioLaunchFormRules,
  type ScenarioLaunchFormState,
} from './scenarioLaunchForm'

const RULES: ScenarioLaunchFormRules = {
  techniqueNames: ['a', 'b', 'c'],
  maxConcreteTechniques: null,
  singleTechnique: false,
}

function state(overrides: Partial<ScenarioLaunchFormState> = {}): ScenarioLaunchFormState {
  return {
    targetName: '',
    adversarialTargetName: '',
    selectedTechniques: ['a'],
    baselineChecked: false,
    datasetEdits: {},
    datasetError: null,
    maxConcurrency: 10,
    maxRetries: 0,
    scenarioParamValues: {},
    ...overrides,
  }
}

function reduce(
  current: ScenarioLaunchFormState,
  actions: ScenarioLaunchFormAction[],
  rules: ScenarioLaunchFormRules = RULES,
): ScenarioLaunchFormState {
  return actions.reduce((next, action) => reduceScenarioLaunchForm(next, action, rules), current)
}

function target(name: string): TargetInstance {
  return { target_registry_name: name, target_type: 'OpenAIChatTarget', identifier: { hash: `${name}-hash` } } as TargetInstance
}

describe('scenario launch form', () => {
  it('starts from defaults that are still available', () => {
    const scenario = {
      baseline_policy: 'enabled',
      include_baseline_by_default: true,
    } as RegisteredScenario
    const initial = initialScenarioLaunchForm({
      scenario,
      targets: [target('chat')],
      defaultObjectiveTarget: target('gone'),
      adversarialTargets: [target('chat')],
      defaultAdversarialTarget: target('chat'),
      defaultTechniques: ['a'],
      dynamicParameters: [],
      initialConversationId: '',
    })

    expect(initial).toMatchObject({
      targetName: '',
      adversarialTargetName: 'chat',
      selectedTechniques: ['a'],
      baselineChecked: true,
      maxConcurrency: 10,
      maxRetries: 0,
    })
  })

  it('never starts with a forbidden baseline', () => {
    const initial = initialScenarioLaunchForm({
      scenario: { baseline_policy: 'forbidden', include_baseline_by_default: true } as RegisteredScenario,
      targets: [],
      defaultObjectiveTarget: null,
      adversarialTargets: [],
      defaultAdversarialTarget: null,
      defaultTechniques: [],
      dynamicParameters: [],
      initialConversationId: '',
    })

    expect(initial.baselineChecked).toBe(false)
  })

  it('adds and removes techniques without duplicates', () => {
    const next = reduce(state(), [
      { type: 'setTechnique', name: 'b', checked: true },
      { type: 'setTechnique', name: 'b', checked: true },
      { type: 'setTechnique', name: 'a', checked: false },
    ])

    expect(next.selectedTechniques).toEqual(['b'])
  })

  it('replaces the selection when only one technique is allowed', () => {
    const next = reduce(state(), [{ type: 'setTechnique', name: 'c', checked: true }], {
      ...RULES, singleTechnique: true, maxConcreteTechniques: 1,
    })

    expect(next.selectedTechniques).toEqual(['c'])
  })

  it('stops adding techniques at the limit', () => {
    const next = reduce(state(), [{ type: 'setTechnique', name: 'b', checked: true }], {
      ...RULES, maxConcreteTechniques: 1,
    })

    expect(next.selectedTechniques).toEqual(['a'])
  })

  it('selects a tag in display order and includes the baseline', () => {
    const next = reduce(state({ selectedTechniques: ['c'] }), [
      { type: 'toggleTag', memberNames: ['b', 'a'], includesBaseline: true, select: true },
    ])

    expect(next.selectedTechniques).toEqual(['a', 'b', 'c'])
    expect(next.baselineChecked).toBe(true)
  })

  it('clears a tag and leaves the baseline alone when it is not a member', () => {
    const next = reduce(state({ selectedTechniques: ['a', 'b', 'c'], baselineChecked: true }), [
      { type: 'toggleTag', memberNames: ['a', 'b'], includesBaseline: false, select: false },
    ])

    expect(next.selectedTechniques).toEqual(['c'])
    expect(next.baselineChecked).toBe(true)
  })

  it('ignores a tag that would go over the limit', () => {
    const current = state()

    const next = reduce(current, [
      { type: 'toggleTag', memberNames: ['b', 'c'], includesBaseline: true, select: true },
    ], { ...RULES, maxConcreteTechniques: 2 })

    expect(next).toBe(current)
  })

  it('drops techniques that are no longer offered', () => {
    const next = reduce(state({ selectedTechniques: ['a', 'removed'] }), [{ type: 'keepAvailableTechniques' }])

    expect(next.selectedTechniques).toEqual(['a'])
  })

  it('clears a saved conversation together with its converter switch', () => {
    const next = reduce(state({
      scenarioParamValues: { prepended_conversation_id: 'saved', apply_converters_to_prepended_history: 'true', objective: 'x' },
    }), [{ type: 'clearPrependedConversation' }])

    expect(next.scenarioParamValues).toEqual({
      prepended_conversation_id: '', apply_converters_to_prepended_history: 'false', objective: 'x',
    })
  })
})
