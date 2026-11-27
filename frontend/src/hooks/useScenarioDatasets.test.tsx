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

  it('loads only when asked and refreshes metadata after success', async () => {
    list.mockResolvedValue({ items: [NAMED] })
    loadDatasets.mockResolvedValue({})
    const view = renderHook(() => useScenarioDatasets('test'))
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    expect(loadDatasets).not.toHaveBeenCalled()
    const request = { datasets: { max_total: 4 } }
    await act(async () => { await view.result.current.loadSelected('test', request, ['named']) })
    expect(loadDatasets).toHaveBeenCalledWith('test', request)
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(view.result.current.loadingNames).toEqual([])
    expect(view.result.current.loadErrors).toEqual({})
  })

  it('keeps each failure on its dataset names, clears it when they load again, and refreshes metadata', async () => {
    list.mockResolvedValue({ items: [NAMED] })
    loadDatasets.mockRejectedValueOnce(new Error('Load failed'))
    const view = renderHook(() => useScenarioDatasets('test'))
    await act(async () => { await view.result.current.loadSelected('test', {}, ['a', 'b']) })
    expect(view.result.current.loadErrors).toEqual({ a: 'Load failed', b: 'Load failed' })
    expect(view.result.current.loadingNames).toEqual([])
    expect(list).toHaveBeenCalledTimes(2)
    let finish: () => void = () => {}
    loadDatasets.mockReturnValueOnce(new Promise<void>((resolve) => { finish = resolve }))
    let retry: Promise<void> = Promise.resolve()
    act(() => { retry = view.result.current.loadSelected('test', {}, ['a']) })
    expect(view.result.current.loadErrors).toEqual({ b: 'Load failed' })
    expect(view.result.current.loadingNames).toEqual(['a'])
    await act(async () => { finish(); await retry })
    expect(view.result.current.loadErrors).toEqual({ b: 'Load failed' })
  })

  it('starts a request for every call, tracks each one, and ignores completion from a previous generation', async () => {
    list.mockResolvedValue({ items: [NAMED] })
    const finishers: Array<() => void> = []
    const held = (): Promise<void> => new Promise<void>((resolve) => { finishers.push(resolve) })
    loadDatasets.mockImplementationOnce(held).mockImplementationOnce(held)
    const view = renderHook(() => useScenarioDatasets('test'))
    let first: Promise<void> = Promise.resolve()
    let second: Promise<void> = Promise.resolve()
    act(() => { first = view.result.current.loadSelected('test', {}, ['a']) })
    act(() => { second = view.result.current.loadSelected('test', {}, ['b']) })
    expect(loadDatasets).toHaveBeenCalledTimes(2)
    expect(view.result.current.loadingNames).toEqual(['a', 'b'])
    await act(async () => { finishers[0]?.(); await first })
    expect(view.result.current.loadingNames).toEqual(['b'])
    generation = 'two'
    view.rerender()
    expect(view.result.current.loadingNames).toEqual([])
    await act(async () => { finishers[1]?.(); await second })
    expect(view.result.current.loadingNames).toEqual([])
    expect(view.result.current.loadErrors).toEqual({})
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

  it('lists nothing until enabled, and aborts an in-flight listing when the scenario changes', async () => {
    list.mockReturnValue(new Promise(() => {}))
    const view = renderHook(({ name, enabled }: { name: string, enabled: boolean }) => useScenarioDatasets(name, enabled), {
      initialProps: { name: 'first', enabled: false },
    })
    expect(list).not.toHaveBeenCalled()
    expect(view.result.current.loading).toBe(false)
    view.rerender({ name: 'first', enabled: true })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1))
    expect(view.result.current.loading).toBe(true)
    const firstSignal = list.mock.calls[0][1] as AbortSignal
    expect(firstSignal.aborted).toBe(false)
    view.rerender({ name: 'second', enabled: true })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(firstSignal.aborted).toBe(true)
    view.rerender({ name: 'second', enabled: false })
    expect((list.mock.calls[1][1] as AbortSignal).aborted).toBe(true)
    expect(view.result.current.loading).toBe(false)
    view.unmount()
  })
})
