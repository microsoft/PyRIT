import type { DatasetInfo } from '@/types'

import {
  catalogQueryFromSearchParams,
  catalogQueryIsDefault,
  catalogQueryToSearchParams,
  DEFAULT_DATASET_CATALOG_QUERY,
  filterDatasets,
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
  it('omits default catalog filters and restores special characters', () => {
    expect(catalogQueryToSearchParams(DEFAULT_DATASET_CATALOG_QUERY).toString()).toBe('')
    expect(catalogQueryIsDefault(DEFAULT_DATASET_CATALOG_QUERY)).toBe(true)

    const query: DatasetCatalogQuery = {
      search: 'a/b?c#d&e% fü',
      modalities: ['image/png', 'text'],
      harmCategories: ['a&b', 'データ'],
      unlabeled: true,
      origins: ['generated', 'remote'],
      sort: 'logical_examples_desc',
    }
    const params = catalogQueryToSearchParams(query)
    const restored = catalogQueryFromSearchParams(params)

    expect(restored).toEqual(query)
    expect(catalogQueryIsDefault(restored)).toBe(false)
    expect(params.getAll('modality')).toEqual(['image/png', 'text'])
    expect(params.getAll('harm_category')).toEqual(['a&b', 'データ'])
    expect(params.getAll('origin')).toEqual(['generated', 'remote'])
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
      ['origin', 'generated'],
      ['origin', 'generated'],
    ])

    expect(catalogQueryFromSearchParams(params)).toEqual({
      ...DEFAULT_DATASET_CATALOG_QUERY,
      modalities: ['text', 'image/png', 'audio_path'],
      harmCategories: ['violence', 'a&b', 'データ'],
      origins: ['generated'],
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

  it('filters by any matching seed origin and treats a missing origin list as no match', () => {
    const generated = makeDataset({ name: 'Generated', selection_key: 'dataset:named:generated', origins: ['generated'] })
    const mixed = makeDataset({ name: 'Mixed', selection_key: 'dataset:named:mixed', origins: ['local', 'remote'] })
    const legacy = makeDataset({ name: 'Legacy', selection_key: 'dataset:named:legacy' })
    const datasets = [generated, mixed, legacy]

    expect(filterDatasets(datasets, DEFAULT_DATASET_CATALOG_QUERY).map((item) => item.name))
      .toEqual(['Generated', 'Legacy', 'Mixed'])
    expect(filterDatasets(datasets, { ...DEFAULT_DATASET_CATALOG_QUERY, origins: ['generated'] }).map((item) => item.name))
      .toEqual(['Generated'])
    expect(filterDatasets(datasets, { ...DEFAULT_DATASET_CATALOG_QUERY, origins: ['remote', 'generated'] }).map((item) => item.name))
      .toEqual(['Generated', 'Mixed'])
    expect(catalogQueryIsDefault({ ...DEFAULT_DATASET_CATALOG_QUERY, origins: ['generated'] })).toBe(false)
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
