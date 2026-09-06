import {
  DEFAULT_SCENARIO_HISTORY_FILTERS,
  SCENARIO_RUN_STATES,
  scenarioHistoryFiltersFromSearchParams,
  scenarioHistoryFiltersToSearchParams,
} from './scenarioHistoryFilters'

describe('scenario history URL filters', () => {
  it('round-trips repeated filters and label search text', () => {
    const filters = {
      scenarioNames: ['red.team', 'benchmark'],
      statuses: ['IN_PROGRESS', 'FAILED'] as const,
      operator: ['alice', 'bob'],
      operation: ['nightly'],
      otherLabels: ['team:security', 'team:safety'],
      labelSearchText: 'team',
    }

    const params = scenarioHistoryFiltersToSearchParams({
      ...filters,
      statuses: [...filters.statuses],
    })

    expect(params.getAll('scenario')).toEqual(['red.team', 'benchmark'])
    expect(params.getAll('status')).toEqual(['IN_PROGRESS', 'FAILED'])
    expect(scenarioHistoryFiltersFromSearchParams(params)).toEqual({
      ...filters,
      statuses: [...filters.statuses],
    })
  })

  it('ignores synthetic and invalid run states without dropping valid filters', () => {
    const params = new URLSearchParams('status=COMPLETED&status=QUEUED&status=UNKNOWN&operator=alice')

    expect(scenarioHistoryFiltersFromSearchParams(params)).toEqual({
      ...DEFAULT_SCENARIO_HISTORY_FILTERS,
      statuses: ['COMPLETED'],
      operator: ['alice'],
    })
  })

  it('round-trips every persisted run state', () => {
    const filters = {
      ...DEFAULT_SCENARIO_HISTORY_FILTERS,
      statuses: [...SCENARIO_RUN_STATES],
    }

    const params = scenarioHistoryFiltersToSearchParams(filters)

    expect(params.getAll('status')).toEqual(SCENARIO_RUN_STATES)
    expect(scenarioHistoryFiltersFromSearchParams(params)).toEqual(filters)
  })

  it('omits empty filters from the URL', () => {
    expect(scenarioHistoryFiltersToSearchParams(DEFAULT_SCENARIO_HISTORY_FILTERS).toString()).toBe('')
  })
})
