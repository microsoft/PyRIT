import { act, renderHook, waitFor } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import type { ScenarioQueueSnapshot } from '@/types'

import { SCENARIO_QUEUE_POLL_INTERVAL_MS, useScenarioQueue } from './useScenarioQueue'

jest.mock('@/services/api', () => ({
  scenariosApi: {
    getQueue: jest.fn(),
  },
}))

const mockGetQueue = scenariosApi.getQueue as jest.Mock
const FIRST_SNAPSHOT: ScenarioQueueSnapshot = {
  revision: 1,
  snapshot_at: '2026-01-01T00:00:00Z',
  active: null,
  queued: [],
}
const SECOND_SNAPSHOT: ScenarioQueueSnapshot = {
  ...FIRST_SNAPSHOT,
  revision: 2,
  queued: [{
    scenario_result_id: 'run-2',
    scenario_name: 'QueuedScenario',
    scenario_registry_name: 'queued.scenario',
    state: 'QUEUED',
    position: 1,
    created_at: '2026-01-01T00:00:01Z',
    enqueued_at: '2026-01-01T00:00:01Z',
  }],
}

describe('useScenarioQueue', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('polls and applies position changes', async () => {
    mockGetQueue
      .mockResolvedValueOnce(FIRST_SNAPSHOT)
      .mockResolvedValueOnce(SECOND_SNAPSHOT)

    const { result, unmount } = renderHook(() => useScenarioQueue())
    await waitFor(() => expect(result.current.snapshot?.revision).toBe(1))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_QUEUE_POLL_INTERVAL_MS)
    })

    expect(result.current.snapshot?.queued[0].position).toBe(1)
    unmount()
  })

  it('keeps the last good snapshot across a transient failure', async () => {
    mockGetQueue
      .mockResolvedValueOnce(FIRST_SNAPSHOT)
      .mockRejectedValueOnce(new Error('temporary queue failure'))

    const { result, unmount } = renderHook(() => useScenarioQueue())
    await waitFor(() => expect(result.current.snapshot?.revision).toBe(1))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_QUEUE_POLL_INTERVAL_MS)
    })

    expect(result.current.snapshot).toEqual(FIRST_SNAPSHOT)
    expect(result.current.stale).toBe(true)
    expect(result.current.error).toBe('temporary queue failure')
    unmount()
  })

  it('retries an initial failure without presenting stale queue data', async () => {
    mockGetQueue
      .mockRejectedValueOnce(new Error('queue unavailable'))
      .mockResolvedValueOnce(FIRST_SNAPSHOT)

    const { result, unmount } = renderHook(() => useScenarioQueue())
    await waitFor(() => expect(result.current.error).toBe('queue unavailable'))

    expect(result.current.snapshot).toBeNull()
    expect(result.current.loading).toBe(false)
    expect(result.current.stale).toBe(false)

    act(() => result.current.retry())
    expect(result.current.loading).toBe(true)
    expect(result.current.error).toBeNull()
    await waitFor(() => expect(result.current.snapshot).toEqual(FIRST_SNAPSHOT))
    unmount()
  })

  it('keeps existing queue data visible while a manual retry is pending', async () => {
    let resolveRetry: ((snapshot: ScenarioQueueSnapshot) => void) | undefined
    mockGetQueue
      .mockResolvedValueOnce(FIRST_SNAPSHOT)
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveRetry = resolve
      }))

    const { result, unmount } = renderHook(() => useScenarioQueue())
    await waitFor(() => expect(result.current.snapshot).toEqual(FIRST_SNAPSHOT))

    act(() => result.current.retry())

    expect(result.current.loading).toBe(false)
    expect(result.current.snapshot).toEqual(FIRST_SNAPSHOT)
    await act(async () => {
      resolveRetry?.(SECOND_SNAPSHOT)
    })
    unmount()
  })

  it('ignores a request that resolves after unmount', async () => {
    let resolveRequest: ((snapshot: ScenarioQueueSnapshot) => void) | undefined
    mockGetQueue.mockImplementationOnce(() => new Promise((resolve) => {
      resolveRequest = resolve
    }))

    const { result, unmount } = renderHook(() => useScenarioQueue())
    await waitFor(() => expect(mockGetQueue).toHaveBeenCalledTimes(1))
    unmount()

    await act(async () => {
      resolveRequest?.(FIRST_SNAPSHOT)
    })
    expect(result.current.snapshot).toBeNull()
  })

  it('ignores a request that rejects after unmount', async () => {
    let rejectRequest: ((reason?: unknown) => void) | undefined
    mockGetQueue.mockImplementationOnce(() => new Promise((_resolve, reject) => {
      rejectRequest = reject
    }))

    const { result, unmount } = renderHook(() => useScenarioQueue())
    await waitFor(() => expect(mockGetQueue).toHaveBeenCalledTimes(1))
    unmount()

    await act(async () => {
      rejectRequest?.(new Error('late failure'))
    })
    expect(result.current.error).toBeNull()
  })
})
