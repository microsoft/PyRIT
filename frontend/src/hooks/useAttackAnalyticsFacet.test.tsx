import { act, renderHook } from '@testing-library/react'

import { analyticsApi } from '@/services/api'
import { makeAnalyticsFacets } from '@/test-utils/analyticsFixtures'
import { DEFAULT_ANALYTICS_VIEW } from '@/utils/attackAnalytics'

import { useAttackAnalyticsFacet } from './useAttackAnalyticsFacet'
import { useRuntime } from './useRuntime'

jest.mock('@/services/api', () => ({ analyticsApi: { facets: jest.fn() } }))
jest.mock('./useRuntime', () => ({ useRuntime: jest.fn() }))
const mockFacets = jest.mocked(analyticsApi.facets)
const mockRuntime = jest.mocked(useRuntime)

async function advance(milliseconds = 250): Promise<void> {
  await act(async () => { await jest.advanceTimersByTimeAsync(milliseconds) })
}

function renderFacet() {
  return renderHook(() => useAttackAnalyticsFacet({ name: 'operation' }, DEFAULT_ANALYTICS_VIEW.filters, 0))
}

describe('useAttackAnalyticsFacet', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'one' })
    mockFacets.mockReset().mockResolvedValue(makeAnalyticsFacets({ has_more: true, next_offset: 50 }))
  })
  afterEach(() => { jest.useRealTimers() })

  it('should start at the first facet page after a search round trip', async () => {
    const { result } = renderFacet()
    await advance()
    act(() => { result.current.nextPage() })
    await advance()
    expect(result.current.offset).toBe(50)
    act(() => { result.current.setSearch('different') })
    await advance()
    act(() => { result.current.setSearch('') })
    await advance()
    expect(result.current.offset).toBe(0)
    expect(mockFacets).toHaveBeenLastCalledWith(expect.objectContaining({ search: '', offset: 0 }), expect.any(AbortSignal))
  })

  it('should wait for runtime readiness and invalidate pages when memory changes', async () => {
    mockRuntime.mockReturnValue({ ready: false, state: 'connecting', generation: '' })
    const { result, rerender } = renderFacet()
    await advance()
    expect(mockFacets).not.toHaveBeenCalled()
    expect(result.current.ready).toBe(false)
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'one' })
    rerender()
    await advance()
    act(() => { result.current.nextPage() })
    await advance()
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'two' })
    rerender()
    expect(result.current.items).toEqual([])
    await advance()
    expect(result.current.offset).toBe(0)
  })

  it('should honor a busy facet response cooldown before Retry', async () => {
    mockFacets.mockRejectedValueOnce({
      isAxiosError: true, response: { status: 503, headers: { 'retry-after': '1' }, data: { detail: 'Busy facet' } },
    })
    const { result } = renderFacet()
    await advance()
    act(() => { result.current.retry() })
    await advance(999)
    expect(mockFacets).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(mockFacets).toHaveBeenCalledTimes(2)
  })
})
