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
