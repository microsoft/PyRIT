import type { ScenarioDatasetRequest, ScenarioDatasetSettings } from '@/types'

import { automaticDatasetRequest, datasetNameWithOrigin, datasetTotalLimit, hasSavedDatasetPolicy } from './datasetPresentation'

const SETTINGS: ScenarioDatasetSettings = {
  sources: [
    { name: 'stored', max_size: 5, fetch: 'never', configured_provider: false },
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

describe('automaticDatasetRequest', () => {
  it('keeps selection, filters and limits while deliberately replacing saved policies', () => {
    const request: ScenarioDatasetRequest = {
      fetch: 'always', max_total: 'all', max_per_dataset: 3, filters: { data_types: ['text'] },
      sources: [{ name: 'stored', max_size: 2, fetch: 'never' }, { name: 'fresh' }],
    }
    expect(automaticDatasetRequest(request, SETTINGS)).toEqual({
      ...request, fetch: 'if_missing',
      sources: [{ name: 'stored', max_size: 2, fetch: 'if_missing' }, { name: 'fresh', fetch: 'if_missing' }],
    })
    expect(hasSavedDatasetPolicy(request)).toBe(true)
    expect(request.fetch).toBe('always')
  })

  it('does not invent unrelated overrides when no saved policy was supplied', () => {
    expect(automaticDatasetRequest({ max_total: 'all', sources: [{ name: 'stored' }] })).toEqual({
      max_total: 'all', sources: [{ name: 'stored' }],
    })
    expect(hasSavedDatasetPolicy({})).toBe(false)
  })
})
