import type { ScenarioDatasetOptions, ScenarioDatasetSettings } from '@/types'

import { datasetNameWithOrigin, datasetPolicies } from './datasetPresentation'

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

describe('datasetNameWithOrigin', () => {
  it.each(['local', 'remote', 'generated', 'user'])('displays recorded %s origin', (origin) => {
    expect(datasetNameWithOrigin('sample', {
      name: 'sample', selection_key: 'dataset:named:sample', loaded: true, provider_available: false,
      origins: [origin], logical_examples: 1, seed_pieces: 1, objectives: 1, modalities: [], harm_categories: [],
      has_unlabeled_harm_categories: false,
    })).toBe(`sample (${origin})`)
  })
  it('does not guess the origin of an unloaded dataset', () => {
    expect(datasetNameWithOrigin('sample', undefined)).toBe('sample (unknown)')
  })
})

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

  it('shows a permitted global draft instead of a stale protected-source projection', () => {
    const defaults: ScenarioDatasetSettings = {
      ...SETTINGS,
      sources: [{ name: 'stored', max_size: 5, fetch: 'if_missing', configured_provider: false }],
    }
    expect(datasetPolicies({ fetch: 'always' }, defaults, defaults, {
      ...OPTIONS, source_settings: false,
    })).toEqual([{ name: 'stored', fetch: 'always' }])
    expect(datasetPolicies({ fetch: 'if_missing' }, defaults, defaults, {
      ...OPTIONS, source_settings: false,
    })).toEqual([{ name: 'stored', fetch: 'if_missing' }])
  })

  it('uses retained effective policies and treats default as inheritance', () => {
    expect(datasetPolicies({
      fetch: 'always', sources: [{ name: 'stored', fetch: 'default' }],
    }, SETTINGS, SETTINGS, OPTIONS)).toEqual([{ name: 'stored', fetch: 'never' }])
  })
})
