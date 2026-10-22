import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'

import { analyticsApi, operationsApi } from '@/services/api'
import { useRuntime } from '@/hooks/useRuntime'
import {
  ANALYTICS_EMPTY_STATISTICS, ANALYTICS_NIGHTLY_ID, ANALYTICS_OPERATION_FILTER, ANALYTICS_OPERATIONS, ANALYTICS_PAGE_TIME,
  ANALYTICS_PREDICATE_LIMIT_REASON, ANALYTICS_TEST_STATISTICS, ANALYTICS_TEST_TIME, ANALYTICS_VALUE_LIMIT_REASON,
  makeAnalyticsFacets, makeAnalyticsReport, makeAnalyticsResults, makeAnalyticsRow,
} from '@/test-utils/analyticsFixtures'
import type {
  AttackAnalyticsFilter, AttackAnalyticsFilters, AttackAnalyticsQuery, AttackAnalyticsReport,
  AttackAnalyticsValue, AttackAnalyticsViewState, OperationListResponse,
} from '@/types'
import {
  ANALYTICS_ASR_NOTE, ANALYTICS_DEBOUNCE_MS, DEFAULT_ANALYTICS_VIEW, analyticsViewToHash, formatAnalyticsTime,
} from '@/utils/attackAnalytics'

import AnalyticsPage from './AnalyticsPage'

jest.mock('@/services/api', () => ({
  analyticsApi: { query: jest.fn(), results: jest.fn(), facets: jest.fn() },
  operationsApi: { list: jest.fn() },
}))
jest.mock('@/hooks/useRuntime', () => ({ useRuntime: jest.fn() }))
const mockRuntime = jest.mocked(useRuntime)

const mockQuery = jest.mocked(analyticsApi.query)
const mockResults = jest.mocked(analyticsApi.results)
const mockFacets = jest.mocked(analyticsApi.facets)
const mockList = jest.mocked(operationsApi.list)

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

function NavigationProbe() {
  const navigate = useNavigate()
  const location = useLocation()
  return <><button onClick={() => { void navigate(-1) }}>Browser back</button><output aria-label="Current URL">{location.search}{location.hash}</output></>
}

function renderPage(view?: AttackAnalyticsViewState, onOpenAttack: (id: string) => void = jest.fn()) {
  const path = view ? `/analytics${analyticsViewToHash(view)}` : '/analytics'
  const page = () => (
    <TestWrapper><MemoryRouter initialEntries={[path]}>
      <AnalyticsPage onOpenAttack={onOpenAttack} /><NavigationProbe />
    </MemoryRouter></TestWrapper>
  )
  const rendered = render(page())
  // A fresh element re-renders the page, keeping the router's history, so it reads a changed runtime.
  return { ...rendered, rerenderPage: (): void => { rendered.rerender(page()) } }
}

function deferred<T>() {
  let resolve: ((value: T) => void) | undefined
  let reject: ((reason: unknown) => void) | undefined
  const promise = new Promise<T>((onResolve: (value: T | PromiseLike<T>) => void, onReject: (reason: unknown) => void) => {
    resolve = onResolve
    reject = onReject
  })
  return {
    promise,
    resolve: (value: T): void => { if (resolve) resolve(value) },
    reject: (reason: unknown): void => { if (reject) reject(reason) },
  }
}

function echoQuery(query: AttackAnalyticsQuery): AttackAnalyticsReport {
  return makeAnalyticsReport({
    filters: query.filters ?? { dimensions: [], outcomes: [] },
    group_by: query.group_by ?? { name: 'operation' },
    compare_by: query.compare_by ?? null,
  })
}

interface DrilldownBoundaryCase {
  readonly chart: 'outcomes' | 'heatmap'
  readonly budget: 'predicates' | 'values'
  readonly initialSize: number
}

type LabelEpochChange = 'Reload' | 'runtime change'

const RENAMED_OPERATIONS: OperationListResponse = { items: [{ ...ANALYTICS_OPERATIONS.items[0], name: 'Renamed' }] }

/** Renames Nightly in the reads the page repeats, as a rename in another tab would. */
function renameNightly(): void {
  mockList.mockResolvedValue(RENAMED_OPERATIONS)
  mockQuery.mockImplementation(async (query: AttackAnalyticsQuery) => makeAnalyticsReport({
    ...echoQuery(query), groups: [{ ...makeAnalyticsReport().groups[0], label: 'Renamed' }],
  }))
}

/** Waits for the current report, then clicks Reload or switches the runtime generation. */
async function changeLabelEpoch(
  change: LabelEpochChange, user: ReturnType<typeof userEvent.setup>, rerenderPage: () => void,
): Promise<void> {
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Reload' })).toBeEnabled() })
  if (change === 'Reload') {
    await user.click(screen.getByRole('button', { name: 'Reload' }))
  } else {
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'second-runtime' })
    rerenderPage()
  }
}

