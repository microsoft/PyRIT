import {
  assertBehaviorErrors,
  assertDraftFromRequest,
  assertSettingsErrors,
  buildAssertRequest,
  defaultAssertSettings,
  newDimensionDraft,
  newLevelDraft,
  stratifyOptions,
  suiteDatasetName,
  type AssertSettingsDraft,
} from './assertRequestDraft'

const BEHAVIOR = { behaviorName: ' returns ', behaviorDescription: 'Explain return policies.' }

function withDimensions(settings: AssertSettingsDraft, explicit: boolean): AssertSettingsDraft {
  return {
    ...settings,
    dimensionMode: explicit ? 'explicit' : 'describe',
    dimensions: [{
      id: 'd1',
      name: ' channel ',
      description: ' Where users write. ',
      levels: [
        { id: 'l1', name: ' chat ', definition: ' Web chat. ' },
        { id: 'l2', name: 'email', definition: 'Email.' },
      ],
    }],
  }
}

describe('assertRequestDraft', () => {
  it('builds the default request with trimmed names and server defaults', () => {
    const settings = defaultAssertSettings()
    expect(assertSettingsErrors(settings)).toEqual([])
    expect(buildAssertRequest(settings, BEHAVIOR)).toEqual({
      behavior_name: 'returns',
      behavior_description: 'Explain return policies.',
      context: '',
      group_count: 25,
      include_user_prompt: true,
      include_adversarial_prompt: false,
      behavior_category_count: 25,
      dimensions: [],
      dimension_level_count: 3,
      sampling: { method: 'pairwise', stratify_by: ['behavior'], replication: 'balanced', with_replacement: true },
      random_seed: 0,
      web_search: false,
      target_system_prompt: null,
      target_owns_instructions: false,
    })
  })

  it('sends levels only for explicit dimensions and only the chosen sampling options', () => {
    const described = buildAssertRequest(withDimensions(defaultAssertSettings(), false), BEHAVIOR)
    expect(described.dimensions).toEqual([{ name: 'channel', description: 'Where users write.', levels: [] }])
    const explicit = buildAssertRequest({
      ...withDimensions(defaultAssertSettings(), true),
      samplingMethod: 'stratified',
      stratifyBy: ['channel', 'removed'],
      replication: 'none',
      withReplacement: false,
      instructionMode: 'custom',
      targetSystemPrompt: 'You are a store assistant.',
    }, BEHAVIOR)
    expect(explicit.dimensions[0]?.levels).toEqual([
      { name: 'chat', definition: 'Web chat.' },
      { name: 'email', definition: 'Email.' },
    ])
    expect(explicit.sampling).toEqual({
      method: 'stratified', stratify_by: ['channel'], replication: 'balanced', with_replacement: true,
    })
    expect(explicit.target_system_prompt).toBe('You are a store assistant.')
    const owned = buildAssertRequest({ ...defaultAssertSettings(), instructionMode: 'target' }, BEHAVIOR)
    expect(owned).toEqual(expect.objectContaining({ target_system_prompt: null, target_owns_instructions: true }))
  })

  it('reports invalid settings the server would reject', () => {
    const errors = assertSettingsErrors({
      ...defaultAssertSettings(),
      groupCount: '0',
      includeUserPrompt: false,
      behaviorCategoryCount: 'many',
      dimensionLevelCount: '1',
      samplingMethod: 'stratified',
      stratifyBy: [],
      randomSeed: '1.5',
      instructionMode: 'custom',
      targetSystemPrompt: ' ',
      dimensions: [
        { id: 'a', name: 'behavior', description: 'x', levels: [] },
        { id: 'b', name: '', description: '', levels: [] },
      ],
    })
    expect(errors).toEqual([
      'Seed groups must be a whole number from 1 to 100,000.',
      'Include a user prompt, an adversarial prompt, or both.',
      'Suggested categories must be a whole number from 1 to 1,000.',
      'Levels per dimension must be a whole number from 2 to 100.',
      '"behavior" is reserved for the taxonomy; rename the dimension.',
      'Dimension 2 needs a name.',
      'Dimension 2 needs a description.',
      'Choose at least one dimension to stratify by.',
      'Random seed must be a whole number.',
      'Enter the target system prompt, or choose another instruction option.',
    ])
  })

  it('checks explicit levels', () => {
    const settings = withDimensions(defaultAssertSettings(), true)
    const [dimension] = settings.dimensions
    const repeated = { ...settings, dimensions: [dimension!, dimension!] }
    expect(assertSettingsErrors(repeated)).toContain('Dimension names must be unique; "channel" is repeated.')
    const tooFew = { ...settings, dimensions: [{ ...dimension!, levels: [newLevelDraft()] }] }
    expect(assertSettingsErrors(tooFew)).toEqual([
      'Dimension "channel" needs at least 2 levels.',
      'Every level of dimension "channel" needs a name and a definition.',
    ])
    const duplicateLevels = {
      ...settings,
      dimensions: [{ ...dimension!, levels: [dimension!.levels[1]!, dimension!.levels[1]!] }],
    }
    expect(assertSettingsErrors(duplicateLevels)).toEqual(['Dimension "channel" repeats the level "email".'])
    expect(newDimensionDraft('explicit').levels).toHaveLength(2)
    expect(newDimensionDraft('describe').levels).toHaveLength(0)
    expect(stratifyOptions(settings)).toEqual(['behavior', 'channel'])
  })

  it('requires a behavior name and policy text', () => {
    expect(assertBehaviorErrors({ behaviorName: ' ', behaviorDescription: '' }))
      .toEqual(['Enter a behavior name.', 'Enter the policy text.'])
  })

  it('reads a stored request back into a draft', () => {
    const request = buildAssertRequest({
      ...withDimensions(defaultAssertSettings(), true),
      samplingMethod: 'full_factorial',
      replication: 'none',
      webSearch: true,
      instructionMode: 'custom',
      targetSystemPrompt: 'Be careful.',
      context: 'A store.',
    }, BEHAVIOR)
    const { behavior, settings } = assertDraftFromRequest({ ...request })
    expect(behavior).toEqual({ behaviorName: 'returns', behaviorDescription: 'Explain return policies.' })
    expect(settings).toEqual(expect.objectContaining({
      context: 'A store.',
      dimensionMode: 'explicit',
      samplingMethod: 'full_factorial',
      replication: 'none',
      webSearch: true,
      instructionMode: 'custom',
      targetSystemPrompt: 'Be careful.',
    }))
    expect(buildAssertRequest(settings, behavior)).toEqual(request)
    expect(assertDraftFromRequest({ target_owns_instructions: true, sampling: { method: 'bogus' } }).settings)
      .toEqual(expect.objectContaining({ instructionMode: 'target', samplingMethod: 'pairwise', groupCount: '25' }))
  })

  it('names suite datasets after the suite and behavior', () => {
    expect(suiteDatasetName('travel planner', 'sycophancy')).toBe('travel_planner_sycophancy')
    expect(suiteDatasetName('', 'sycophancy')).toBe('sycophancy')
  })
})
