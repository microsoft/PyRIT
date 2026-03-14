export interface HistoryFilters {
  attackClass: string
  outcome: string
  converter: string
  operator: string
  operation: string
  otherLabels: string[]
  labelSearchText: string
}

export const DEFAULT_HISTORY_FILTERS: HistoryFilters = {
  attackClass: '',
  outcome: '',
  converter: '',
  operator: '',
  operation: '',
  otherLabels: [],
  labelSearchText: '',
}
