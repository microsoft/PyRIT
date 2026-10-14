import type { ScenarioDatasetOptions, ScenarioDatasetSettings } from '@/types'

import { datasetPolicies } from './datasetPresentation'

const SETTINGS: ScenarioDatasetSettings = {
  sources: [
    { name: 'stored', max_size: 5, fetch: 'never', configured_provider: false },
    { name: 'fresh', max_size: 5, fetch: 'always', configured_provider: true },
  ],
  max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {},
}
const OPTIONS: ScenarioDatasetOptions = {
  mode: 'any', allowed_names: [], per_dataset_limit: true, total_limit: true, supports_overrides: true, filters: true,
}

describe('datasetPolicies', () => {
  it('uses effective source policies rather than the default global policy', () => {
    expect(datasetPolicies({}, SETTINGS, SETTINGS, OPTIONS)).toEqual([
      { name: 'stored', fetch: 'never' }, { name: 'fresh', fetch: 'always' },
    ])
  })

  it('keeps explicit source policies when applying a global request', () => {
    expect(datasetPolicies({
      fetch: 'always', sources: [{ name: 'stored', fetch: 'never' }, { name: 'new' }],
    }, SETTINGS, SETTINGS, OPTIONS)).toEqual([
      { name: 'stored', fetch: 'never' }, { name: 'new', fetch: 'always' },
    ])
  })

  it('does not overwrite protected policies with global draft settings', () => {
    expect(datasetPolicies({ fetch: 'always' }, SETTINGS, SETTINGS, {
      ...OPTIONS, source_settings: false,
    })).toEqual([{ name: 'stored', fetch: 'never' }, { name: 'fresh', fetch: 'always' }])
  })

  it('uses retained effective policies and treats default as inheritance', () => {
    expect(datasetPolicies({
      fetch: 'always', sources: [{ name: 'stored', fetch: 'default' }],
    }, SETTINGS, SETTINGS, OPTIONS)).toEqual([{ name: 'stored', fetch: 'never' }])
  })
})
