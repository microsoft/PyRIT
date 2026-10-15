import type {
  AttackAnalyticsDenominator,
  AttackAnalyticsDimension,
  AttackAnalyticsFilter,
  AttackAnalyticsFilters,
  AttackAnalyticsOption,
  AttackAnalyticsQuery,
  AttackAnalyticsStatistics,
  AttackAnalyticsValue,
  AttackAnalyticsViewState,
  AttackOutcome,
} from '@/types'

export const ANALYTICS_PAGE_SIZE = 25
export const ANALYTICS_GROUP_LIMIT = 15
export const ANALYTICS_AXIS_LIMIT = 20
export const ANALYTICS_DEBOUNCE_MS = 250
export const ANALYTICS_MAX_PREDICATES = 16
export const ANALYTICS_MAX_VALUES = 100
export const ANALYTICS_OUTCOMES: AttackOutcome[] = ['success', 'failure', 'error', 'undetermined']

export const ANALYTICS_ASR_NOTE =
  '* Outcome filter active. ASR uses only the selected outcomes and may be 100% when only successes are selected.'
export const ANALYTICS_ASR_DEFINITION =
  'Attack success rate is attacker successes among selected decided results. Errors and undetermined results are excluded.'
export const ANALYTICS_ALL_ASR_DEFINITION =
  'Attack success rate uses all selected results as its denominator, including errors and undetermined results.'

export const DEFAULT_ANALYTICS_VIEW: AttackAnalyticsViewState = {
  // Match the URL parser's canonical shape before the first URL edit, too.
  filters: { dimensions: [], outcomes: [], updated_after: null, updated_before: null },
  groupBy: { name: 'operation' },
  heatmapRow: { name: 'targeted_harm_category' },
  heatmapColumn: { name: 'attack_type' },
  chart: 'outcomes',
  heatmapMetric: 'success_rate',
  denominator: 'decided',
}

export const ANALYTICS_DIMENSIONS: Array<{ value: string; label: string; dimension: AttackAnalyticsDimension }> = [
  { value: 'operation', label: 'Operation', dimension: { name: 'operation' } },
  { value: 'operator', label: 'Operator', dimension: { name: 'operator' } },
  { value: 'objective_target', label: 'Objective target', dimension: { name: 'objective_target' } },
  { value: 'model', label: 'Model', dimension: { name: 'model' } },
  { value: 'targeted_harm_category', label: 'Targeted harm category', dimension: { name: 'targeted_harm_category' } },
  { value: 'attack_type', label: 'Attack type', dimension: { name: 'attack_type' } },
  { value: 'request_converters', label: 'Request converter', dimension: { name: 'converter_type', converter_direction: 'request' } },
  { value: 'response_converters', label: 'Response converter', dimension: { name: 'converter_type', converter_direction: 'response' } },
  { value: 'scenario', label: 'Scenario run', dimension: { name: 'scenario' } },
]

const PERCENT_FORMAT = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 })
const COUNT_FORMAT = new Intl.NumberFormat()
const LABEL_KEY_PATTERN = /^[A-Za-z0-9_.-]{1,128}$/
const AWARE_TIMESTAMP_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(Z|[+-]\d{2}:\d{2})$/i

/**
 * Identity includes the custom label key and converter direction, not just the
 * dimension name. Omitted direction and explicit "request" are the same dimension.
 */
export function analyticsDimensionKey(dimension: AttackAnalyticsDimension): string {
  return JSON.stringify([dimension.name, dimension.label_key ?? null, dimension.converter_direction ?? 'request'])
}

/** Use typed keys for equality/React keys; display labels can collide with each other and with absence labels. */
export function analyticsValueKey(value: AttackAnalyticsValue): string {
  return JSON.stringify([value.kind, value.value])
}

export function analyticsDimensionLabel(dimension: AttackAnalyticsDimension): string {
  if (dimension.name === 'label') return `Label: ${dimension.label_key}`
  return ANALYTICS_DIMENSIONS.find(
    (option: typeof ANALYTICS_DIMENSIONS[number]) =>
      analyticsDimensionKey(option.dimension) === analyticsDimensionKey(dimension),
  )?.label ?? dimension.name
}

