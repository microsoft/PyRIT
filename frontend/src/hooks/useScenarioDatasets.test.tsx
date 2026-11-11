import { StrictMode, type ReactNode } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { DatasetInfo, DatasetListResponse } from '@/types'

import { useScenarioDatasets } from './useScenarioDatasets'

jest.mock('@/services/api', () => ({
  scenariosApi: { listCompatibleDatasets: jest.fn(), loadDatasets: jest.fn() },
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
    list.mockReset().mockResolvedValue({ items: [NAMED] })
  })

  it('uses shared compatible listing once in StrictMode and never prepares catalog choices', async () => {
    const view = renderHook(() => useScenarioDatasets('generic'), {
      wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
    })
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    view.rerender()
    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith('generic', expect.any(AbortSignal))
    expect(scenariosApi.loadDatasets).not.toHaveBeenCalled()
  })

  it('loads a new scenario or runtime without using old eligibility', async () => {
    const view = renderHook(
      ({ name }: { name: string }) => useScenarioDatasets(name),
      { initialProps: { name: 'one' } },
    )
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    view.rerender({ name: 'two' })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    generation = 'two'
    view.rerender({ name: 'two' })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(3))
  })

  it('keeps named and unnamed namespaces distinct', async () => {
    const namedUnnamed = { ...NAMED, name: 'Unnamed', selection_key: 'dataset:named:Unnamed' }
    list.mockResolvedValue({ items: [namedUnnamed, { ...NAMED, selection_key: 'dataset:unnamed' }] })
    const view = renderHook(() => useScenarioDatasets('policy'))
    await waitFor(() => expect(view.result.current.items).toEqual([namedUnnamed]))
  })

  it('surfaces listing failures and retries explicitly', async () => {
    list.mockRejectedValueOnce(new Error('Cannot list compatible datasets'))
    const view = renderHook(() => useScenarioDatasets('policy'))
    await waitFor(() => expect(view.result.current.error).toContain('Cannot list compatible datasets'))
    view.rerender()
    expect(list).toHaveBeenCalledTimes(1)
    act(() => { view.result.current.retry() })
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    expect(view.result.current.error).toBeNull()
  })

  it('retains the current rows during refresh and ignores old-runtime responses', async () => {
    const view = renderHook(() => useScenarioDatasets('generic'))
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    const old = deferred<DatasetListResponse>()
    list.mockReturnValueOnce(old.promise)
    act(() => { view.result.current.reload() })
    expect(view.result.current.loading).toBe(true)
    expect(view.result.current.items).toEqual([NAMED])
    generation = 'two'
    view.rerender()
    await waitFor(() => expect(view.result.current.loading).toBe(false))
    await act(async () => { old.resolve({ items: [{ ...NAMED, name: 'stale' }] }) })
    expect(view.result.current.items).toEqual([NAMED])
  })

  it('waits for a ready runtime and a named scenario', () => {
    ready = false
    const view = renderHook(() => useScenarioDatasets(''))
    expect(list).not.toHaveBeenCalled()
    view.unmount()
  })
})
