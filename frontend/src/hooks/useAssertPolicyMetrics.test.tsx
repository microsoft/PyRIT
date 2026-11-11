import { act, renderHook, waitFor } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import type { AssertPolicyMetrics } from '@/types'

import { useAssertPolicyMetrics } from './useAssertPolicyMetrics'

jest.mock('@/services/api', () => ({ scenariosApi: { getAssertPolicyMetrics: jest.fn() } }))
let runtime = { ready: true, generation: 'one' }
jest.mock('./useRuntime', () => ({ useRuntime: () => runtime }))
const getMetrics = jest.mocked(scenariosApi.getAssertPolicyMetrics)

const METRICS = { scenario_result_id: 'run', techniques: {} } as unknown as AssertPolicyMetrics

describe('useAssertPolicyMetrics', () => {
  beforeEach(() => {
    getMetrics.mockReset()
    runtime = { ready: true, generation: 'one' }
  })

  it('loads metrics for the run', async () => {
    getMetrics.mockResolvedValue(METRICS)
    const view = renderHook(() => useAssertPolicyMetrics('run'))

    expect(view.result.current.status).toBe('loading')
    await waitFor(() => expect(view.result.current.status).toBe('success'))
    expect(view.result.current.metrics).toBe(METRICS)
    expect(getMetrics).toHaveBeenCalledWith('run', expect.any(AbortSignal))
  })

  it('reports an error and retries', async () => {
    getMetrics
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { detail: 'Run not yet completed' } } })
      .mockResolvedValueOnce(METRICS)
    const view = renderHook(() => useAssertPolicyMetrics('run'))
    await waitFor(() => expect(view.result.current.error).toBe('Run not yet completed'))
    expect(view.result.current.status).toBe('error')

    act(() => view.result.current.retry())
    expect(view.result.current.status).toBe('loading')
    await waitFor(() => expect(view.result.current.status).toBe('success'))
    expect(getMetrics).toHaveBeenCalledTimes(2)
  })

  it('waits for the runtime and ignores a request from an older runtime', async () => {
    runtime = { ready: false, generation: 'one' }
    let finishOld: () => void = () => { throw new Error('The old request has not started') }
    const refreshed = { ...METRICS, scenario_result_id: 'refreshed' }
    getMetrics
      .mockReturnValueOnce(new Promise<AssertPolicyMetrics>((resolve) => { finishOld = () => resolve(METRICS) }))
      .mockResolvedValueOnce(refreshed)
    const view = renderHook(() => useAssertPolicyMetrics('run'))
    expect(getMetrics).not.toHaveBeenCalled()

    runtime = { ready: true, generation: 'one' }
    view.rerender()
    runtime = { ready: true, generation: 'two' }
    view.rerender()
    await waitFor(() => expect(view.result.current.metrics).toBe(refreshed))

    await act(async () => { finishOld() })
    expect(view.result.current.metrics).toBe(refreshed)
  })
})
