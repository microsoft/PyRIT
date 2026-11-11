import { StrictMode, type ReactNode } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'

import { datasetsApi, scenariosApi } from '@/services/api'
import type { DatasetInfo, DatasetListResponse } from '@/types'

import { useScenarioDatasets } from './useScenarioDatasets'

jest.mock('@/services/api', () => ({
  scenariosApi: { listCompatibleDatasets: jest.fn(), loadDatasets: jest.fn() },
  datasetsApi: { listDatasets: jest.fn() },
}))
let generation = 'one'
let ready = true
jest.mock('./useRuntime', () => ({ useRuntime: () => ({ ready, generation }) }))
const list = jest.mocked(scenariosApi.listCompatibleDatasets)
const metadata = jest.mocked(datasetsApi.listDatasets)
const loadSelected = jest.mocked(scenariosApi.loadDatasets)
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
    list.mockReset().mockResolvedValue({ items: [NAMED] })
    metadata.mockReset().mockResolvedValue({ items: [NAMED] })
    loadSelected.mockReset().mockResolvedValue({
      items: [NAMED],
      settings: { sources: [], max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {} },
    })
  })

  it('loads eligible choices before any selection and only once in StrictMode', async () => {
    const view = renderHook(() => useScenarioDatasets('policy', true, true), {
      wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
    })
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    view.rerender()
    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith('policy', expect.any(AbortSignal))
  })

  it('does not reload for equivalent rerenders, but loads a new scenario or runtime', async () => {
    const view = renderHook(
      ({ scenarioName }: { scenarioName: string }) => useScenarioDatasets(scenarioName, true, true),
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
    const view = renderHook(() => useScenarioDatasets('policy', true, true))
    await waitFor(() => expect(view.result.current.items).toEqual([namedUnnamed]))
  })

  it('surfaces a load error and retries only on explicit action', async () => {
    list.mockRejectedValueOnce(new Error('Cannot load eligible datasets'))
    const view = renderHook(() => useScenarioDatasets('policy', true, true))
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
    const view = renderHook(() => useScenarioDatasets('policy', true, true))
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
    const view = renderHook(() => useScenarioDatasets('policy', true, true))
    expect(list).not.toHaveBeenCalled()
    expect(view.result.current.loading).toBe(false)
    ready = true
    list.mockReturnValueOnce(new Promise(() => {}))
    view.rerender()
    expect(list).toHaveBeenCalledTimes(1)
    view.unmount()
  })

  it('keeps generic metadata provider-inclusive and reuses it across preset scenario changes', async () => {
    const provider = {
      ...NAMED, name: 'provider', selection_key: 'dataset:named:provider',
      loaded: false, provider_available: true, logical_examples: null,
    }
    metadata.mockResolvedValue({ items: [NAMED, provider] })
    const view = renderHook(
      ({ name }: { name: string }) => useScenarioDatasets(name),
      { initialProps: { name: 'generic' } },
    )
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED, provider]))
    view.rerender({ name: 'foundry.assert_policy' })
    expect(metadata).toHaveBeenCalledTimes(1)
    expect(metadata).toHaveBeenCalledWith({ loaded_only: false }, expect.any(AbortSignal))
    expect(list).not.toHaveBeenCalled()
    expect(loadSelected).not.toHaveBeenCalled()
  })

  it('loads missing generic selections only on explicit action and then refreshes metadata', async () => {
    const view = renderHook(() => useScenarioDatasets('generic'))
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    expect(loadSelected).not.toHaveBeenCalled()
    const request = { datasets: { sources: [{ name: 'provider' }] } }
    await act(async () => { await view.result.current.loadSelected('generic', request) })
    expect(loadSelected).toHaveBeenCalledWith('generic', request)
    await waitFor(() => expect(metadata).toHaveBeenCalledTimes(2))
    expect(view.result.current.loadError).toBeNull()
    expect(list).not.toHaveBeenCalled()
  })

  it('surfaces explicit generic load errors and keeps refreshing current metadata', async () => {
    loadSelected.mockRejectedValueOnce(new Error('Provider load failed'))
    const view = renderHook(() => useScenarioDatasets('generic'))
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    await act(async () => { await view.result.current.loadSelected('generic', {}) })
    expect(view.result.current.loadError).toContain('Provider load failed')
    expect(view.result.current.preparing).toBe(false)
    await waitFor(() => expect(metadata).toHaveBeenCalledTimes(2))
  })

  it('retains the current generic rows while their metadata reloads', async () => {
    const view = renderHook(() => useScenarioDatasets('generic'))
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    let finish: (value: DatasetListResponse) => void = () => {}
    metadata.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    act(() => { view.result.current.reload() })
    expect(view.result.current.loading).toBe(true)
    expect(view.result.current.initialLoading).toBe(false)
    expect(view.result.current.items).toEqual([NAMED])
    await act(async () => { finish({ items: [] }) })
    expect(view.result.current.items).toEqual([])
  })

  it('deduplicates explicit generic loads and ignores completion from an old runtime', async () => {
    let finish: () => void = () => {}
    loadSelected.mockReturnValueOnce(new Promise((resolve) => {
      finish = () => resolve({
        items: [], settings: { sources: [], max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {} },
      })
    }))
    const view = renderHook(() => useScenarioDatasets('generic'))
    let pending: Promise<void>
    act(() => {
      pending = view.result.current.loadSelected('generic', {})
      void view.result.current.loadSelected('generic', {})
    })
    expect(loadSelected).toHaveBeenCalledTimes(1)
    generation = 'two'
    view.rerender()
    await act(async () => { finish(); await pending })
    expect(view.result.current.preparing).toBe(false)
    expect(view.result.current.loadError).toBeNull()
  })
})
