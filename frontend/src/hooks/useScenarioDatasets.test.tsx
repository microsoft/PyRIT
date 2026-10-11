import { act, renderHook, waitFor } from '@testing-library/react'

import { datasetsApi } from '@/services/api'
import type { DatasetInfo } from '@/types'

import { useScenarioDatasets } from './useScenarioDatasets'

jest.mock('@/services/api', () => ({ datasetsApi: { list: jest.fn() } }))
let generation = 'one'
jest.mock('./useRuntime', () => ({ useRuntime: () => ({ ready: true, generation }) }))
const list = datasetsApi.list as jest.Mock
const NAMED: DatasetInfo = {
  name: 'named', selection_key: 'dataset:named:named', loaded: true, provider_available: false, logical_examples: 1, origins: ['local'],
}

describe('useScenarioDatasets', () => {
  beforeEach(() => { jest.clearAllMocks(); generation = 'one' })

  it('caches metadata for the page, excludes unnamed populations, and explicitly reloads', async () => {
    list.mockResolvedValue([NAMED, { ...NAMED, name: '(unnamed)', selection_key: 'dataset:unnamed' }])
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
    list.mockReturnValueOnce(new Promise<DatasetInfo[]>((resolve) => { completeOld = resolve }))
    list.mockResolvedValueOnce([{ ...NAMED, name: 'new' }])
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
    list.mockResolvedValueOnce([NAMED])
    const view = renderHook(() => useScenarioDatasets())
    await waitFor(() => expect(view.result.current.items).toEqual([NAMED]))
    list.mockReturnValueOnce(new Promise(() => {}))
    generation = 'two'
    view.rerender()
    expect(view.result.current.items).toEqual([])
    expect(view.result.current.loading).toBe(true)
    view.unmount()
  })
})
