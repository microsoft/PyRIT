import type { DatasetInfo } from '@/types'

const SEARCH_PARAM = 'q'
const MODALITY_PARAM = 'modality'
const HARM_CATEGORY_PARAM = 'harm_category'
const UNLABELED_PARAM = 'unlabeled'
const SORT_PARAM = 'sort'

export const DATASET_SORTS = [
  'name',
  'name_desc',
  'logical_examples_desc',
  'logical_examples',
] as const

export type DatasetSort = (typeof DATASET_SORTS)[number]

export const DEFAULT_DATASET_SORT: DatasetSort = 'name'

export const DATASET_SORT_LABELS: Record<DatasetSort, string> = {
  name: 'Name (A-Z)',
  name_desc: 'Name (Z-A)',
  logical_examples_desc: 'Most examples',
  logical_examples: 'Fewest examples',
}

export interface DatasetCatalogQuery {
  readonly search: string
  readonly modalities: readonly string[]
  readonly harmCategories: readonly string[]
  readonly unlabeled: boolean
  readonly sort: DatasetSort
}

export const DEFAULT_DATASET_CATALOG_QUERY: DatasetCatalogQuery = {
  search: '',
  modalities: [],
  harmCategories: [],
  unlabeled: false,
  sort: DEFAULT_DATASET_SORT,
}

export function isDatasetSort(value: string): value is DatasetSort {
  return DATASET_SORTS.some((sort) => sort === value)
}

/** Reads catalog search, facets, and sort from the page query. */
export function catalogQueryFromSearchParams(params: URLSearchParams): DatasetCatalogQuery {
  const sortValue = params.get(SORT_PARAM)
  return {
    search: params.get(SEARCH_PARAM) ?? '',
    modalities: params.getAll(MODALITY_PARAM),
    harmCategories: params.getAll(HARM_CATEGORY_PARAM),
    unlabeled: params.get(UNLABELED_PARAM) === 'true',
    sort: sortValue != null && isDatasetSort(sortValue) ? sortValue : DEFAULT_DATASET_SORT,
  }
}

/** Encodes catalog search, facets, and sort, omitting filters that are already the default. */
export function catalogQueryToSearchParams(query: DatasetCatalogQuery): URLSearchParams {
  const params = new URLSearchParams()
  if (query.search) params.set(SEARCH_PARAM, query.search)
  for (const modality of query.modalities) params.append(MODALITY_PARAM, modality)
  for (const category of query.harmCategories) params.append(HARM_CATEGORY_PARAM, category)
  if (query.unlabeled) params.set(UNLABELED_PARAM, 'true')
  if (query.sort !== DEFAULT_DATASET_SORT) params.set(SORT_PARAM, query.sort)
  return params
}

export function catalogQueryIsDefault(query: DatasetCatalogQuery): boolean {
  return query.search === ''
    && query.modalities.length === 0
    && query.harmCategories.length === 0
    && !query.unlabeled
    && query.sort === DEFAULT_DATASET_SORT
}

function matchesSearch(name: string, search: string): boolean {
  const query = search.trim().toLowerCase()
  if (!query) return true
  return name.toLowerCase().includes(query)
}

function matchesModalities(dataset: DatasetInfo, modalities: readonly string[]): boolean {
  if (modalities.length === 0) return true
  return modalities.some((modality) => dataset.modalities.includes(modality))
}

function matchesHarm(dataset: DatasetInfo, query: DatasetCatalogQuery): boolean {
  const harmSelected = query.harmCategories.length > 0 || query.unlabeled
  if (!harmSelected) return true
  const categoryMatch = query.harmCategories.some((category) => dataset.harm_categories.includes(category))
  const unlabeledMatch = query.unlabeled && dataset.has_unlabeled_harm_categories
  return categoryMatch || unlabeledMatch
}

function countSort(sort: DatasetSort): 'asc' | 'desc' | null {
  switch (sort) {
    case 'logical_examples':
      return 'asc'
    case 'logical_examples_desc':
      return 'desc'
    default:
      return null
  }
}

function compareCounts(left: number | null, right: number | null, direction: 'asc' | 'desc'): number {
  if (left == null && right == null) return 0
  if (left == null) return 1
  if (right == null) return -1
  return direction === 'asc' ? left - right : right - left
}

function compareNames(left: DatasetInfo, right: DatasetInfo, direction: 'asc' | 'desc'): number {
  const nameOrder = left.name.localeCompare(right.name, 'en', { sensitivity: 'base' })
  const directed = direction === 'asc' ? nameOrder : -nameOrder
  if (directed !== 0) return directed
  return left.selection_key.localeCompare(right.selection_key, 'en')
}

/** Returns catalog rows matching the query. Unknown counts sort after known counts. */
export function filterDatasets(
  datasets: readonly DatasetInfo[],
  query: DatasetCatalogQuery,
): DatasetInfo[] {
  const matched: DatasetInfo[] = []
  for (const dataset of datasets) {
    if (!matchesSearch(dataset.name, query.search)) continue
    if (!matchesModalities(dataset, query.modalities)) continue
    if (!matchesHarm(dataset, query)) continue
    matched.push(dataset)
  }

  const countDirection = countSort(query.sort)
  const nameDirection = query.sort === 'name_desc' ? 'desc' : 'asc'
  return matched.sort((left, right) => {
    if (countDirection) {
      const countOrder = compareCounts(left.logical_examples, right.logical_examples, countDirection)
      if (countOrder !== 0) return countOrder
    }
    return compareNames(left, right, countDirection ? 'asc' : nameDirection)
  })
}
