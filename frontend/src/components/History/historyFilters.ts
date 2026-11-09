export interface HistoryFilters {
  outcome: string
  includeScenarioAttacks: boolean
  operator: string[]
  operationId: string[]
  otherLabels: string[]
  labelSearchText: string
}

export const DEFAULT_HISTORY_FILTERS: HistoryFilters = {
  outcome: '',
  includeScenarioAttacks: true,
  operator: [],
  operationId: [],
  otherLabels: [],
  labelSearchText: '',
}

/** Builds the history filter state from a URL query string. */
export function filtersFromSearchParams(params: URLSearchParams): HistoryFilters {
  return {
    outcome: params.get('outcome') ?? '',
    includeScenarioAttacks: params.get('includeScannerAttacks') !== 'false',
    operator: params.getAll('operator'),
    operationId: params.getAll('operation_id'),
    otherLabels: params.getAll('label'),
    labelSearchText: params.get('labelSearch') ?? '',
  }
}

/** Encodes history filter state into a URL query string, omitting inactive filters. */
export function filtersToSearchParams(filters: HistoryFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.outcome) params.set('outcome', filters.outcome)
  if (!filters.includeScenarioAttacks) params.set('includeScannerAttacks', 'false')
  for (const operator of filters.operator) params.append('operator', operator)
  for (const operationId of filters.operationId) params.append('operation_id', operationId)
  for (const label of filters.otherLabels) params.append('label', label)
  if (filters.labelSearchText) params.set('labelSearch', filters.labelSearchText)
  return params
}
