import type { RegisteredScenario } from '@/types'

import {
  techniqueSetDisplayName,
  techniqueSetMembers,
  techniqueSetName,
  techniqueSetOptionLabel,
} from './scenarioTechniqueSets'

function makeScenario(overrides: Partial<RegisteredScenario> = {}): RegisteredScenario {
  return {
    scenario_name: 'test.scenario',
    scenario_type: 'TestScenario',
    scenario_version: 1,
    description: 'Test scenario.',
    description_markdown: 'Test scenario.',
    default_technique: 'default',
    default_techniques: ['crescendo'],
    aggregate_techniques: ['default', 'quick_set'],
    aggregate_technique_expansions: {
      quick_set: ['crescendo', 'crescendo', 'pair'],
    },
    all_techniques: ['crescendo', 'pair'],
    default_datasets: [],
    dataset_size_limit: {
      default_scope: 'none',
      default_count: null,
      override_scope: 'unsupported',
    },
    baseline_policy: 'forbidden',
    include_baseline_by_default: false,
    supported_parameters: [],
    default_run_size: {
      version: 1,
      status: 'unavailable',
      total_attack_count: null,
      minimum_attack_count: null,
      maximum_attack_count: null,
      condition: null,
      components: [],
      datasets: [],
      adaptive_details: null,
      note: null,
      retries_included: false,
    },
    ...overrides,
  }
}

describe('scenarioTechniqueSets', () => {
  it('formats known, custom, and empty technique-set names', () => {
    expect(techniqueSetName('default')).toBe('Recommended')
    expect(techniqueSetName('custom_red_team')).toBe('Custom red team')
    expect(techniqueSetName('')).toBe('')
  })

  it('expands named sets, removes duplicates, and falls back to default members', () => {
    const scenario = makeScenario()

    expect(techniqueSetMembers(scenario, 'quick_set')).toEqual(['crescendo', 'pair'])
    expect(techniqueSetMembers(scenario, 'default')).toEqual(['crescendo'])
    expect(techniqueSetMembers(scenario, 'unknown_set')).toEqual([])
  })

  it('labels default and custom sets with singular and plural member counts', () => {
    const scenario = makeScenario()

    expect(techniqueSetDisplayName(scenario, 'default')).toBe('Recommended (default)')
    expect(techniqueSetDisplayName(scenario, 'quick_set')).toBe('Quick set')
    expect(techniqueSetOptionLabel(scenario, 'default')).toBe('Recommended (default) — 1 technique')
    expect(techniqueSetOptionLabel(scenario, 'quick_set')).toBe('Quick set (2 techniques)')
  })
})