describe('AnalyticsPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'first-runtime' })
    mockQuery.mockReset().mockImplementation(async (query: AttackAnalyticsQuery) => echoQuery(query))
    mockResults.mockReset().mockResolvedValue(makeAnalyticsResults())
    mockFacets.mockReset().mockResolvedValue(makeAnalyticsFacets())
    mockList.mockReset().mockResolvedValue(ANALYTICS_OPERATIONS)
  })

  afterEach(() => { jest.useRealTimers() })

  it.each<DrilldownBoundaryCase>([
    { chart: 'outcomes', budget: 'predicates', initialSize: 15 },
    { chart: 'heatmap', budget: 'predicates', initialSize: 14 },
    { chart: 'outcomes', budget: 'values', initialSize: 499 },
    { chart: 'heatmap', budget: 'values', initialSize: 498 },
  ])(
    'should allow the final valid $chart drill-down at the $budget limit without disabling the report',
    async ({ chart, budget, initialSize }: DrilldownBoundaryCase) => {
      jest.useFakeTimers()
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      const reason = budget === 'predicates' ? ANALYTICS_PREDICATE_LIMIT_REASON : ANALYTICS_VALUE_LIMIT_REASON
      const filters: AttackAnalyticsFilters = {
        ...DEFAULT_ANALYTICS_VIEW.filters,
        dimensions: budget === 'predicates'
          ? Array.from({ length: initialSize }, () => ANALYTICS_OPERATION_FILTER)
          : Array.from({ length: 5 }, (_unused: unknown, index: number): AttackAnalyticsFilter => ({
            ...ANALYTICS_OPERATION_FILTER,
            values: Array.from({ length: index === 4 ? initialSize - 400 : 100 }, (_value: unknown, value: number): AttackAnalyticsValue => ({
              kind: 'value', value: value === 0 ? ANALYTICS_NIGHTLY_ID : `00000000-0000-4000-8000-${String(index * 100 + value).padStart(12, '0')}`,
            })),
          })),
      }
      const row: AttackAnalyticsFilter = {
        dimension: DEFAULT_ANALYTICS_VIEW.heatmapRow,
        values: [{ kind: 'value', value: 'category-A' }], match_mode: 'any',
      }
      const column: AttackAnalyticsFilter = {
        dimension: DEFAULT_ANALYTICS_VIEW.heatmapColumn,
        values: [{ kind: 'value', value: 'PromptSendingAttack' }], match_mode: 'any',
      }
      const additions = chart === 'heatmap' ? [row, column] : [ANALYTICS_OPERATION_FILTER]
      const reportForQuery = (query: AttackAnalyticsQuery, unavailableReason: string | null): AttackAnalyticsReport => makeAnalyticsReport({
        ...echoQuery(query),
        drilldown_unavailable_reason: unavailableReason,
        has_more_groups: true, next_group_offset: 15,
        results: makeAnalyticsResults({ has_more: true, next_cursor: 'page-at-limit' }),
        ...(chart === 'heatmap' ? {
          rows: [{ key: row.values[0], label: 'category-A' }],
          columns: [{ key: column.values[0], label: 'PromptSendingAttack' }],
          cells: [{
            row: row.values[0], column: column.values[0],
            statistics: ANALYTICS_TEST_STATISTICS, drilldown_filters: additions,
          }],
        } : {}),
      })
      mockQuery.mockImplementation(async (query: AttackAnalyticsQuery) => reportForQuery(query, reason))
        .mockImplementationOnce(async (query: AttackAnalyticsQuery) => reportForQuery(query, null))
      mockResults.mockResolvedValue(makeAnalyticsResults({
        items: [makeAnalyticsRow({ objective_preview: 'Paged at the limit' })],
      }))
      renderPage({ ...DEFAULT_ANALYTICS_VIEW, chart, filters })
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
      const actionName = chart === 'outcomes' ? 'Filter to Nightly' : /category-A \/ PromptSendingAttack: 10 results/
      await user.click(screen.getByRole('button', { name: actionName }))
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })

      expect(mockQuery).toHaveBeenCalledTimes(2)
      expect(mockQuery.mock.calls[1][0].filters?.dimensions).toEqual([...filters.dimensions, ...additions])
      const notice = screen.getByText(reason, { selector: '[role="note"]' })
      expect(notice).toBeVisible()
      expect(screen.getByRole('region', { name: 'Outcome summary' })).toBeVisible()
      const chartRegion = screen.getByRole('region', { name: chart === 'outcomes' ? 'Outcome breakdown by Operation' : 'Heatmap' })
      expect(chartRegion).toBeVisible()
      expect(chartRegion).toHaveAccessibleDescription(reason)
      for (const button of within(chartRegion).getAllByRole('button')) {
        if (chart === 'outcomes') expect(button).toBeDisabled()
        else expect(button).toHaveAttribute('aria-disabled', 'true')
      }
      const url = screen.getByLabelText('Current URL').textContent
      await user.click(screen.getByRole('button', { name: actionName }))
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
      expect(screen.getByLabelText('Current URL').textContent).toBe(url)
      expect(mockQuery).toHaveBeenCalledTimes(2)

      const results = screen.getByRole('region', { name: 'Matching AttackResults' })
      await user.click(within(results).getByRole('button', { name: 'Next' }))
      expect(await within(results).findByText('Paged at the limit')).toBeInTheDocument()
      expect(within(results).getByText('Page 2')).toBeInTheDocument()
      expect(mockResults).toHaveBeenCalledTimes(1)
      expect(mockQuery).toHaveBeenCalledTimes(2)
      if (chart === 'outcomes') expect(screen.getByRole('button', { name: 'Next groups' })).toBeEnabled()

      await user.click(screen.getByRole('button', { name: 'Filter to success: 4' }))
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
      expect(mockQuery).toHaveBeenCalledTimes(3)
      expect(mockQuery.mock.calls[2][0].filters).toMatchObject({
        dimensions: [...filters.dimensions, ...additions], outcomes: ['success'],
      })
      expect(screen.getByText(reason, { selector: '[role="note"]' })).toBeVisible()
    },
  )

  it('should load all saved results once, use server statistics and reuse the included first page', async () => {
    mockQuery.mockResolvedValue(makeAnalyticsReport({
      summary: {
        success_rate: 0.625, success_rate_decided: 0.625, success_rate_all: 15 / 64,
        successes: 15, failures: 9, errors: 10, undetermined: 30, total_decided: 24,
        total_results: 64, decided_share: 0.375,
        outcome_shares: { success: 15 / 64, failure: 9 / 64, error: 10 / 64, undetermined: 30 / 64 },
      },
    }))
    const onOpenAttack = jest.fn()
    const user = userEvent.setup()
    renderPage(undefined, onOpenAttack)
    expect(screen.getByRole('status', { name: 'Loading analytics for current filters' })).toBeInTheDocument()
    expect(await screen.findByText('Inspect a saved response')).toBeInTheDocument()
    const summary = screen.getByRole('region', { name: 'Outcome summary' })
    expect(within(summary).getByText('62.5%')).toBeInTheDocument()
    expect(within(summary).getByText('37.5%')).toBeInTheDocument()
    expect(within(summary).getByText('15 success / 24 decided')).toBeInTheDocument()
    expect(within(summary).getByText('64 total')).toBeInTheDocument()
    expect(mockQuery).toHaveBeenCalledTimes(1)
    expect(mockQuery).toHaveBeenCalledWith(expect.objectContaining({
      filters: DEFAULT_ANALYTICS_VIEW.filters,
      group_by: { name: 'operation' }, compare_by: null, result_limit: 25,
    }), expect.any(AbortSignal))
    expect(mockResults).not.toHaveBeenCalled()
    expect(mockFacets).not.toHaveBeenCalled()
    await user.click(screen.getByRole('row', { name: 'Open result result-1' }))
    expect(onOpenAttack).toHaveBeenCalledWith('result-1')
  })

  it('should paginate only results, keep report freshness, and reset the table on Reload', async () => {
    const user = userEvent.setup()
    mockQuery.mockResolvedValue(makeAnalyticsReport({
      results: makeAnalyticsResults({ has_more: true, next_cursor: 'opaque-next' }),
    }))
    mockResults.mockResolvedValue(makeAnalyticsResults({
      items: [makeAnalyticsRow({ attack_result_id: 'result-2', objective_preview: 'Second saved result' })],
      computed_at: ANALYTICS_PAGE_TIME,
    }))
    renderPage()
    await screen.findByText('Inspect a saved response')
    const refreshed = screen.getByText(/Last refreshed:/).textContent
    const results = screen.getByRole('region', { name: 'Matching AttackResults' })
    await user.click(within(results).getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Second saved result')).toBeInTheDocument()
    expect(within(results).getByText('Page 2')).toBeInTheDocument()
    expect(screen.getByText(/Last refreshed:/).textContent).toBe(refreshed)
    expect(within(results).getByText(formatAnalyticsTime(ANALYTICS_PAGE_TIME))).toBeInTheDocument()
    expect(mockResults).toHaveBeenCalledWith({
      filters: { dimensions: [], outcomes: [] }, cursor: 'opaque-next', limit: 25,
    }, expect.any(AbortSignal))
    expect(mockQuery).toHaveBeenCalledTimes(1)
    expect(mockFacets).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    expect(await screen.findByText('Inspect a saved response')).toBeInTheDocument()
    await waitFor(() => { expect(mockQuery).toHaveBeenCalledTimes(2) })
    expect(within(results).getByText('Page 1')).toBeInTheDocument()
    expect(mockResults).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['heatmap color', 'heatmap', 'Cell color', 'total_results'],
    ['bar presentation', 'outcomes', 'Chart view', 'success-rate'],
    ['ASR denominator', 'outcomes', 'ASR denominator', 'all'],
    ['heatmap denominator', 'heatmap', 'ASR denominator', 'all'],
  ] as const)(
    'should change only %s without requests or resetting result, group, or facet pages',
    async (_name: string, chart: 'heatmap' | 'outcomes', control: string, value: string) => {
      jest.useFakeTimers()
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      mockQuery.mockImplementation(async (query: AttackAnalyticsQuery) => makeAnalyticsReport({
        ...echoQuery(query),
        has_more_groups: true, next_group_offset: 15,
        results: makeAnalyticsResults({ has_more: true, next_cursor: 'page-2' }),
      }))
      mockResults.mockResolvedValue(makeAnalyticsResults({
        items: [makeAnalyticsRow({ objective_preview: 'Second saved result' })],
        computed_at: ANALYTICS_PAGE_TIME,
      }))
      mockFacets.mockResolvedValue(makeAnalyticsFacets({ has_more: true, next_offset: 50 }))
      renderPage(chart === 'heatmap' ? { ...DEFAULT_ANALYTICS_VIEW, chart } : undefined)
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
      if (chart === 'outcomes') {
        await user.click(screen.getByRole('button', { name: 'Next groups' }))
        await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
      }
      const reportRequests = chart === 'outcomes' ? 2 : 1
      const results = screen.getByRole('region', { name: 'Matching AttackResults' })
      await user.click(within(results).getByRole('button', { name: 'Next' }))
      expect(await within(results).findByText('Page 2')).toBeInTheDocument()
      const refreshed = screen.getByText(/Last refreshed:/).textContent
      await user.click(screen.getByRole('button', { name: 'Operation' }))
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
      await user.click(screen.getByRole('button', { name: 'Next values' }))
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })

      await user.selectOptions(screen.getByLabelText(control), value)
      expect(screen.queryByRole('status', { name: 'Reloading saved results' })).not.toBeInTheDocument()
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS * 2) })

      expect(mockQuery).toHaveBeenCalledTimes(reportRequests)
      expect(mockResults).toHaveBeenCalledTimes(1)
      expect(mockFacets).toHaveBeenCalledTimes(2)
      expect(within(results).getByText('Page 2')).toBeInTheDocument()
      expect(within(results).getByText('Second saved result')).toBeInTheDocument()
      expect(within(results).getByText(formatAnalyticsTime(ANALYTICS_PAGE_TIME))).toBeInTheDocument()
      expect(screen.getByText(/Last refreshed:/).textContent).toBe(refreshed)
      expect(screen.getByRole('button', { name: 'First values' })).toBeEnabled()
      if (chart === 'outcomes') expect(screen.getByRole('button', { name: 'First groups' })).toBeEnabled()
    },
  )

  it('should not abort an in-flight results page when only the heatmap color changes', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const pending = deferred<ReturnType<typeof makeAnalyticsResults>>()
    mockQuery.mockResolvedValue(makeAnalyticsReport({
      results: makeAnalyticsResults({ has_more: true, next_cursor: 'page-2' }),
    }))
    mockResults.mockReturnValueOnce(pending.promise)
    renderPage({ ...DEFAULT_ANALYTICS_VIEW, chart: 'heatmap' })
    await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
    const results = screen.getByRole('region', { name: 'Matching AttackResults' })
    await user.click(within(results).getByRole('button', { name: 'Next' }))
    const signal = mockResults.mock.calls[0][1]
    await user.selectOptions(screen.getByLabelText('Cell color'), 'total_results')
    await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS * 2) })
    expect(signal?.aborted).toBe(false)
    expect(mockQuery).toHaveBeenCalledTimes(1)
    await act(async () => {
      pending.resolve(makeAnalyticsResults({ items: [makeAnalyticsRow({ objective_preview: 'Second saved result' })] }))
    })
    expect(within(results).getByText('Page 2')).toBeInTheDocument()
    expect(within(results).getByText('Second saved result')).toBeInTheDocument()
  })

  it.each([false, true])(
    'should treat returning to an earlier query as a new read without reviving its result page (intervening read settled: %s)',
    async (interveningReadSettled: boolean) => {
      jest.useFakeTimers()
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      const otherReport = deferred<AttackAnalyticsReport>()
      const returnedReport = deferred<AttackAnalyticsReport>()
      mockQuery.mockResolvedValueOnce(makeAnalyticsReport({
        results: makeAnalyticsResults({ has_more: true, next_cursor: 'old-page-2' }),
      })).mockReturnValueOnce(otherReport.promise).mockReturnValueOnce(returnedReport.promise)
      mockResults.mockResolvedValue(makeAnalyticsResults({
        items: [makeAnalyticsRow({ objective_preview: 'Old second page' })],
      }))
      renderPage()
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
      await user.click(within(screen.getByRole('region', { name: 'Matching AttackResults' })).getByRole('button', { name: 'Next' }))
      await screen.findByText('Page 2')
      await user.selectOptions(screen.getByLabelText('Group by'), 'model')
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
      if (interveningReadSettled) {
        await act(async () => { otherReport.resolve(makeAnalyticsReport({ group_by: { name: 'model' } })) })
      }

      await user.click(screen.getByRole('button', { name: 'Browser back' }))
      expect(screen.getByRole('status', {
        name: interveningReadSettled ? 'Loading analytics for current filters' : 'Reloading saved results',
      })).toBeInTheDocument()
      await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
      await act(async () => {
        returnedReport.resolve(makeAnalyticsReport({
          computed_at: ANALYTICS_PAGE_TIME,
          results: makeAnalyticsResults({ items: [makeAnalyticsRow({ objective_preview: 'Fresh original cohort' })] }),
        }))
        otherReport.resolve(makeAnalyticsReport({ group_by: { name: 'model' } }))
      })
      expect(screen.getByText('Fresh original cohort')).toBeInTheDocument()
      expect(screen.getByText('Page 1')).toBeInTheDocument()
      expect(screen.queryByText('Old second page')).not.toBeInTheDocument()
      expect(mockQuery).toHaveBeenCalledTimes(3)
      expect(mockResults).toHaveBeenCalledTimes(1)
    },
  )

  it('should keep a failed Reload visibly stale with its original timestamp and support Retry', async () => {
    const user = userEvent.setup()
    mockQuery.mockResolvedValueOnce(makeAnalyticsReport())
      .mockRejectedValueOnce(new Error('Analytics is busy'))
      .mockResolvedValueOnce(makeAnalyticsReport({ computed_at: ANALYTICS_PAGE_TIME }))
    renderPage()
    await screen.findByText('Inspect a saved response')
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    expect(await screen.findByText(/Reload failed.*data may be stale/)).toBeInTheDocument()
    expect(screen.getByText('Inspect a saved response')).toBeInTheDocument()
    expect(screen.getByText(/Last refreshed:/)).toHaveTextContent(formatAnalyticsTime(ANALYTICS_TEST_TIME))
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => { expect(screen.getByText(/Last refreshed:/)).toHaveTextContent(formatAnalyticsTime(ANALYTICS_PAGE_TIME)) })
    expect(screen.queryByText(/data may be stale/)).not.toBeInTheDocument()
  })

  it('should never show old results under changed filters, including when the new cohort fails', async () => {
    const user = userEvent.setup()
    const pending = deferred<AttackAnalyticsReport>()
    mockQuery.mockResolvedValueOnce(makeAnalyticsReport()).mockReturnValueOnce(pending.promise)
    renderPage()
    await screen.findByText('Inspect a saved response')
    await user.click(screen.getByRole('button', { name: 'Filter to error: 1' }))
    expect(screen.queryByText('Inspect a saved response')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Outcome summary' })).not.toBeInTheDocument()
    await waitFor(() => { expect(mockQuery).toHaveBeenCalledTimes(2) })
    await act(async () => { pending.reject(new Error('Cannot read this cohort')) })
    expect(await screen.findByText(/Could not load analytics for these filters/)).toBeInTheDocument()
    expect(screen.queryByText('Inspect a saved response')).not.toBeInTheDocument()
  })

  it('should abort superseded reports and ignore a late successful response', async () => {
    const user = userEvent.setup()
    const pending = deferred<AttackAnalyticsReport>()
    mockQuery.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(makeAnalyticsReport({
      group_by: { name: 'model' },
      results: makeAnalyticsResults({ items: [makeAnalyticsRow({ objective_preview: 'Latest cohort' })] }),
    }))
    renderPage()
    await waitFor(() => { expect(mockQuery).toHaveBeenCalledTimes(1) })
    const signal = mockQuery.mock.calls[0][1]
    await user.selectOptions(screen.getByLabelText('Group by'), 'model')
    expect(await screen.findByText('Latest cohort')).toBeInTheDocument()
    expect(signal?.aborted).toBe(true)
    await act(async () => { pending.resolve(makeAnalyticsReport()) })
    expect(screen.getByText('Latest cohort')).toBeInTheDocument()
    expect(screen.queryByText('Inspect a saved response')).not.toBeInTheDocument()
  })

  it('should abort an in-flight results page on Reload so it cannot replace the refreshed first page', async () => {
    const user = userEvent.setup()
    const pending = deferred<ReturnType<typeof makeAnalyticsResults>>()
    mockQuery.mockResolvedValue(makeAnalyticsReport({
      results: makeAnalyticsResults({ has_more: true, next_cursor: 'page-2' }),
    }))
    mockResults.mockReturnValueOnce(pending.promise)
    renderPage()
    await screen.findByText('Inspect a saved response')
    await user.click(within(screen.getByRole('region', { name: 'Matching AttackResults' })).getByRole('button', { name: 'Next' }))
    const signal = mockResults.mock.calls[0][1]
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    await waitFor(() => { expect(mockQuery).toHaveBeenCalledTimes(2) })
    expect(signal?.aborted).toBe(true)
    await act(async () => {
      pending.resolve(makeAnalyticsResults({ items: [makeAnalyticsRow({ objective_preview: 'Obsolete page' })] }))
    })
    expect(screen.queryByText('Obsolete page')).not.toBeInTheDocument()
    expect(screen.getByText('Page 1')).toBeInTheDocument()
  })

  it('should preserve report and current rows on a results-only error, then retry that cursor', async () => {
    const user = userEvent.setup()
    mockQuery.mockResolvedValue(makeAnalyticsReport({
      results: makeAnalyticsResults({ has_more: true, next_cursor: 'retry-cursor' }),
    }))
    mockResults.mockRejectedValueOnce(new Error('Page deadline exceeded')).mockResolvedValueOnce(makeAnalyticsResults())
    renderPage()
    await screen.findByText('Inspect a saved response')
    await user.click(within(screen.getByRole('region', { name: 'Matching AttackResults' })).getByRole('button', { name: 'Next' }))
    expect(await screen.findByText(/Still showing page 1/)).toBeInTheDocument()
    expect(screen.getByText('Inspect a saved response')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry results' }))
    await screen.findByText('Page 2')
    expect(mockResults.mock.calls[1][0].cursor).toBe('retry-cursor')
    expect(mockQuery).toHaveBeenCalledTimes(1)
  })

  it('should apply typed group predicates in addition to existing converter, outcome and date filters', async () => {
    const user = userEvent.setup()
    const converters: AttackAnalyticsFilter = {
      dimension: { name: 'converter_type', converter_direction: 'response' },
      values: [{ kind: 'value', value: 'A' }, { kind: 'value', value: 'B' }],
      match_mode: 'any',
    }
    const added: AttackAnalyticsFilter = { ...converters, values: [{ kind: 'missing', value: null }] }
    const view: AttackAnalyticsViewState = {
      ...DEFAULT_ANALYTICS_VIEW,
      groupBy: converters.dimension,
      filters: {
        dimensions: [ANALYTICS_OPERATION_FILTER, converters], outcomes: ['success', 'failure'],
        updated_after: '2026-01-01T00:00:00Z', updated_before: '2026-10-01T00:00:00Z',
      },
    }
    mockQuery.mockImplementation(async (query: AttackAnalyticsQuery) => makeAnalyticsReport({
      ...echoQuery(query), outcome_filter_applied: true, groups_overlap: true,
      groups: [{ key: { kind: 'missing', value: null }, label: 'Unknown', statistics: ANALYTICS_TEST_STATISTICS, drilldown_filters: [added] }],
    }))
    renderPage(view)
    await user.click(await screen.findByRole('button', { name: 'Filter to Unknown (missing metadata)' }))
    await waitFor(() => {
      expect(mockQuery).toHaveBeenLastCalledWith(expect.objectContaining({
        filters: { ...view.filters, dimensions: [...view.filters.dimensions, added] },
      }), expect.any(AbortSignal))
    })
    expect(screen.getByText(/Groups overlap/)).toBeInTheDocument()
  })

  it('should clear a rejected edit message when browser Back restores a valid view', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Inspect a saved response')
    await user.selectOptions(screen.getByLabelText('Chart view'), 'heatmap')
    await user.selectOptions(screen.getByLabelText('Compare by'), 'targeted_harm_category')
    expect(screen.getByText('Choose different heatmap row and column dimensions.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Browser back' }))
    expect(screen.getByLabelText('Chart view')).toHaveValue('outcomes')
    expect(screen.queryByText('Choose different heatmap row and column dimensions.')).not.toBeInTheDocument()
  })

  it('should not revive an old group offset after an axis round trip', async () => {
    const user = userEvent.setup()
    mockQuery.mockImplementation(async (query: AttackAnalyticsQuery) => makeAnalyticsReport({
      ...echoQuery(query), has_more_groups: true, next_group_offset: 15,
    }))
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Next groups' }))
    await waitFor(() => { expect(screen.getByRole('button', { name: 'First groups' })).toBeEnabled() })
    await user.selectOptions(screen.getByLabelText('Group by'), 'model')
    await waitFor(() => { expect(mockQuery).toHaveBeenLastCalledWith(expect.objectContaining({
      group_by: { name: 'model' }, group_offset: 0,
    }), expect.any(AbortSignal)) })
    await user.click(screen.getByRole('button', { name: 'Browser back' }))
    await waitFor(() => { expect(mockQuery).toHaveBeenLastCalledWith(expect.objectContaining({
      group_by: { name: 'operation' }, group_offset: 0,
    }), expect.any(AbortSignal)) })
    expect(mockQuery).toHaveBeenCalledTimes(4)
  })

  it('should restore filter and chart choices with browser Back', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Filter to success: 4' }))
    await user.selectOptions(screen.getByLabelText('Chart view'), 'success-rate')
    expect(screen.getByLabelText('Chart view')).toHaveValue('success-rate')
    await user.click(screen.getByRole('button', { name: 'Browser back' }))
    expect(screen.getByLabelText('Chart view')).toHaveValue('outcomes')
    expect(screen.getByRole('button', { name: 'Remove outcome success' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Browser back' }))
    expect(screen.queryByRole('button', { name: 'Remove outcome success' })).not.toBeInTheDocument()
    await waitFor(() => { expect(mockQuery).toHaveBeenLastCalledWith(expect.objectContaining({ filters: DEFAULT_ANALYTICS_VIEW.filters }), expect.any(AbortSignal)) })
  })

  it('should label a drill-down Operation chip by name', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Filter to Nightly' }))
    expect(await screen.findByRole('button', { name: 'Operation (ANY): Nightly' })).toBeInTheDocument()
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(ANALYTICS_NIGHTLY_ID)
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it('should name a drill-down Operation chip while its Operation lookup is pending', async () => {
    const user = userEvent.setup()
    mockList.mockReturnValue(new Promise(() => {}))
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Filter to Nightly' }))
    expect(screen.getByRole('button', { name: 'Operation (ANY): Nightly' })).toBeInTheDocument()
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it('should name heatmap drill-down chips from the report row and column labels', async () => {
    const user = userEvent.setup()
    const row: AttackAnalyticsFilter = {
      dimension: DEFAULT_ANALYTICS_VIEW.heatmapRow, values: [{ kind: 'value', value: 'privacy' }], match_mode: 'any',
    }
    const column: AttackAnalyticsFilter = {
      dimension: DEFAULT_ANALYTICS_VIEW.heatmapColumn, values: [{ kind: 'value', value: 'promptsendingattack' }], match_mode: 'any',
    }
    mockQuery.mockImplementation(async (query: AttackAnalyticsQuery) => makeAnalyticsReport({
      ...echoQuery(query),
      rows: [{ key: row.values[0], label: 'Privacy' }],
      columns: [{ key: column.values[0], label: 'PromptSendingAttack' }],
      cells: [{ row: row.values[0], column: column.values[0], statistics: ANALYTICS_TEST_STATISTICS, drilldown_filters: [row, column] }],
    }))
    renderPage({ ...DEFAULT_ANALYTICS_VIEW, chart: 'heatmap' })
    await user.click(await screen.findByRole('button', { name: /^Privacy \/ PromptSendingAttack: 10 results/ }))
    expect(screen.getByRole('button', { name: 'Targeted harm category (ANY): Privacy' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Attack type (ANY): PromptSendingAttack' })).toBeInTheDocument()
  })

  it('should name a Scenario drill-down chip from the report label instead of the run ID', async () => {
    const user = userEvent.setup()
    const runId = '7f0c4e2a-5b1d-4c3e-9f8a-2d6b1e4c8a90'
    const scenario: AttackAnalyticsFilter = {
      dimension: { name: 'scenario' }, values: [{ kind: 'value', value: runId }], match_mode: 'any',
    }
    mockQuery.mockImplementation(async (query: AttackAnalyticsQuery) => makeAnalyticsReport({
      ...echoQuery(query),
      groups: [{
        key: scenario.values[0], label: 'Nightly scan (1e4c8a90)',
        statistics: ANALYTICS_TEST_STATISTICS, drilldown_filters: [scenario],
      }],
    }))
    renderPage({ ...DEFAULT_ANALYTICS_VIEW, groupBy: scenario.dimension })
    await user.click(await screen.findByRole('button', { name: 'Filter to Nightly scan (1e4c8a90)' }))
    expect(screen.getByRole('button', { name: 'Scenario run (ANY): Nightly scan (1e4c8a90)' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `Scenario run (ANY): ${runId}` })).not.toBeInTheDocument()
  })

  it('should rename a restored Operation chip on Reload, as the report does', async () => {
    const user = userEvent.setup()
    const { rerenderPage } = renderPage({
      ...DEFAULT_ANALYTICS_VIEW, filters: { ...DEFAULT_ANALYTICS_VIEW.filters, dimensions: [ANALYTICS_OPERATION_FILTER] },
    })
    expect(await screen.findByRole('button', { name: 'Operation (ANY): Nightly' })).toBeInTheDocument()
    renameNightly()
    await changeLabelEpoch('Reload', user, rerenderPage)
    expect(await screen.findByRole('button', { name: 'Filter to Renamed' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Operation (ANY): Renamed' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Operation (ANY): Nightly' })).not.toBeInTheDocument()
    expect(mockList).toHaveBeenCalledTimes(2)
  })

  it.each<LabelEpochChange>(['Reload', 'runtime change'])(
    'should rename a facet-selected Operation chip after a %s',
    async (change: LabelEpochChange) => {
      const user = userEvent.setup()
      const { rerenderPage } = renderPage()
      await user.click(screen.getByRole('button', { name: 'Operation' }))
      await user.click(await screen.findByRole('checkbox', { name: 'Nightly' }))
      await user.click(screen.getByRole('button', { name: 'Apply filter' }))
      expect(screen.getByRole('button', { name: 'Operation (ANY): Nightly' })).toBeInTheDocument()
      await waitFor(() => { expect(mockList).toHaveBeenCalledTimes(1) })
      renameNightly()
      await changeLabelEpoch(change, user, rerenderPage)
      expect(await screen.findByRole('button', { name: 'Operation (ANY): Renamed' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Operation (ANY): Nightly' })).not.toBeInTheDocument()
    },
  )

  it.each<LabelEpochChange>(['Reload', 'runtime change'])(
    'should rename a drill-down Operation chip after a %s',
    async (change: LabelEpochChange) => {
      const user = userEvent.setup()
      const { rerenderPage } = renderPage()
      await user.click(await screen.findByRole('button', { name: 'Filter to Nightly' }))
      expect(screen.getByRole('button', { name: 'Operation (ANY): Nightly' })).toBeInTheDocument()
      await waitFor(() => { expect(mockList).toHaveBeenCalledTimes(1) })
      renameNightly()
      await changeLabelEpoch(change, user, rerenderPage)
      expect(await screen.findByRole('button', { name: 'Operation (ANY): Renamed' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Operation (ANY): Nightly' })).not.toBeInTheDocument()
    },
  )

  it.each<LabelEpochChange>(['Reload', 'runtime change'])(
    'should keep drill-down and facet labels of other dimensions after a %s',
    async (change: LabelEpochChange) => {
      const user = userEvent.setup()
      const scenario: AttackAnalyticsFilter = {
        dimension: { name: 'scenario' }, values: [{ kind: 'value', value: '7f0c4e2a-5b1d-4c3e-9f8a-2d6b1e4c8a90' }], match_mode: 'any',
      }
      mockQuery.mockImplementation(async (query: AttackAnalyticsQuery) => makeAnalyticsReport({
        ...echoQuery(query),
        groups: [{
          key: scenario.values[0], label: 'Nightly scan (1e4c8a90)',
          statistics: ANALYTICS_TEST_STATISTICS, drilldown_filters: [scenario],
        }],
      }))
      mockFacets.mockResolvedValue(makeAnalyticsFacets({
        items: [{ key: { kind: 'value', value: 'objective-target-hash' }, label: 'Production chat' }],
      }))
      const { rerenderPage } = renderPage({ ...DEFAULT_ANALYTICS_VIEW, groupBy: scenario.dimension })
      await user.click(await screen.findByRole('button', { name: 'Filter to Nightly scan (1e4c8a90)' }))
      await user.click(screen.getByRole('button', { name: 'Objective target' }))
      await user.click(await screen.findByRole('checkbox', { name: 'Production chat' }))
      await user.click(screen.getByRole('button', { name: 'Apply filter' }))
      await waitFor(() => { expect(screen.getByRole('button', { name: 'Reload' })).toBeEnabled() })
      const reportReads = mockQuery.mock.calls.length
      await changeLabelEpoch(change, user, rerenderPage)
      await waitFor(() => { expect(mockQuery).toHaveBeenCalledTimes(reportReads + 1) })
      await waitFor(() => { expect(screen.getByRole('button', { name: 'Reload' })).toBeEnabled() })
      expect(screen.getByRole('button', { name: 'Scenario run (ANY): Nightly scan (1e4c8a90)' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Objective target (ANY): Production chat' })).toBeInTheDocument()
    },
  )

  it('should show a legacy Operation name suffix once on heatmap axes, drill-down chips and the chip editor', async () => {
    const user = userEvent.setup()
    const row: AttackAnalyticsFilter = {
      dimension: { name: 'operation' }, values: [{ kind: 'legacy_operation_name', value: 'Retired' }], match_mode: 'any',
    }
    const column: AttackAnalyticsFilter = {
      dimension: { name: 'attack_type' }, values: [{ kind: 'value', value: 'promptsendingattack' }], match_mode: 'any',
    }
    mockQuery.mockImplementation(async (query: AttackAnalyticsQuery) => makeAnalyticsReport({
      ...echoQuery(query),
      rows: [{ key: row.values[0], label: 'Retired (legacy name)' }],
      columns: [{ key: column.values[0], label: 'PromptSendingAttack' }],
      cells: [{ row: row.values[0], column: column.values[0], statistics: ANALYTICS_TEST_STATISTICS, drilldown_filters: [row, column] }],
    }))
    renderPage({ ...DEFAULT_ANALYTICS_VIEW, chart: 'heatmap', heatmapRow: row.dimension, heatmapColumn: column.dimension })
    expect(await screen.findByRole('rowheader', { name: 'Retired (legacy name)' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Retired \(legacy name\) \/ PromptSendingAttack: 10 results/ }))
    await user.click(screen.getByRole('button', { name: 'Operation (ANY): Retired (legacy name)' }))
    const selected = screen.getByRole('group', { name: 'Selected filter values' })
    expect(within(selected).getByRole('checkbox', { name: 'Retired (legacy name)' })).toBeChecked()
    expect(mockList).not.toHaveBeenCalled()
  })

  it('should invalidate a stale filter editor when browser Back restores a different predicate', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Filter to Nightly' }))
    await user.click(screen.getByRole('button', { name: /^Operation \(ANY\): Nightly/ }))
    expect(screen.getByRole('region', { name: 'Filter editor' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Browser back' }))
    expect(screen.queryByRole('region', { name: 'Filter editor' })).not.toBeInTheDocument()
    expect(screen.getByText(/The edited filter changed/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Operation' }))
    expect(screen.queryByRole('group', { name: 'Selected filter values' })).not.toBeInTheDocument()
  })

  it('should browse every group with server offsets and not mistake a group page for a results page', async () => {
    const user = userEvent.setup()
    mockQuery.mockResolvedValue(makeAnalyticsReport({ has_more_groups: true, next_group_offset: 15 }))
    renderPage()
    await screen.findByRole('region', { name: 'Outcome breakdown by Operation' })
    expect(screen.queryByRole('button', { name: 'Show all groups' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show aggregate data' })).not.toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Saved attack results' })).toBeInTheDocument()
    expect(mockQuery).toHaveBeenLastCalledWith(expect.objectContaining({ group_limit: 15, group_offset: 0 }), expect.any(AbortSignal))
    await waitFor(() => { expect(screen.getByRole('button', { name: 'Next groups' })).toBeEnabled() })
    await user.click(screen.getByRole('button', { name: 'Next groups' }))
    await waitFor(() => { expect(mockQuery).toHaveBeenLastCalledWith(expect.objectContaining({ group_limit: 15, group_offset: 15 }), expect.any(AbortSignal)) })
    await waitFor(() => { expect(screen.getByRole('button', { name: 'First groups' })).toBeEnabled() })
    await user.click(screen.getByRole('button', { name: 'First groups' }))
    await waitFor(() => { expect(mockQuery).toHaveBeenLastCalledWith(expect.objectContaining({ group_limit: 15, group_offset: 0 }), expect.any(AbortSignal)) })
    expect(mockResults).not.toHaveBeenCalled()
  })

  it('should reset group pagination on Reload while keeping a failed refresh coherent', async () => {
    const user = userEvent.setup()
    mockQuery.mockResolvedValue(makeAnalyticsReport({ has_more_groups: true, next_group_offset: 15 }))
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Next groups' }))
    await waitFor(() => { expect(screen.getByRole('button', { name: 'First groups' })).toBeEnabled() })
    mockQuery.mockRejectedValueOnce(new Error('Reload deadline exceeded'))
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    await screen.findByText(/Reload failed.*data may be stale/)
    expect(mockQuery).toHaveBeenLastCalledWith(expect.objectContaining({ group_offset: 0 }), expect.any(AbortSignal))
    expect(screen.getByText('Inspect a saved response')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'First groups' })).toBeEnabled()
  })

  it('should discard an aborted results request even when the superseding group request fails', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const pending = deferred<ReturnType<typeof makeAnalyticsResults>>()
    mockQuery.mockResolvedValueOnce(makeAnalyticsReport({
      has_more_groups: true, next_group_offset: 15,
      results: makeAnalyticsResults({ has_more: true, next_cursor: 'page-2' }),
    })).mockRejectedValueOnce(new Error('Group page timed out'))
    mockResults.mockReturnValueOnce(pending.promise)
    renderPage()
    await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
    const results = screen.getByRole('region', { name: 'Matching AttackResults' })
    await user.click(within(results).getByRole('button', { name: 'Next' }))
    const signal = mockResults.mock.calls[0][1]
    await user.click(screen.getByRole('button', { name: 'Next groups' }))
    await act(async () => { await jest.advanceTimersByTimeAsync(ANALYTICS_DEBOUNCE_MS) })
    expect(signal?.aborted).toBe(true)
    expect(screen.getByText(/Reload failed.*data may be stale/)).toBeInTheDocument()
    expect(within(results).getByText('Page 1')).toBeInTheDocument()
    expect(within(results).queryByText('Loading results page')).not.toBeInTheDocument()
    expect(within(results).getByRole('button', { name: 'Next' })).toBeEnabled()
    await act(async () => {
      pending.resolve(makeAnalyticsResults({ items: [makeAnalyticsRow({ objective_preview: 'Obsolete page' })] }))
    })
    expect(screen.queryByText('Obsolete page')).not.toBeInTheDocument()
  })

  it.each(['error', 'undetermined'] as const)('should show unavailable ASR* for %s-only results', async (outcome: 'error' | 'undetermined') => {
    mockQuery.mockResolvedValue(makeAnalyticsReport({
      outcome_filter_applied: true,
      summary: { ...ANALYTICS_EMPTY_STATISTICS, total_results: 2, [outcome === 'error' ? 'errors' : 'undetermined']: 2 },
    }))
    renderPage({ ...DEFAULT_ANALYTICS_VIEW, filters: { dimensions: [], outcomes: [outcome] } })
    const summary = await screen.findByRole('region', { name: 'Outcome summary' })
    expect(within(summary).getByText('Unavailable*')).toBeInTheDocument()
    expect(within(summary).queryByText('0%')).not.toBeInTheDocument()
    expect(screen.getByRole('note', { name: 'Outcome-filtered ASR' })).toHaveTextContent(ANALYTICS_ASR_NOTE)
    expect(screen.getByRole('note', { name: 'Outcome-filtered ASR' })).toBeVisible()
    expect(within(summary).getByRole('button', { name: /ASR\*:.*Outcome filter active/ })).toBeInTheDocument()
  })

  it.each([false, true])('should distinguish empty storage from a filtered empty cohort (%s)', async (filtered: boolean) => {
    mockQuery.mockResolvedValue(makeAnalyticsReport({
      summary: ANALYTICS_EMPTY_STATISTICS, groups: [], results: makeAnalyticsResults({ items: [] }),
    }))
    renderPage(filtered ? { ...DEFAULT_ANALYTICS_VIEW, filters: { dimensions: [ANALYTICS_OPERATION_FILTER], outcomes: [] } } : undefined)
    expect(await screen.findByRole('heading', { name: filtered ? 'No results match these filters' : 'No saved AttackResults yet' })).toBeInTheDocument()
  })

  it('should render backend warnings as text and refuse malformed shared URLs', async () => {
    const warning = '<img src=x onerror="alert(1)"> SQLite reads may block writes.'
    mockQuery.mockResolvedValue(makeAnalyticsReport({ warnings: [warning] }))
    const { unmount } = renderPage()
    expect(await screen.findByText(warning)).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    unmount()
    mockQuery.mockClear()
    render(<TestWrapper><MemoryRouter initialEntries={['/analytics?analytics=not-json']}>
      <AnalyticsPage onOpenAttack={jest.fn()} />
    </MemoryRouter></TestWrapper>)
    expect(screen.getByText(/Cannot restore this analytics link/)).toBeInTheDocument()
    expect(mockQuery).not.toHaveBeenCalled()
  })
})
