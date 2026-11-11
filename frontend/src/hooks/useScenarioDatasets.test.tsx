import { StrictMode, type ReactNode } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'

import { datasetsApi, scenariosApi } from '@/services/api'
import type { DatasetInfo } from '@/types'

import { useScenarioDatasets } from './useScenarioDatasets'

jest.mock('@/services/api', () => ({
  datasetsApi: { listDatasets: jest.fn() },
  scenariosApi: { loadDatasets: jest.fn(), listCompatibleDatasets: jest.fn() },
}))
let generation = 'one'
jest.mock('./useRuntime', () => ({ useRuntime: () => ({ ready: true, generation }) }))
const list = scenariosApi.listCompatibleDatasets as jest.Mock
const loadDatasets = scenariosApi.loadDatasets as jest.Mock
const NAMED: DatasetInfo = {
  name: 'named', selection_key: 'dataset:named:named', loaded: true, provider_available: false, logical_examples: 1, origins: ['local'], seed_pieces: 1, objectives: 1, modalities: ['text'], harm_categories: [], has_unlabeled_harm_categories: false,
}

describe('useScenarioDatasets', () => {
  beforeEach(() => { jest.clearAllMocks(); generation = 'one' })

  it('caches metadata for the page, excludes unnamed populations, and explicitly reloads', async () => {
    list.mockResolvedValue({ items: [NAMED, { ...NAMED, name: '(unnamed)', selection_key: 'dataset:unnamed' }] })
    const view = renderHook(() => useScenarioDatasets('test'))
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
    const view = renderHook(() => useScenarioDatasets('test'))
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
    const view = renderHook(() => useScenarioDatasets('test'))
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    list.mockReturnValueOnce(new Promise(() => {}))
    generation = 'two'
    view.rerender()
    expect(view.result.current.items).toEqual([])
    expect(view.result.current.loading).toBe(true)
    view.unmount()
  })

  it('never prepares providers while reading or refreshing metadata', async () => {
    list.mockResolvedValue({ items: [NAMED] })
    loadDatasets.mockResolvedValue({})
    const view = renderHook(() => useScenarioDatasets('test'))
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    expect(loadDatasets).not.toHaveBeenCalled()
    act(() => { view.result.current.reload() })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(loadDatasets).not.toHaveBeenCalled()
  })

  it('uses one read-only compatible listing in StrictMode and scopes cached choices to scenario', async () => {
    list.mockResolvedValue({ items: [NAMED] })
    const view = renderHook(({ name }: { name: string }) => useScenarioDatasets(name), {
      initialProps: { name: 'first' },
      wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
    })
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith('first', expect.any(AbortSignal))
    expect(datasetsApi.listDatasets).not.toHaveBeenCalled()
    expect(loadDatasets).not.toHaveBeenCalled()
    view.rerender({ name: 'second' })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(list).toHaveBeenLastCalledWith('second', expect.any(AbortSignal))
  })
})
