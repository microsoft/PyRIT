import { act, renderHook, waitFor } from '@testing-library/react'

import { usePolledRecord } from './usePolledRecord'

interface Job {
  readonly status: 'running' | 'done'
  readonly step: number
}

const INTERVAL_MS = 2_000
const NOT_FOUND = { isAxiosError: true, response: { status: 404, data: { detail: 'Not found' } } }
const SERVER_ERROR = { isAxiosError: true, response: { status: 500, data: { detail: 'Server down' } } }

const load = jest.fn<Promise<Job>, [string, AbortSignal]>()
const isActive = (job: Job): boolean => job.status === 'running'

async function advance(milliseconds: number): Promise<void> {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(milliseconds)
  })
}

describe('usePolledRecord', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('polls while the record is active and stops when it is not', async () => {
    load
      .mockResolvedValueOnce({ status: 'running', step: 1 })
      .mockResolvedValueOnce({ status: 'running', step: 2 })
      .mockResolvedValueOnce({ status: 'done', step: 3 })
    const { result } = renderHook(() => usePolledRecord('job-1', load, isActive, INTERVAL_MS))

    expect(result.current.state.loadStatus).toBe('loading')
    await waitFor(() => expect(result.current.state.record?.step).toBe(1))
    expect(load).toHaveBeenCalledWith('job-1', expect.any(AbortSignal))
    await advance(INTERVAL_MS)
    await waitFor(() => expect(result.current.state.record?.step).toBe(2))
    await advance(INTERVAL_MS)
    await waitFor(() => expect(result.current.state.record?.step).toBe(3))
    await advance(INTERVAL_MS * 3)
    expect(load).toHaveBeenCalledTimes(3)
  })

  it('retries one failed refresh, then keeps the record and reports it is stale', async () => {
    load
      .mockResolvedValueOnce({ status: 'running', step: 1 })
      .mockRejectedValueOnce(SERVER_ERROR)
      .mockRejectedValueOnce(SERVER_ERROR)
      .mockResolvedValueOnce({ status: 'done', step: 2 })
    const { result } = renderHook(() => usePolledRecord('job-1', load, isActive, INTERVAL_MS))

    await waitFor(() => expect(result.current.state.record?.step).toBe(1))
    await advance(INTERVAL_MS)
    expect(result.current.state.stale).toBe(false)
    await advance(INTERVAL_MS)
    await waitFor(() => expect(result.current.state).toEqual({
      loadStatus: 'ready', record: { status: 'running', step: 1 }, error: 'Server down', stale: true,
    }))
    await advance(INTERVAL_MS)
    await waitFor(() => expect(result.current.state.record?.step).toBe(2))
    expect(result.current.state.stale).toBe(false)
  })

  it('reports a missing record and loads it again on retry', async () => {
    load.mockRejectedValueOnce(NOT_FOUND).mockResolvedValueOnce({ status: 'done', step: 1 })
    const { result } = renderHook(() => usePolledRecord('job-1', load, isActive, INTERVAL_MS))

    await waitFor(() => expect(result.current.state.loadStatus).toBe('not-found'))
    expect(result.current.state.error).toBe('Not found')
    act(() => { result.current.retry() })
    expect(result.current.state.loadStatus).toBe('loading')
    await waitFor(() => expect(result.current.state.record?.step).toBe(1))
  })

  it('reports an error when the first load keeps failing', async () => {
    load.mockRejectedValue(SERVER_ERROR)
    const { result } = renderHook(() => usePolledRecord('job-1', load, isActive, INTERVAL_MS))

    await advance(INTERVAL_MS)
    await waitFor(() => expect(result.current.state.loadStatus).toBe('error'))
    expect(result.current.state.record).toBeNull()
  })

  it('shows an applied record immediately and reloads to resume polling', async () => {
    load.mockResolvedValueOnce({ status: 'done', step: 1 }).mockResolvedValueOnce({ status: 'running', step: 3 })
    const { result } = renderHook(() => usePolledRecord('job-1', load, isActive, INTERVAL_MS))
    await waitFor(() => expect(result.current.state.record?.step).toBe(1))

    act(() => { result.current.applyRecord({ status: 'running', step: 2 }) })
    expect(result.current.state.record?.step).toBe(2)
    await waitFor(() => expect(result.current.state.record?.step).toBe(3))
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('does not load a blank key', () => {
    const { result } = renderHook(() => usePolledRecord(' ', load, isActive, INTERVAL_MS))

    expect(result.current.state.loadStatus).toBe('not-found')
    expect(load).not.toHaveBeenCalled()
  })
})
