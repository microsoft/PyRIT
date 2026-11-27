import type { ScenarioDatasetRequest, ScenarioDatasetSettings } from '@/types'

import {
  datasetCanRefresh, datasetLoadingChoice, datasetNameWithOrigin, datasetTotalLimit, withScanLevelLoading,
} from './datasetPresentation'

const SETTINGS: ScenarioDatasetSettings = {
  sources: [
    { name: 'stored', max_size: 5, fetch: 'if_missing', configured_provider: false },
    { name: 'fresh', max_size: 5, fetch: 'always', configured_provider: true },
  ],
  max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {},
}

describe('datasetTotalLimit', () => {
  it.each<[ScenarioDatasetRequest, number | 'all']>([
    [{}, 5],
    [{ max_total: null }, 5],
    [{ max_total: 'default' }, 5],
    [{ max_total: 3 }, 3],
    [{ max_total: 'all' }, 'all'],
  ])('resolves %j against the current defaults', (request: ScenarioDatasetRequest, expected: number | 'all') => {
    expect(datasetTotalLimit(request, { ...SETTINGS, max_total: 5 })).toBe(expected)
  })
})

describe('datasetNameWithOrigin', () => {
  it.each([
    ['local', 'Local files'], ['remote', 'Remote provider'], ['generated', 'Generated'], ['user', 'User'],
  ])('displays recorded %s origin with the catalog label', (origin, label) => {
    expect(datasetNameWithOrigin('sample', {
      name: 'sample', selection_key: 'dataset:named:sample', loaded: true, provider_available: false,
      origins: [origin], logical_examples: 1, seed_pieces: 1, objectives: 1, modalities: [], harm_categories: [],
      has_unlabeled_harm_categories: false,
    })).toBe(`sample (${label})`)
  })
  it('does not guess the origin of an unloaded dataset', () => {
    expect(datasetNameWithOrigin('sample', undefined)).toBe('sample (Unknown)')
  })
})

describe('datasetLoadingChoice', () => {
  it.each<[ScenarioDatasetRequest, 'if_missing' | 'always']>([
    [{}, 'if_missing'],
    [{ fetch: null }, 'if_missing'],
    [{ fetch: 'default' }, 'if_missing'],
    [{ fetch: 'never' }, 'if_missing'],
    [{ fetch: 'if_missing' }, 'if_missing'],
    [{ sources: [{ name: 'fresh', fetch: 'always' }] }, 'if_missing'],
    [{ fetch: 'always' }, 'always'],
  ])('shows %j as %s', (request: ScenarioDatasetRequest, expected: 'if_missing' | 'always') => {
    expect(datasetLoadingChoice(request)).toBe(expected)
  })
})

describe('withScanLevelLoading', () => {
  it('drops per-source policies and reads never as Cached, keeping every other setting', () => {
    expect(withScanLevelLoading({
      fetch: 'never', max_total: 3, filters: { harm_categories: ['hate'] },
      sources: [{ name: 'stored', fetch: 'never', max_size: 2 }, { name: 'fresh', fetch: 'always' }],
    })).toEqual({
      fetch: 'if_missing', max_total: 3, filters: { harm_categories: ['hate'] },
      sources: [{ name: 'stored', max_size: 2 }, { name: 'fresh' }],
    })
  })

  it.each<ScenarioDatasetRequest>([
    {}, { fetch: 'always' }, { fetch: 'if_missing' }, { fetch: null, sources: null }, { fetch: 'default' },
  ])('keeps %j unchanged', (request: ScenarioDatasetRequest) => {
    expect(withScanLevelLoading(request)).toEqual(request)
  })
})

describe('datasetCanRefresh', () => {
  const dataset = {
    name: 'sample', selection_key: 'dataset:named:sample', loaded: true, provider_available: false,
    logical_examples: 1, seed_pieces: 1, objectives: 1, modalities: [], harm_categories: [],
    has_unlabeled_harm_categories: false,
  }
  it('needs a registered or configured provider', () => {
    expect(datasetCanRefresh(dataset, SETTINGS.sources[0])).toBe(false)
    expect(datasetCanRefresh(undefined, undefined)).toBe(false)
    expect(datasetCanRefresh({ ...dataset, provider_available: true }, SETTINGS.sources[0])).toBe(true)
    expect(datasetCanRefresh(undefined, SETTINGS.sources[1])).toBe(true)
  })
})
