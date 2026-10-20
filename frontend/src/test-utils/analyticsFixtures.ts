import type {
  AttackAnalyticsFacets, AttackAnalyticsFilter, AttackAnalyticsReport,
  AttackAnalyticsResultRow, AttackAnalyticsResults, AttackAnalyticsStatistics, OperationListResponse,
} from '@/types'

export const ANALYTICS_NIGHTLY_ID = '1d385514-6557-41e5-a385-907e35d23a69'
export const ANALYTICS_TEST_TIME = '2026-09-14T12:00:00Z'
export const ANALYTICS_PAGE_TIME = '2026-09-14T12:05:00Z'
export const ANALYTICS_PREDICATE_LIMIT_REASON =
  'Remove a dimension filter before drilling down further (maximum 16 predicates).'
export const ANALYTICS_VALUE_LIMIT_REASON =
  'Select fewer dimension values before drilling down further (maximum 500 values).'

export const ANALYTICS_TEST_STATISTICS: AttackAnalyticsStatistics = {
  success_rate: 0.6666666667,
  success_rate_decided: 0.6666666667,
  success_rate_all: 0.4,
  total_decided: 6,
  successes: 4,
  failures: 2,
  undetermined: 3,
  errors: 1,
  total_results: 10,
  decided_share: 0.6,
  outcome_shares: { success: 0.4, failure: 0.2, error: 0.1, undetermined: 0.3 },
}

export const ANALYTICS_EMPTY_STATISTICS: AttackAnalyticsStatistics = {
  success_rate: null,
  success_rate_decided: null,
  success_rate_all: null,
  total_decided: 0,
  successes: 0,
  failures: 0,
  undetermined: 0,
  errors: 0,
  total_results: 0,
  decided_share: null,
  outcome_shares: { success: 0, failure: 0, error: 0, undetermined: 0 },
}

export const ANALYTICS_OPERATION_FILTER: AttackAnalyticsFilter = {
  dimension: { name: 'operation' },
  values: [{ kind: 'value', value: ANALYTICS_NIGHTLY_ID }],
  match_mode: 'any',
}

export const ANALYTICS_OPERATIONS: OperationListResponse = {
  items: [{ id: ANALYTICS_NIGHTLY_ID, name: 'Nightly', created_at: ANALYTICS_TEST_TIME, finding_counts: {} }],
}

export function makeAnalyticsRow(overrides: Partial<AttackAnalyticsResultRow> = {}): AttackAnalyticsResultRow {
  return {
    attack_result_id: 'result-1',
    objective_preview: 'Inspect a saved response',
    outcome: 'success',
    updated_at: ANALYTICS_TEST_TIME,
    operation_id: ANALYTICS_NIGHTLY_ID,
    operation: 'Nightly',
    operator: 'Alice',
    attack_type: 'PromptSendingAttack',
    target_model: 'Retired model',
    target_identifier_hash: 'retired-target-hash',
    scenario_result_id: null,
    targeted_harm_categories: ['category-A'],
    request_converters: ['ConverterA', 'ConverterB'],
    response_converters: [],
    labels: { team: 'research' },
    ...overrides,
  }
}

export function makeAnalyticsResults(overrides: Partial<AttackAnalyticsResults> = {}): AttackAnalyticsResults {
  return {
    items: [makeAnalyticsRow()],
    has_more: false,
    next_cursor: null,
    computed_at: ANALYTICS_TEST_TIME,
    ...overrides,
  }
}

export function makeAnalyticsReport(overrides: Partial<AttackAnalyticsReport> = {}): AttackAnalyticsReport {
  return {
    filters: { dimensions: [], outcomes: [] },
    group_by: { name: 'operation' },
    compare_by: null,
    summary: ANALYTICS_TEST_STATISTICS,
    outcome_filter_applied: false,
    groups_overlap: false,
    drilldown_unavailable_reason: null,
    groups: [{
      key: { kind: 'value', value: ANALYTICS_NIGHTLY_ID },
      label: 'Nightly',
      statistics: ANALYTICS_TEST_STATISTICS,
      drilldown_filters: [ANALYTICS_OPERATION_FILTER],
    }],
    has_more_groups: false,
    next_group_offset: null,
    rows: [],
    columns: [],
    cells: [],
    axes_truncated: false,
    results: makeAnalyticsResults(),
    computed_at: ANALYTICS_TEST_TIME,
    warnings: [],
    ...overrides,
  }
}

export function makeAnalyticsFacets(overrides: Partial<AttackAnalyticsFacets> = {}): AttackAnalyticsFacets {
  return {
    items: [
      { key: { kind: 'value', value: ANALYTICS_NIGHTLY_ID }, label: 'Nightly' },
      { key: { kind: 'legacy_operation_name', value: 'Unknown' }, label: 'Unknown (legacy name)' },
      { key: { kind: 'missing', value: null }, label: 'Unknown' },
    ],
    has_more: false,
    next_offset: null,
    computed_at: ANALYTICS_TEST_TIME,
    ...overrides,
  }
}
