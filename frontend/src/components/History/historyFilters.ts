export type ConverterMatchMode = 'any' | 'all'

export interface HistoryFilters {
  attackTypes: string[]
  outcome: string
  converter: string[]
  converterMatchMode: ConverterMatchMode
  hasConverters: boolean | undefined
  operator: string[]
  operation: string[]
  otherLabels: string[]
  labelSearchText: string
}

export const DEFAULT_HISTORY_FILTERS: HistoryFilters = {
  attackTypes: [],
  outcome: '',
  converter: [],
  converterMatchMode: 'any',
  hasConverters: undefined,
  operator: [],
  operation: [],
  otherLabels: [],
  labelSearchText: '',
}

/** Builds the history filter state from a URL query string. */
export function filtersFromSearchParams(params: URLSearchParams): HistoryFilters {
  const hasConverters = params.get('hasConverters')
  return {
    attackTypes: params.getAll('attackType'),
    outcome: params.get('outcome') ?? '',
    converter: params.getAll('converter'),
    converterMatchMode: params.get('converterMatch') === 'all' ? 'all' : 'any',
    hasConverters: hasConverters === null ? undefined : hasConverters === 'true',
    operator: params.getAll('operator'),
    operation: params.getAll('operation'),
    otherLabels: params.getAll('label'),
    labelSearchText: params.get('labelSearch') ?? '',
  }
}

/** Encodes history filter state into a URL query string, omitting inactive filters. */
export function filtersToSearchParams(filters: HistoryFilters): URLSearchParams {
  const params = new URLSearchParams()
  for (const attackType of filters.attackTypes) params.append('attackType', attackType)
  if (filters.outcome) params.set('outcome', filters.outcome)
  for (const converter of filters.converter) params.append('converter', converter)
  if (filters.converterMatchMode === 'all') params.set('converterMatch', 'all')
  if (filters.hasConverters !== undefined) params.set('hasConverters', String(filters.hasConverters))
  for (const operator of filters.operator) params.append('operator', operator)
  for (const operation of filters.operation) params.append('operation', operation)
  for (const label of filters.otherLabels) params.append('label', label)
  if (filters.labelSearchText) params.set('labelSearch', filters.labelSearchText)
  return params
}
