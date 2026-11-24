import { makeScenario } from '@/test-utils/scenarioFixtures'
import { makePresetResponse, makeRunSize, makeSuite } from '@/test-utils/scenarioSuiteFixtures'
import type { ScenarioPresetResponse } from '@/types'

import {
  formatAttackCount,
  formatScenarioCount,
  memberDatasetNames,
  memberDescription,
  memberScenarioName,
  memberTechniqueNames,
  otherSuitesUsing,
  suiteMembers,
  suiteNeedsAttention,
  suiteTotalAttacks,
  type SuiteMember,
} from './suiteMembers'

const JAILBREAK = makeScenario({
  scenario_name: 'airt.jailbreak',
  description: 'Tries jailbreak templates.',
  default_datasets: ['airt_harms'],
  aggregate_techniques: ['all', 'default'],
  default_technique: 'default',
  aggregate_technique_expansions: { all: ['prompt_sending', 'skeleton_key'], default: ['prompt_sending'] },
  default_techniques: ['prompt_sending'],
})
const SCENARIOS = new Map([[JAILBREAK.scenario_name, JAILBREAK]])

function membersFor(
  presets: ScenarioPresetResponse[],
  names: string[] = presets.map((item) => item.preset.name),
): SuiteMember[] {
  return suiteMembers(
    makeSuite('release_gate', names),
    new Map(presets.map((item) => [item.preset.name, item])),
    SCENARIOS,
  )
}

function pair(members: SuiteMember[]): [SuiteMember, SuiteMember] {
  const [first, second] = members
  if (!first || !second) throw new Error('Expected two members.')
  return [first, second]
}

describe('suiteMembers', () => {
  it('pairs each entry with its stored scenario and the registered scenario it runs, in order', () => {
    const smoke = makePresetResponse({ name: 'smoke', scenario_name: 'airt.jailbreak' })
    const [first, missing] = pair(membersFor([smoke], ['smoke', 'gone']))

    expect(first.preset).toBe(smoke)
    expect(first.scenario).toBe(JAILBREAK)
    expect(missing.presetName).toBe('gone')
    expect(missing.preset).toBeUndefined()
    expect(missing.scenario).toBeUndefined()
  })

  it('leads with the scenario name and falls back to the stored name when the scenario is unknown', () => {
    const [known, missing] = pair(membersFor(
      [makePresetResponse({ name: 'smoke', scenario_name: 'airt.jailbreak' })],
      ['smoke', 'gone'],
    ))

    expect(memberScenarioName(known)).toBe('airt.jailbreak')
    expect(memberScenarioName(missing)).toBe('gone')
  })

  it("describes a member with its own description, else its scenario's", () => {
    const [own, inherited] = pair(membersFor([
      makePresetResponse({ name: 'own', scenario_name: 'airt.jailbreak', description: 'Two templates only.' }),
      makePresetResponse({ name: 'inherited', scenario_name: 'airt.jailbreak' }),
    ]))

    expect(memberDescription(own)).toBe('Two templates only.')
    expect(memberDescription(inherited)).toBe('Tries jailbreak templates.')
  })

  it('expands pinned technique sets and falls back to the scenario defaults', () => {
    const [pinned, defaults] = pair(membersFor([
      makePresetResponse({ name: 'pinned', scenario_name: 'airt.jailbreak', techniques: ['all', 'crescendo'] }),
      makePresetResponse({ name: 'defaults', scenario_name: 'airt.jailbreak' }),
    ]))

    expect(memberTechniqueNames(pinned)).toEqual(['prompt_sending', 'skeleton_key', 'crescendo'])
    expect(memberTechniqueNames(defaults)).toEqual(['prompt_sending'])
  })

  it('names pinned datasets, else the scenario defaults', () => {
    const [pinned, defaults] = pair(membersFor([
      makePresetResponse({
        name: 'pinned',
        scenario_name: 'airt.jailbreak',
        datasets: { sources: [{ name: 'harmbench' }] },
      }),
      makePresetResponse({ name: 'defaults', scenario_name: 'airt.jailbreak' }),
    ]))

    expect(memberDatasetNames(pinned)).toEqual(['harmbench'])
    expect(memberDatasetNames(defaults)).toEqual(['airt_harms'])
  })
})

describe('suiteTotalAttacks', () => {
  it('adds exact estimates', () => {
    const members = membersFor([
      makePresetResponse({ name: 'a', scenario_name: 'airt.jailbreak' }, { run_size: makeRunSize(90) }),
      makePresetResponse({ name: 'b', scenario_name: 'airt.jailbreak' }, { run_size: makeRunSize(1_200) }),
    ])

    expect(suiteTotalAttacks(members)).toBe(1_290)
  })

  it.each([
    ['missing', undefined],
    ['approximate', makeRunSize(10, 'approximate')],
    ['conditional', { ...makeRunSize(10, 'conditional'), estimated_attack_count: null }],
  ])('shows no total when one estimate is %s', (_label, runSize) => {
    const members = membersFor([
      makePresetResponse({ name: 'a', scenario_name: 'airt.jailbreak' }, { run_size: makeRunSize(90) }),
      makePresetResponse({ name: 'b', scenario_name: 'airt.jailbreak' }, { run_size: runSize }),
    ])

    expect(suiteTotalAttacks(members)).toBeNull()
  })

  it('shows no total for a preset without scenarios', () => {
    expect(suiteTotalAttacks([])).toBeNull()
  })
})

describe('suite helpers', () => {
  it('flags a preset whose entries report issues', () => {
    expect(suiteNeedsAttention(makeSuite('ok', ['a']))).toBe(false)
    expect(suiteNeedsAttention(makeSuite('broken', ['a'], {
      entryIssues: [[{ field: 'preset_name', message: 'Missing.' }]],
    }))).toBe(true)
  })

  it('lists the other presets that share a stored scenario', () => {
    const suites = [
      makeSuite('release_gate', ['smoke']),
      makeSuite('quarterly', ['smoke', 'other']),
      makeSuite('nightly', ['other']),
    ]

    expect(otherSuitesUsing('smoke', 'release_gate', suites)).toEqual(['quarterly'])
    expect(otherSuitesUsing('other', 'nightly', suites)).toEqual(['quarterly'])
  })

  it('formats counts with singular and plural forms', () => {
    expect(formatScenarioCount(1)).toBe('1 scenario')
    expect(formatScenarioCount(3)).toBe('3 scenarios')
    expect(formatAttackCount(1)).toBe('1 attack')
    expect(formatAttackCount(1_250)).toBe('1,250 attacks')
  })
})
