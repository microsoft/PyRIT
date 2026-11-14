import type { DatasetInfo } from '@/types'

import {
  catalogQueryFromSearchParams,
  catalogQueryIsDefault,
  catalogQueryToSearchParams,
  DEFAULT_DATASET_CATALOG_QUERY,
  datasetModalities,
  filterDatasets,
  normalizeDatasetModality,
  type DatasetCatalogQuery,
  type DatasetSort,
} from './datasetQuery'

function makeDataset(overrides: Partial<DatasetInfo> & Pick<DatasetInfo, 'name' | 'selection_key'>): DatasetInfo {
  return {
    loaded: true,
    provider_available: false,
    logical_examples: 1,
    seed_pieces: 1,
    objectives: 1,
    modalities: [],
    harm_categories: [],
    has_unlabeled_harm_categories: false,
    ...overrides,
  }
}

describe('datasetQuery', () => {
  it.each([
    ['image', 'image_path'],
    ['audio', 'audio_path'],
    ['video', 'video_path'],
  ])('matches %s providers before and after loading, including legacy filters', (modality: string, storedType: string) => {
    const available = makeDataset({
      name: 'provider', selection_key: 'dataset:named:provider', loaded: false, provider_available: true,
      provider: {
        description: null, source_url: null, license_url: null, access_url: null,
        required_environment_variables: [], modalities: [modality, 'text'], harm_categories: [],
        size: [], load_time: [], unavailable_reason: null,
      },
    })
    const stored = { ...available, loaded: true, modalities: [storedType, 'text'] }
    expect(datasetModalities(available)).toEqual([modality, 'text'])
    expect(datasetModalities(stored)).toEqual([modality, 'text'])
    for (const view of ['available', 'all'] as const) {
      for (const filter of [modality, storedType]) {
        const query = { ...DEFAULT_DATASET_CATALOG_QUERY, view, modalities: [filter] }
        expect(filterDatasets([available], query)).toEqual([available])
        expect(filterDatasets([stored], query)).toEqual([stored])
      }
    }
    expect(filterDatasets([available], {
      ...DEFAULT_DATASET_CATALOG_QUERY, view: 'memory', modalities: [modality],
    })).toEqual([])
  })

  it.each(['text', 'binary_path', 'image/png', 'custom'])('preserves the distinct modality %s', (modality: string) => {
    expect(normalizeDatasetModality(modality)).toBe(modality)
  })

  it('deduplicates equivalent stored and provider modality names', () => {
    expect(datasetModalities(makeDataset({
      name: 'mixed', selection_key: 'dataset:named:mixed', modalities: ['image', 'image_path', 'text'],
    }))).toEqual(['image', 'text'])
  })

  it('keeps provider availability independent of stored data and preserves the view in the URL', () => {
    const stored = makeDataset({ name: 'stored', selection_key: 'dataset:named:stored' })
    const available = makeDataset({
      name: 'provider', selection_key: 'dataset:named:provider', loaded: false, provider_available: true,
      logical_examples: null,
    })
    const both = makeDataset({ name: 'both', selection_key: 'dataset:named:both', provider_available: true })
    const datasets = [stored, available, both]
    expect(filterDatasets(datasets, DEFAULT_DATASET_CATALOG_QUERY).map((item) => item.name)).toEqual(['both', 'stored'])
    const query = { ...DEFAULT_DATASET_CATALOG_QUERY, view: 'available' as const }
    expect(filterDatasets(datasets, query).map((item) => item.name)).toEqual(['both', 'provider'])
    expect(catalogQueryFromSearchParams(catalogQueryToSearchParams(query))).toEqual(query)
    expect(filterDatasets(datasets, { ...query, view: 'all' })).toHaveLength(3)
  })
  it('omits default catalog filters and restores special characters', () => {
    expect(catalogQueryToSearchParams(DEFAULT_DATASET_CATALOG_QUERY).toString()).toBe('')
    expect(catalogQueryIsDefault(DEFAULT_DATASET_CATALOG_QUERY)).toBe(true)

    const query: DatasetCatalogQuery = {
      search: 'a/b?c#d&e% fü',
      modalities: ['image/png', 'text'],
      harmCategories: ['a&b', 'データ'],
      unlabeled: true,
      sort: 'logical_examples_desc',
    }
    const params = catalogQueryToSearchParams(query)
    const restored = catalogQueryFromSearchParams(params)

    expect(restored).toEqual(query)
    expect(catalogQueryIsDefault(restored)).toBe(false)
    expect(params.getAll('modality')).toEqual(['image/png', 'text'])
    expect(params.getAll('harm_category')).toEqual(['a&b', 'データ'])
  })

  it('ignores an unknown sort value', () => {
    expect(catalogQueryFromSearchParams(new URLSearchParams('sort=nope')).sort).toBe('name')
  })

  it('deduplicates parsed facets in first-occurrence order', () => {
    const params = new URLSearchParams([
      ['modality', 'text'],
      ['modality', 'image/png'],
      ['modality', 'text'],
      ['modality', 'audio_path'],
      ['modality', 'image/png'],
      ['harm_category', 'violence'],
      ['harm_category', 'a&b'],
      ['harm_category', 'violence'],
      ['harm_category', 'データ'],
      ['harm_category', 'a&b'],
    ])

    expect(catalogQueryFromSearchParams(params)).toEqual({
      ...DEFAULT_DATASET_CATALOG_QUERY,
      modalities: ['text', 'image/png', 'audio_path'],
      harmCategories: ['violence', 'a&b', 'データ'],
    })
  })

  it('deduplicates serialized facets in first-occurrence order', () => {
    const params = catalogQueryToSearchParams({
      ...DEFAULT_DATASET_CATALOG_QUERY,
      modalities: ['text', 'image/png', 'text', 'audio_path', 'image/png'],
      harmCategories: ['violence', 'a&b', 'violence', 'データ', 'a&b'],
    })

    expect(params.getAll('modality')).toEqual(['text', 'image/png', 'audio_path'])
    expect(params.getAll('harm_category')).toEqual(['violence', 'a&b', 'データ'])
  })

  it('filters by name, modality, and harm without treating unlabeled rows as safe', () => {
    const hate = makeDataset({
      name: 'Hate bench',
      selection_key: 'dataset:named:hate',
      modalities: ['text'],
      harm_categories: ['hate'],
    })
    const unlabeled = makeDataset({
      name: 'Plain',
      selection_key: 'dataset:named:plain',
      modalities: ['image_path'],
      has_unlabeled_harm_categories: true,
    })
    const datasets = [hate, unlabeled]

    expect(filterDatasets(datasets, { ...DEFAULT_DATASET_CATALOG_QUERY, search: '  HATE ' }).map((item) => item.name))
      .toEqual(['Hate bench'])
    expect(filterDatasets(datasets, { ...DEFAULT_DATASET_CATALOG_QUERY, modalities: ['image_path'] }).map((item) => item.name))
      .toEqual(['Plain'])
    expect(filterDatasets(datasets, { ...DEFAULT_DATASET_CATALOG_QUERY, harmCategories: ['hate'] }).map((item) => item.name))
      .toEqual(['Hate bench'])
    expect(filterDatasets(datasets, { ...DEFAULT_DATASET_CATALOG_QUERY, unlabeled: true }).map((item) => item.name))
      .toEqual(['Plain'])
    expect(filterDatasets(datasets, {
      ...DEFAULT_DATASET_CATALOG_QUERY,
      harmCategories: ['hate'],
      unlabeled: true,
    }).map((item) => item.name)).toEqual(['Hate bench', 'Plain'])
    expect(filterDatasets(datasets, {
      ...DEFAULT_DATASET_CATALOG_QUERY,
      modalities: ['text'],
      unlabeled: true,
    })).toEqual([])
  })

  it('sorts names and known counts with deterministic ties', () => {
    const alpha = makeDataset({
      name: 'alpha',
      selection_key: 'dataset:named:alpha',
      logical_examples: 2,
    })
    const beta = makeDataset({
      name: 'beta',
      selection_key: 'dataset:named:beta',
      logical_examples: 10,
    })
    const same = makeDataset({
      name: 'alpha',
      selection_key: 'dataset:named:alpha-2',
      logical_examples: 2,
    })
    const rows = [beta, same, alpha]

    expect(filterDatasets(rows, DEFAULT_DATASET_CATALOG_QUERY).map((item) => item.selection_key)).toEqual([
      'dataset:named:alpha',
      'dataset:named:alpha-2',
      'dataset:named:beta',
    ])
    expect(filterDatasets(rows, { ...DEFAULT_DATASET_CATALOG_QUERY, sort: 'name_desc' }).map((item) => item.selection_key)).toEqual([
      'dataset:named:beta',
      'dataset:named:alpha',
      'dataset:named:alpha-2',
    ])
    expect(filterDatasets(rows, { ...DEFAULT_DATASET_CATALOG_QUERY, sort: 'logical_examples' }).map((item) => item.selection_key)).toEqual([
      'dataset:named:alpha',
      'dataset:named:alpha-2',
      'dataset:named:beta',
    ])
    expect(filterDatasets(rows, { ...DEFAULT_DATASET_CATALOG_QUERY, sort: 'logical_examples_desc' }).map((item) => item.selection_key)).toEqual([
      'dataset:named:beta',
      'dataset:named:alpha',
      'dataset:named:alpha-2',
    ])
  })

  it.each<[DatasetSort, string[]]>([
    ['logical_examples', ['zero', 'few', 'many', 'a-unknown', 'b-unknown']],
    ['logical_examples_desc', ['many', 'few', 'zero', 'a-unknown', 'b-unknown']],
  ])('keeps unknown logical-example counts last for %s', (sort: DatasetSort, expected: string[]) => {
    const rows = [
      makeDataset({ name: 'b-unknown', selection_key: 'dataset:named:b-unknown', logical_examples: null }),
      makeDataset({ name: 'zero', selection_key: 'dataset:named:zero', logical_examples: 0 }),
      makeDataset({ name: 'many', selection_key: 'dataset:named:many', logical_examples: 10 }),
      makeDataset({ name: 'a-unknown', selection_key: 'dataset:named:a-unknown', logical_examples: null }),
      makeDataset({ name: 'few', selection_key: 'dataset:named:few', logical_examples: 2 }),
    ]

    expect(filterDatasets(rows, { ...DEFAULT_DATASET_CATALOG_QUERY, sort }).map((item: DatasetInfo) => item.name))
      .toEqual(expected)
  })
})