/** Fallback for restored URL selections whose friendly server labels have not been fetched. */
export function analyticsValueLabel(value: AttackAnalyticsValue): string {
  if (value.kind === 'missing') return '(Missing metadata)'
  if (value.kind === 'no_converters') return '(No converters)'
  return value.value || '(Empty value)'
}

/** Keep the server's friendly label, but distinguish typed absences from literal "Unknown"/"No converters" values. */
export function analyticsOptionLabel(option: AttackAnalyticsOption): string {
  if (option.key.kind === 'missing') return `${option.label} (missing metadata)`
  if (option.key.kind === 'no_converters') return `${option.label} (empty pipeline)`
  return option.label || '(Empty value)'
}

/** Format a server-computed ratio; no decided results (null) must never become a misleading 0%. */
export function formatAnalyticsPercent(value: number | null): string {
  return value === null ? 'Unavailable' : PERCENT_FORMAT.format(value)
}

export function formatAnalyticsCount(value: number): string {
  return COUNT_FORMAT.format(value)
}

export function analyticsSuccessRate(
  statistics: AttackAnalyticsStatistics,
  denominator: AttackAnalyticsDenominator = 'decided',
): number | null {
  return denominator === 'decided' ? statistics.success_rate_decided : statistics.success_rate_all
}

export function analyticsAsrDefinition(denominator: AttackAnalyticsDenominator): string {
  return denominator === 'decided' ? ANALYTICS_ASR_DEFINITION : ANALYTICS_ALL_ASR_DEFINITION
}

/** Show the SDK numerator and denominator explicitly, separate from the full result population. */
export function analyticsSuccessCountsLabel(
  statistics: Pick<AttackAnalyticsStatistics, 'successes' | 'total_decided' | 'total_results'>,
  denominator: AttackAnalyticsDenominator = 'decided',
): string {
  const count = denominator === 'decided' ? statistics.total_decided : statistics.total_results
  return `${formatAnalyticsCount(statistics.successes)} success / ${formatAnalyticsCount(count)} ${denominator === 'decided' ? 'decided' : 'all results'}`
}

/** Display a time-zone-aware server timestamp in the reader's local time zone. */
export function formatAnalyticsTime(value: string): string {
  return new Date(value).toLocaleString()
}

/** Text equivalent of a chart mark for accessible names/tooltips, using SDK counts and rates unchanged. */
export function analyticsStatisticsLabel(
  statistics: AttackAnalyticsStatistics,
  marked: boolean,
  denominator: AttackAnalyticsDenominator = 'decided',
): string {
  return `${formatAnalyticsCount(statistics.total_results)} results; ` +
    `ASR${marked ? '*' : ''} ${formatAnalyticsPercent(analyticsSuccessRate(statistics, denominator))}; ` +
    `${analyticsSuccessCountsLabel(statistics, denominator)}; ` +
    `${statistics.failures} failures; ${statistics.errors} errors; ${statistics.undetermined} undetermined`
}

export function hasAnalyticsFilters(filters: AttackAnalyticsFilters): boolean {
  return filters.dimensions.length > 0 || filters.outcomes.length > 0 ||
    Boolean(filters.updated_after) || Boolean(filters.updated_before)
}

/**
 * Append the server's exact drill-down predicates as additional AND conditions.
 * Do not merge repeated dimensions: (converter A OR B) AND C is not A OR B OR C.
 * An outcome-segment click replaces only the outcome restriction, retaining dates.
 */
export function appendAnalyticsDrilldown(
  filters: AttackAnalyticsFilters,
  predicates: AttackAnalyticsFilter[],
  outcome?: AttackOutcome,
): AttackAnalyticsFilters {
  return {
    ...filters,
    dimensions: [...filters.dimensions, ...predicates],
    outcomes: outcome ? [outcome] : filters.outcomes,
  }
}

/**
 * Project shareable view state onto the server contract. Outcome/rate bars share
 * a request, and heatmap color uses statistics already present in each cell.
 * Only the active axes are sent; switching a display-only control needs no read.
 */
export function analyticsQuery(
  view: AttackAnalyticsViewState,
  groupOffset = 0,
): AttackAnalyticsQuery {
  return {
    filters: view.filters,
    group_by: view.chart === 'heatmap' ? view.heatmapRow : view.groupBy,
    compare_by: view.chart === 'heatmap' ? view.heatmapColumn : null,
    group_limit: ANALYTICS_GROUP_LIMIT,
    group_offset: groupOffset,
    axis_limit: ANALYTICS_AXIS_LIMIT,
    result_limit: ANALYTICS_PAGE_SIZE,
  }
}

