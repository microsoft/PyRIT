import { act, renderHook, waitFor } from '@testing-library/react'

import { datasetsApi, scenariosApi } from '@/services/api'
import type { DatasetInfo } from '@/types'

import { useScenarioDatasets } from './useScenarioDatasets'

jest.mock('@/services/api', () => ({
  datasetsApi: { listDatasets: jest.fn() },
  scenariosApi: { loadDatasets: jest.fn() },
}))
let generation = 'one'
jest.mock('./useRuntime', () => ({ useRuntime: () => ({ ready: true, generation }) }))
const list = datasetsApi.listDatasets as jest.Mock
const loadDatasets = scenariosApi.loadDatasets as jest.Mock
const NAMED: DatasetInfo = {
  name: 'named', selection_key: 'dataset:named:named', loaded: true, provider_available: false, logical_examples: 1, origins: ['local'], seed_pieces: 1, objectives: 1, modalities: ['text'], harm_categories: [], has_unlabeled_harm_categories: false,
}

describe('useScenarioDatasets', () => {
  beforeEach(() => { jest.clearAllMocks(); generation = 'one' })

  it('caches metadata for the page, excludes unnamed populations, and explicitly reloads', async () => {
    list.mockResolvedValue({ items: [NAMED, { ...NAMED, name: '(unnamed)', selection_key: 'dataset:unnamed' }] })
    const view = renderHook(() => useScenarioDatasets())
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    view.rerender()
    expect(list).toHaveBeenCalledTimes(1)
    act(() => { view.result.current.reload() })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    generation = 'two'
    view.rerender()
    await waitFor(() => expect(list).toHaveBeenCalledTimes(3))
  })

  it('ignores old-generation responses and surfaces reload failures without generating datasets', async () => {
    let completeOld: (value: DatasetInfo[]) => void = () => {}
    list.mockReturnValueOnce(new Promise<DatasetInfo[]>((resolve) => { completeOld = resolve }).then((items) => ({ items })))
    list.mockResolvedValueOnce({ items: [{ ...NAMED, name: 'new' }] })
    const view = renderHook(() => useScenarioDatasets())
    generation = 'two'
    view.rerender()
    await waitFor(() => expect(view.result.current.items[0]?.name).toBe('new'))
    await act(async () => { completeOld([NAMED]) })
    expect(view.result.current.items[0]?.name).toBe('new')
    list.mockRejectedValueOnce(new Error('offline'))
    act(() => { view.result.current.reload() })
    await waitFor(() => expect(view.result.current.error).not.toBeNull())
  })

  it('hides loaded metadata immediately when the runtime changes', async () => {
    list.mockResolvedValueOnce({ items: [NAMED] })
    const view = renderHook(() => useScenarioDatasets())
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    list.mockReturnValueOnce(new Promise(() => {}))
    generation = 'two'
    view.rerender()
    expect(view.result.current.items).toEqual([])
    expect(view.result.current.loading).toBe(true)
    view.unmount()
  })

  it('loads only on explicit action and refreshes metadata after success', async () => {
    list.mockResolvedValue({ items: [NAMED] })
    loadDatasets.mockResolvedValue({})
    const view = renderHook(() => useScenarioDatasets())
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    expect(loadDatasets).not.toHaveBeenCalled()
    const request = { datasets: { max_total: 4 } }
    await act(async () => { await view.result.current.loadSelected('test', request) })
    expect(loadDatasets).toHaveBeenCalledWith('test', request)
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(view.result.current.loadError).toBeNull()
  })

  it('retains load failures and still refreshes current metadata', async () => {
    list.mockResolvedValue({ items: [NAMED] })
    loadDatasets.mockRejectedValue(new Error('Load failed'))
    const view = renderHook(() => useScenarioDatasets())
    await act(async () => { await view.result.current.loadSelected('test', {}) })
    await waitFor(() => expect(view.result.current.loadError).not.toBeNull())
    expect(view.result.current.preparing).toBe(false)
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('deduplicates load clicks and ignores completion from a previous generation', async () => {
    list.mockResolvedValue({ items: [NAMED] })
    let finish: () => void = () => {}
    loadDatasets.mockReturnValue(new Promise<void>((resolve) => { finish = resolve }))
    const view = renderHook(() => useScenarioDatasets())
    let pending: Promise<void>
    act(() => {
      pending = view.result.current.loadSelected('test', {})
      void view.result.current.loadSelected('test', {})
    })
    expect(loadDatasets).toHaveBeenCalledTimes(1)
    generation = 'two'
    view.rerender()
    await act(async () => { finish(); await pending })
    expect(view.result.current.preparing).toBe(false)
    expect(view.result.current.loadError).toBeNull()
  })
})
