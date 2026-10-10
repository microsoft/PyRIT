import type { DatasetInfo } from '@/types'

import {
  catalogQueryFromSearchParams,
  catalogQueryIsDefault,
  catalogQueryToSearchParams,
  DEFAULT_DATASET_CATALOG_QUERY,
  filterDatasets,
  type DatasetCatalogQuery,
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

  it('sorts names and keeps unknown counts after known counts', () => {
    const alpha = makeDataset({
      name: 'alpha',
      selection_key: 'dataset:named:alpha',
      logical_examples: 2,
      seed_pieces: 0,
      objectives: null,
    })
    const beta = makeDataset({
      name: 'beta',
      selection_key: 'dataset:named:beta',
      logical_examples: 10,
      seed_pieces: null,
      objectives: 1,
    })
    const same = makeDataset({
      name: 'alpha',
      selection_key: 'dataset:named:alpha-2',
      logical_examples: 2,
      seed_pieces: 0,
      objectives: null,
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
})
