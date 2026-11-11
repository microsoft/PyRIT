import { StrictMode, type ReactNode } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import type { DatasetInfo, DatasetListResponse } from '@/types'

import { useScenarioDatasets } from './useScenarioDatasets'

jest.mock('@/services/api', () => ({
  scenariosApi: { listCompatibleDatasets: jest.fn() },
}))
let generation = 'one'
let ready = true
jest.mock('./useRuntime', () => ({ useRuntime: () => ({ ready, generation }) }))
const list = jest.mocked(scenariosApi.listCompatibleDatasets)
const NAMED: DatasetInfo = {
  name: 'named', selection_key: 'dataset:named:named', loaded: true, provider_available: false,
  logical_examples: 1, origins: ['local'], seed_pieces: 1, objectives: 1, modalities: ['text'],
  harm_categories: [], has_unlabeled_harm_categories: false,
}

describe('useScenarioDatasets', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    generation = 'one'
    ready = true
    list.mockResolvedValue({ items: [NAMED] })
  })

  it('loads eligible choices before any selection and only once in StrictMode', async () => {
    const view = renderHook(() => useScenarioDatasets('policy'), {
      wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
    })
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    view.rerender()
    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith('policy', expect.any(AbortSignal))
  })

  it('does not reload for equivalent rerenders, but loads a new scenario or runtime', async () => {
    const view = renderHook(
      ({ scenarioName }: { scenarioName: string }) => useScenarioDatasets(scenarioName),
      { initialProps: { scenarioName: 'one' } },
    )
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    view.rerender({ scenarioName: 'one' })
    expect(list).toHaveBeenCalledTimes(1)
    view.rerender({ scenarioName: 'two' })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    generation = 'two'
    view.rerender({ scenarioName: 'two' })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(3))
  })

  it('excludes the unnamed aggregate without confusing a named Unnamed dataset', async () => {
    const namedUnnamed = { ...NAMED, name: 'Unnamed', selection_key: 'dataset:named:Unnamed' }
    list.mockResolvedValue({ items: [namedUnnamed, { ...NAMED, selection_key: 'dataset:unnamed' }] })
    const view = renderHook(() => useScenarioDatasets('policy'))
    await waitFor(() => expect(view.result.current.items).toEqual([namedUnnamed]))
  })

  it('surfaces a load error and retries only on explicit action', async () => {
    list.mockRejectedValueOnce(new Error('Cannot load eligible datasets'))
    const view = renderHook(() => useScenarioDatasets('policy'))
    await waitFor(() => expect(view.result.current.error).toContain('Cannot load eligible datasets'))
    view.rerender()
    expect(list).toHaveBeenCalledTimes(1)
    act(() => { view.result.current.retry() })
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    expect(view.result.current.error).toBeNull()
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('ignores a stale response and clears choices immediately on runtime changes', async () => {
    let finish: (value: DatasetListResponse) => void = () => {}
    list.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    const view = renderHook(() => useScenarioDatasets('policy'))
    generation = 'two'
    view.rerender()
    expect(view.result.current.items).toEqual([])
    expect(view.result.current.loading).toBe(true)
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    await act(async () => { finish({ items: [{ ...NAMED, name: 'stale' }] }) })
    expect(view.result.current.items).toEqual([NAMED])
    expect(list.mock.calls[0]?.[1]?.aborted).toBe(true)
  })

  it('waits for the runtime and does not update an unmounted consumer', async () => {
    ready = false
    const view = renderHook(() => useScenarioDatasets('policy'))
    expect(list).not.toHaveBeenCalled()
    expect(view.result.current.loading).toBe(false)
    ready = true
    list.mockReturnValueOnce(new Promise(() => {}))
    view.rerender()
    expect(list).toHaveBeenCalledTimes(1)
    view.unmount()
  })
})