/** Operation/operator use dedicated dimensions, never the arbitrary-label lookup path. */
export function isAnalyticsLabelKey(value: string): boolean {
  return LABEL_KEY_PATTERN.test(value) && value !== 'operation' && value !== 'operator'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function requireKeys(value: Record<string, unknown>, keys: string[]): void {
  if (Object.keys(value).some((key: string) => !keys.includes(key))) {
    throw new Error('Unknown analytics fields. No filters were applied.')
  }
}

/** Rebuild allowlisted fields from untrusted URL JSON instead of asserting a shared TypeScript type. */
function parseDimension(value: unknown): AttackAnalyticsDimension {
  if (!isRecord(value)) throw new Error('A dimension is missing.')
  requireKeys(value, ['name', 'label_key', 'converter_direction'])
  if (value.converter_direction !== undefined &&
    value.converter_direction !== 'request' && value.converter_direction !== 'response') {
    throw new Error('Unknown converter direction.')
  }
  if (value.name === 'label') {
    if (typeof value.label_key !== 'string' || !isAnalyticsLabelKey(value.label_key)) {
      throw new Error('Label keys must use 1-128 letters, numbers, underscores, periods or hyphens. Use dedicated operation/operator filters.')
    }
    if (value.converter_direction === 'response') throw new Error('Converter direction applies only to converters.')
    return { name: 'label', label_key: value.label_key }
  }
  if (value.label_key != null) throw new Error('Only custom labels accept a label key.')
  if (value.name === 'converter_type') {
    return { name: 'converter_type', converter_direction: value.converter_direction ?? 'request' }
  }
  if (value.converter_direction === 'response') throw new Error('Converter direction applies only to converters.')
  switch (value.name) {
    case 'operation':
    case 'operator':
    case 'targeted_harm_category':
    case 'attack_type':
    case 'objective_target':
    case 'model':
    case 'scenario':
      return { name: value.name }
    default:
      throw new Error('Unknown analytics dimension.')
  }
}

function parseValue(value: unknown): AttackAnalyticsValue {
  if (!isRecord(value)) throw new Error('Invalid filter value.')
  requireKeys(value, ['kind', 'value'])
  if (value.kind === 'value' && typeof value.value === 'string' && [...value.value].length <= 4096) {
    return { kind: 'value', value: value.value }
  }
  if ((value.kind === 'missing' || value.kind === 'no_converters') && value.value === null) {
    return { kind: value.kind, value: null }
  }
  throw new Error('Filter values must use a typed value, missing metadata, or no-converters key.')
}

/** Reject malformed predicates rather than dropping them and accidentally broadening a shared cohort. */
function parseFilter(value: unknown): AttackAnalyticsFilter {
  if (!isRecord(value) || !Array.isArray(value.values) ||
    value.values.length === 0 || value.values.length > ANALYTICS_MAX_VALUES) {
    throw new Error('Each filter needs between 1 and 100 values.')
  }
  requireKeys(value, ['dimension', 'values', 'match_mode'])
  const dimension = parseDimension(value.dimension)
  const values = value.values.map(parseValue)
  const matchMode = value.match_mode === undefined ? 'any' : value.match_mode
  if (matchMode !== 'any' && matchMode !== 'all') throw new Error('Unknown filter match mode.')
  if (dimension.name !== 'converter_type' &&
    (matchMode === 'all' || values.some((item: AttackAnalyticsValue) => item.kind === 'no_converters'))) {
    throw new Error('ALL matching and no-converters values apply only to converter filters.')
  }
  if ((dimension.name === 'operation' || dimension.name === 'operator') &&
    values.some((item: AttackAnalyticsValue) => item.value !== null && [...item.value].length > 128)) {
    throw new Error('Operation and operator values must not exceed 128 characters.')
  }
  return { dimension, values, match_mode: matchMode }
}

/** Fixed-width UTC instants retain Python's microsecond precision, including across DST folds. */
function canonicalTimestamp(value: string): string {
  const match = AWARE_TIMESTAMP_PATTERN.exec(value)
  const milliseconds = Date.parse(value)
  if (!match || !Number.isFinite(milliseconds)) {
    throw new Error('Last updated bounds must be ISO 8601 timestamps with Z or an explicit time-zone offset.')
  }
  const [, year, month, day, hour, minute, second = '0', fraction = '', offset] = match
  const calendar = new Date(`${year}-${month}-${day}T00:00:00Z`)
  if (Number(year) < 1 || calendar.getUTCMonth() + 1 !== Number(month) ||
    calendar.getUTCDate() !== Number(day) || Number(hour) > 23 || Number(minute) > 59 ||
    Number(second) > 59 || (offset !== 'Z' && offset !== 'z' &&
      (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(4)) > 59))) {
    throw new Error('Enter a valid last updated timestamp.')
  }
  const utc = new Date(milliseconds).toISOString()
  if (utc.length !== 24 || utc.startsWith('0000')) {
    throw new Error('Last updated bounds must be representable in UTC years 0001 through 9999.')
  }
  return `${utc.slice(0, 19)}.${fraction.padEnd(6, '0')}Z`
}

