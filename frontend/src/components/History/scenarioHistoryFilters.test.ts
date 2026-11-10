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
      operationId: ['6f1b2c3d-0000-4000-8000-000000000001'],
      otherLabels: ['team:security', 'team:safety'],
      labelSearchText: 'team',
      referenceId: '11111111-1111-4111-8111-111111111111',
    }

    const params = scenarioHistoryFiltersToSearchParams({
      ...filters,
      statuses: [...filters.statuses],
    })

    expect(params.getAll('scenario')).toEqual(['red.team', 'benchmark'])
    expect(params.getAll('status')).toEqual(['IN_PROGRESS', 'FAILED'])
    expect(params.getAll('operation_id')).toEqual(['6f1b2c3d-0000-4000-8000-000000000001'])
    expect(params.get('id')).toBe(filters.referenceId)
    expect(scenarioHistoryFiltersFromSearchParams(params)).toEqual({
      ...filters,
      statuses: [...filters.statuses],
    })
  })

  it('ignores invalid run states without dropping valid filters', () => {
    const params = new URLSearchParams('status=COMPLETED&status=QUEUED&status=UNKNOWN&operator=alice')

    expect(scenarioHistoryFiltersFromSearchParams(params)).toEqual({
      ...DEFAULT_SCENARIO_HISTORY_FILTERS,
      statuses: ['COMPLETED', 'QUEUED'],
      operator: ['alice'],
    })
  })

  it('round-trips every persisted run state', () => {
    const filters = {
      ...DEFAULT_SCENARIO_HISTORY_FILTERS,
      statuses: [...SCENARIO_RUN_STATES],
    }

    const params = scenarioHistoryFiltersToSearchParams(filters)

    expect(params.getAll('status')).toEqual([
      'CREATED', 'QUEUED', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'CANCELLED',
    ])
    expect(scenarioHistoryFiltersFromSearchParams(params)).toEqual(filters)
  })

  it('preserves a QUEUED-only status filter from the URL', () => {
    const params = new URLSearchParams('status=QUEUED')

    expect(scenarioHistoryFiltersFromSearchParams(params).statuses).toEqual(['QUEUED'])
  })

  it('omits empty filters from the URL', () => {
    expect(scenarioHistoryFiltersToSearchParams(DEFAULT_SCENARIO_HISTORY_FILTERS).toString()).toBe('')
  })
})
