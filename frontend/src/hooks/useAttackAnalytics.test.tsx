import { act, renderHook } from '@testing-library/react'

import { analyticsApi } from '@/services/api'
import { makeAnalyticsReport, makeAnalyticsResults, makeAnalyticsRow } from '@/test-utils/analyticsFixtures'
import type { AttackAnalyticsQuery, AttackAnalyticsReport } from '@/types'
import { ANALYTICS_DEBOUNCE_MS, DEFAULT_ANALYTICS_VIEW, analyticsQuery } from '@/utils/attackAnalytics'

import { useAttackAnalytics } from './useAttackAnalytics'
import { useRuntime } from './useRuntime'

jest.mock('@/services/api', () => ({ analyticsApi: { query: jest.fn(), results: jest.fn() } }))
jest.mock('./useRuntime', () => ({ useRuntime: jest.fn() }))
const mockQuery = jest.mocked(analyticsApi.query)
const mockResults = jest.mocked(analyticsApi.results)
const mockRuntime = jest.mocked(useRuntime)

const QUERY = analyticsQuery(DEFAULT_ANALYTICS_VIEW)
const BUSY = { isAxiosError: true, response: { status: 503, data: { detail: 'Capacity busy' }, headers: { 'retry-after': '1' } } }

async function advance(milliseconds = ANALYTICS_DEBOUNCE_MS): Promise<void> {
  await act(async () => { await jest.advanceTimersByTimeAsync(milliseconds) })
}

function renderAnalytics() {
  return renderHook(({ query }: { query: AttackAnalyticsQuery | null }) => useAttackAnalytics(query), {
    initialProps: { query: QUERY },
  })
}

describe('useAttackAnalytics', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'one' })
    mockQuery.mockReset().mockResolvedValue(makeAnalyticsReport({
      results: makeAnalyticsResults({ has_more: true, next_cursor: 'next' }),
    }))
    mockResults.mockReset().mockResolvedValue(makeAnalyticsResults())
  })

  afterEach(() => { jest.useRealTimers() })

  it('should stop reads during runtime replacement and never revive old results after recovery', async () => {
    let complete: ((report: AttackAnalyticsReport) => void) | undefined
    mockQuery.mockReturnValueOnce(new Promise<AttackAnalyticsReport>((resolve: (report: AttackAnalyticsReport) => void) => {
      complete = resolve
    }))
    const { result, rerender } = renderAnalytics()
    await advance()
    const oldSignal = mockQuery.mock.calls[0][1]
    mockRuntime.mockReturnValue({ ready: false, state: 'draining', generation: '' })
    rerender({ query: QUERY })
    await advance()
    expect(oldSignal?.aborted).toBe(true)
    expect(result.current.loading).toBe(false)
    expect(result.current.report).toBeNull()
    expect(mockQuery).toHaveBeenCalledTimes(1)

    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'two' })
    mockQuery.mockResolvedValue(makeAnalyticsReport({
      results: makeAnalyticsResults({ items: [makeAnalyticsRow({ objective_preview: 'New runtime result' })] }),
    }))
    rerender({ query: QUERY })
    await advance()
    await act(async () => { complete?.(makeAnalyticsReport()) })
    expect(result.current.results?.items[0].objective_preview).toBe('New runtime result')
    expect(mockQuery).toHaveBeenCalledTimes(2)
  })

  it('should honor Retry-After before a manual report retry and never poll the dashboard', async () => {
    mockQuery.mockRejectedValueOnce(BUSY)
    const { result } = renderAnalytics()
    await advance()
    expect(result.current.error).toBe('Capacity busy')
    act(() => { result.current.reload() })
    await advance(999)
    expect(mockQuery).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(mockQuery).toHaveBeenCalledTimes(2)
    await advance(300_000)
    expect(mockQuery).toHaveBeenCalledTimes(2)
    expect(mockResults).not.toHaveBeenCalled()
  })

  it('should cancel a results cooldown on Reload without sending the abandoned page', async () => {
    const { result } = renderAnalytics()
    await advance()
    mockResults.mockRejectedValueOnce(BUSY)
    await act(async () => { result.current.nextPage() })
    expect(result.current.resultsError).toBe('Capacity busy')
    act(() => { result.current.retryResults() })
    act(() => { result.current.reload() })
    await advance(2000)
    expect(mockQuery).toHaveBeenCalledTimes(2)
    expect(mockResults).toHaveBeenCalledTimes(1)
    expect(result.current.resultsLoading).toBe(false)
    expect(result.current.page).toBe(0)
  })

  it('should recover an invalid cursor with a rows-only first page', async () => {
    const { result } = renderAnalytics()
    await advance()
    const refreshed = result.current.report?.computed_at
    mockResults.mockRejectedValueOnce({
      isAxiosError: true, response: { status: 400, data: { detail: 'Invalid analytics cursor' } },
    })
    await act(async () => { result.current.nextPage() })
    expect(result.current.resultsCursorInvalid).toBe(true)
    await act(async () => { result.current.retryResults() })
    expect(mockResults).toHaveBeenLastCalledWith({
      filters: makeAnalyticsReport().filters, cursor: null, limit: 25,
    }, expect.any(AbortSignal))
    expect(mockQuery).toHaveBeenCalledTimes(1)
    expect(result.current.report?.computed_at).toBe(refreshed)
  })

  it('should preserve a result page for semantically equivalent filters and default dimension fields', async () => {
    const { result, rerender } = renderAnalytics()
    await advance()
    await act(async () => { result.current.nextPage() })
    expect(result.current.page).toBe(1)
    rerender({ query: {
      ...QUERY, group_by: { name: 'operation', label_key: null, converter_direction: 'request' },
      filters: { dimensions: [], outcomes: ['undetermined', 'error', 'failure', 'success'] },
    } })
    await advance()
    expect(result.current.page).toBe(1)
    expect(mockQuery).toHaveBeenCalledTimes(1)
    expect(mockResults).toHaveBeenCalledTimes(1)
  })
})