function parseTimestamp(value: unknown): string | null {
  if (value == null) return null
  if (typeof value !== 'string') throw new Error('Last updated bounds must be timestamps with a time zone.')
  canonicalTimestamp(value)
  return value
}

export function analyticsDateRangeError(after: string | null, before: string | null): string | null {
  try {
    const start = after ? canonicalTimestamp(after) : null
    const end = before ? canonicalTimestamp(before) : null
    if (start && end && start >= end) return 'Last updated after must be earlier than last updated before.'
    return null
  } catch (error: unknown) {
    return error instanceof Error ? error.message : 'Invalid last updated range.'
  }
}

/** Bound URL input before requesting data; preserve repeated predicates and their AND semantics. */
function parseFilters(value: unknown): AttackAnalyticsFilters {
  if (!isRecord(value) || !Array.isArray(value.dimensions) ||
    value.dimensions.length > ANALYTICS_MAX_PREDICATES || !Array.isArray(value.outcomes) || value.outcomes.length > 4) {
    throw new Error('Filters allow at most 16 predicates and four outcomes.')
  }
  requireKeys(value, ['dimensions', 'outcomes', 'updated_after', 'updated_before'])
  const dimensions = value.dimensions.map(parseFilter)
  const outcomes: AttackOutcome[] = value.outcomes.map((outcome: unknown) => {
    if (outcome === 'success' || outcome === 'failure' || outcome === 'error' || outcome === 'undetermined') {
      return outcome
    }
    throw new Error('Unknown attack outcome.')
  })
  let valueCount = 0
  for (const predicate of dimensions) valueCount += predicate.values.length
  if (valueCount > 500) throw new Error('Select at most 500 dimension values.')
  const after = parseTimestamp(value.updated_after)
  const before = parseTimestamp(value.updated_before)
  const rangeError = analyticsDateRangeError(after, before)
  if (rangeError) throw new Error(rangeError)
  return { dimensions, outcomes: [...new Set(outcomes)], updated_after: after, updated_before: before }
}

/** One validation path for shared links and proposed UI edits, including currently inactive chart settings. */
function parseView(value: unknown): AttackAnalyticsViewState {
  if (!isRecord(value)) throw new Error('Invalid analytics view.')
  requireKeys(value, ['filters', 'groupBy', 'heatmapRow', 'heatmapColumn', 'chart', 'heatmapMetric', 'denominator'])
  const chart = value.chart
  const heatmapMetric = value.heatmapMetric
  if (chart !== 'outcomes' && chart !== 'success-rate' && chart !== 'heatmap') {
    throw new Error('Unknown analytics chart.')
  }
  if (heatmapMetric !== 'success_rate' && heatmapMetric !== 'total_results') {
    throw new Error('Unknown heatmap metric.')
  }
  const denominator = value.denominator === undefined ? 'decided' : value.denominator
  if (denominator !== 'decided' && denominator !== 'all') throw new Error('Unknown ASR denominator.')
  const heatmapRow = parseDimension(value.heatmapRow)
  const heatmapColumn = parseDimension(value.heatmapColumn)
  if (analyticsDimensionKey(heatmapRow) === analyticsDimensionKey(heatmapColumn)) {
    throw new Error('Choose different heatmap row and column dimensions.')
  }
  return {
    filters: parseFilters(value.filters),
    groupBy: parseDimension(value.groupBy),
    heatmapRow,
    heatmapColumn,
    chart,
    heatmapMetric,
    denominator,
  }
}

