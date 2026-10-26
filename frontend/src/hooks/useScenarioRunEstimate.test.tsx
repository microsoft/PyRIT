import { act, renderHook } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import type { ScenarioRunSizeEstimateRequest, ScenarioRunSizeEstimateResponse } from '@/types'

import { ESTIMATE_DEBOUNCE_MS, useScenarioRunEstimate } from './useScenarioRunEstimate'

jest.mock('@/services/api', () => ({ scenariosApi: { estimateRun: jest.fn() } }))
const estimateRun = jest.mocked(scenariosApi.estimateRun)

function response(total: number, maxTotal: number | 'all' = 'all'): ScenarioRunSizeEstimateResponse {
  return {
    dataset_size: { kind: 'bounded', value: total },
    dataset_limit: { state: 'scenario_default' },
    estimated_attack_count: total,
    components: [{ label: 'Attacks', count: total, is_baseline: false, note: null }],
    datasets: [],
    note: null,
    effective_datasets: { sources: [], max_per_dataset: 'all', max_total: maxTotal, fetch: 'if_missing', filters: {} },
  } as unknown as ScenarioRunSizeEstimateResponse
}

interface Props {
  request: ScenarioRunSizeEstimateRequest | null
  ready?: boolean
  paused?: boolean
  invalidReason?: string | null
}

function renderEstimate(initial: Props) {
  return renderHook(
    ({ request, ready = true, paused = false, invalidReason = null }: Props) => useScenarioRunEstimate({
      scenarioName: 'scenario',
      request,
      requestKey: request === null ? null : JSON.stringify(request),
      invalidReason,
      ready,
      paused,
    }),
    { initialProps: initial },
  )
}

async function settle(milliseconds = ESTIMATE_DEBOUNCE_MS): Promise<void> {
  await act(async () => {
    jest.advanceTimersByTime(milliseconds)
    await Promise.resolve()
    await Promise.resolve()
  })
}

function deferred<T>() {
  let resolve: (value: T) => void = () => {}
  let reject: (error: unknown) => void = () => {}
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

describe('useScenarioRunEstimate', () => {
  beforeEach(() => {
    jest.useFakeTimers()
    estimateRun.mockReset()
  })
  afterEach(() => jest.useRealTimers())

  it('sends only the last request after rapid changes', async () => {
    estimateRun.mockResolvedValue(response(5))
    const view = renderEstimate({ request: { techniques: ['a'] } })
    view.rerender({ request: { techniques: ['a', 'b'] } })
    view.rerender({ request: { techniques: ['b'] } })

    await settle(ESTIMATE_DEBOUNCE_MS - 1)
    expect(estimateRun).not.toHaveBeenCalled()
    await settle(1)

    expect(estimateRun).toHaveBeenCalledTimes(1)
    expect(estimateRun.mock.calls[0][1]).toEqual({ techniques: ['b'] })
    expect(view.result.current.state.status).toBe('available')
  })

  it('drops an older answer that arrives after a newer request', async () => {
    const slow = deferred<ScenarioRunSizeEstimateResponse>()
    estimateRun.mockReturnValueOnce(slow.promise).mockResolvedValueOnce(response(9))
    const view = renderEstimate({ request: { techniques: ['a'] } })
    await settle()
    view.rerender({ request: { techniques: ['b'] } })
    await settle()

    await act(async () => { slow.resolve(response(1)) })

    expect(view.result.current.current?.status).toBe('resolved')
    expect(view.result.current.state.status === 'available' && view.result.current.state.estimate.total).toBe(9)
  })

  it('keeps the last good estimate as stale when a refresh fails', async () => {
    estimateRun.mockResolvedValueOnce(response(4)).mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: { detail: 'down' } } })
    const view = renderEstimate({ request: { techniques: ['a'] } })
    await settle()
    view.rerender({ request: { techniques: ['b'] } })

    expect(view.result.current.state.status).toBe('refreshing')
    await settle()

    const state = view.result.current.state
    expect(state.status).toBe('stale')
    expect(state.status === 'stale' && state.estimate.total).toBe(4)
  })

  it('refreshes allowed datasets without the rejected edits and keeps the edited settings', async () => {
    estimateRun
      .mockResolvedValueOnce(response(4, 7))
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 422, data: { detail: 'bad source' } } })
      .mockResolvedValueOnce(response(4, 'all'))
    const view = renderEstimate({ request: { techniques: ['a'] } })
    await settle()
    view.rerender({ request: { techniques: ['a'], datasets: { sources: [{ name: 'nope' }] } } })
    await settle()

    expect(estimateRun).toHaveBeenCalledTimes(3)
    expect(estimateRun.mock.calls[2][1]).toEqual({ techniques: ['a'] })
    expect(view.result.current.datasetProjection?.effective_datasets?.max_total).toBe(7)
  })

  it('reports why no request could be built instead of calling the backend', async () => {
    const view = renderEstimate({ request: null, invalidReason: 'Select a target.' })
    await settle()

    expect(estimateRun).not.toHaveBeenCalled()
    expect(view.result.current.state).toMatchObject({ status: 'unavailable', note: 'Select a target.' })
  })

  it.each([
    ['not ready', { ready: false }],
    ['paused', { paused: true }],
  ])('waits while %s', async (_, props) => {
    renderEstimate({ request: { techniques: ['a'] }, ...props })
    await settle()

    expect(estimateRun).not.toHaveBeenCalled()
  })

  it('cancels the pending request on unmount', async () => {
    const pending = deferred<ScenarioRunSizeEstimateResponse>()
    estimateRun.mockReturnValueOnce(pending.promise)
    const view = renderEstimate({ request: { techniques: ['a'] } })
    await settle()
    const signal = estimateRun.mock.calls[0][2] as AbortSignal

    view.unmount()

    expect(signal.aborted).toBe(true)
    await act(async () => { pending.resolve(response(2)) })
  })
})