/** Validate a proposed edit before committing it to browser history or issuing a request. */
export function analyticsViewError(view: AttackAnalyticsViewState): string | null {
  try {
    parseView(view)
    return null
  } catch (error: unknown) {
    return error instanceof Error ? error.message : 'Invalid analytics view.'
  }
}

/** Serialize validated exploration state, not transient page cursors, open editors, or refresh generations. */
export function analyticsViewToSearchParams(view: AttackAnalyticsViewState): URLSearchParams {
  return new URLSearchParams({ analytics: JSON.stringify(view) })
}

/** Fragments are never sent in HTTP request targets, including on reload or direct navigation. */
export function analyticsViewToHash(view: AttackAnalyticsViewState): string {
  return `#${analyticsViewToSearchParams(view)}`
}

export function analyticsViewFromLocation(
  location: { readonly search: string; readonly hash: string },
): { view: AttackAnalyticsViewState; error: string | null } {
  const search = new URLSearchParams(location.search)
  const fragment = new URLSearchParams(location.hash.slice(1))
  if (location.hash && (!fragment.has('analytics') || search.size > 0)) {
    return { view: DEFAULT_ANALYTICS_VIEW, error: 'Cannot restore this analytics link. Use one analytics state, in the URL fragment.' }
  }
  return analyticsViewFromSearchParams(location.hash ? fragment : search)
}

/**
 * Restore and validate a complete exploration. On error the default view exists
 * only to render recovery controls: callers must disable requests until reset.
 * Silently querying that fallback would broaden the cohort in a malformed link.
 */
export function analyticsViewFromSearchParams(
  params: URLSearchParams,
): { view: AttackAnalyticsViewState; error: string | null } {
  try {
    for (const key of params.keys()) {
      if (key !== 'analytics') throw new Error(`Unknown analytics URL parameter: ${key}.`)
    }
    const encoded = params.get('analytics')
    if (encoded === null) return { view: DEFAULT_ANALYTICS_VIEW, error: null }
    if (params.getAll('analytics').length !== 1) throw new Error('Multiple analytics states are ambiguous.')
    const value: unknown = JSON.parse(encoded)
    return { view: parseView(value), error: null }
  } catch (error: unknown) {
    return {
      view: DEFAULT_ANALYTICS_VIEW,
      error: `Cannot restore this analytics link. ${error instanceof Error ? error.message : 'Invalid view.'}`,
    }
  }
}

/** Request identity only: preserve predicate order and budget, but normalize equivalent set/instant spellings. */
export function analyticsFiltersKey(filters: AttackAnalyticsFilters): string {
  const dimensions = filters.dimensions.map((predicate: AttackAnalyticsFilter) => JSON.stringify([
    analyticsDimensionKey(predicate.dimension), predicate.match_mode,
    predicate.values.map(analyticsValueKey).sort(),
  ])).sort()
  const outcomes = [...new Set(filters.outcomes)].sort()
  return JSON.stringify([
    dimensions, outcomes.length === ANALYTICS_OUTCOMES.length ? [] : outcomes,
    filters.updated_after ? canonicalTimestamp(filters.updated_after) : null,
    filters.updated_before ? canonicalTimestamp(filters.updated_before) : null,
  ])
}

export function analyticsQueryKey(query: AttackAnalyticsQuery | null): string {
  if (query === null) return 'null'
  return JSON.stringify([
    analyticsFiltersKey(query.filters ?? DEFAULT_ANALYTICS_VIEW.filters),
    analyticsDimensionKey(query.group_by ?? DEFAULT_ANALYTICS_VIEW.groupBy),
    query.compare_by ? analyticsDimensionKey(query.compare_by) : null,
    query.group_limit ?? ANALYTICS_GROUP_LIMIT, query.group_offset ?? 0,
    query.axis_limit ?? ANALYTICS_AXIS_LIMIT, query.result_limit ?? ANALYTICS_PAGE_SIZE,
  ])
}
