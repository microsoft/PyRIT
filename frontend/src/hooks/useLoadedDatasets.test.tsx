import { act, renderHook, waitFor } from '@testing-library/react'

import { datasetsApi } from '@/services/api'
import type { DatasetInfo } from '@/types'

import { useLoadedDatasets } from './useLoadedDatasets'

let generation = 'gen-1'

jest.mock('@/hooks/useRuntime', () => ({
  useRuntime: () => ({ ready: true, state: 'ready', generation }),
}))

jest.mock('@/services/api', () => ({
  datasetsApi: {
    listDatasets: jest.fn(),
  },
}))

const mockListDatasets = datasetsApi.listDatasets as jest.Mock

const FIRST: DatasetInfo = {
  name: 'harmbench',
  selection_key: 'dataset:named:harmbench',
  loaded: true,
  provider_available: false,
  logical_examples: 2,
  seed_pieces: 2,
  objectives: 2,
  modalities: ['text'],
  harm_categories: ['hate'],
  has_unlabeled_harm_categories: false,
}

const SECOND: DatasetInfo = {
  ...FIRST,
  name: 'other',
  selection_key: 'dataset:named:other',
}

describe('useLoadedDatasets', () => {
  beforeEach(() => {
    generation = 'gen-1'
    jest.clearAllMocks()
  })

  it('loads the catalog and replaces it when the runtime generation changes', async () => {
    mockListDatasets
      .mockResolvedValueOnce({ items: [FIRST] })
      .mockResolvedValueOnce({ items: [SECOND] })

    const { result, rerender } = renderHook(() => useLoadedDatasets())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.datasets).toEqual([FIRST])
    expect(result.current.error).toBeNull()
    expect(mockListDatasets).toHaveBeenCalledTimes(1)
    expect(mockListDatasets.mock.calls[0][0]).toEqual({ loaded_only: true })
    const firstSignal = mockListDatasets.mock.calls[0][1] as AbortSignal
    expect(firstSignal.aborted).toBe(false)

    generation = 'gen-2'
    rerender()

    expect(result.current.loading).toBe(true)
    expect(result.current.datasets).toEqual([])
    expect(firstSignal.aborted).toBe(true)

    await waitFor(() => expect(result.current.datasets).toEqual([SECOND]))
    expect(result.current.loading).toBe(false)
    expect(mockListDatasets).toHaveBeenCalledTimes(2)
    const secondSignal = mockListDatasets.mock.calls[1][1] as AbortSignal
    expect(secondSignal).not.toBe(firstSignal)
    expect(secondSignal.aborted).toBe(false)
    expect(mockListDatasets.mock.calls[1][0]).toEqual({ loaded_only: true })
  })

  it('aborts an in-flight request when the runtime generation changes without showing the cancellation', async () => {
    mockListDatasets.mockImplementation((_query: unknown, signal: AbortSignal) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => {
        reject(new Error('canceled'))
      })
    }))

    const { result, rerender } = renderHook(() => useLoadedDatasets())
    await waitFor(() => expect(mockListDatasets).toHaveBeenCalledTimes(1))
    const firstSignal = mockListDatasets.mock.calls[0][1] as AbortSignal

    mockListDatasets.mockReturnValueOnce(new Promise(() => {}))
    generation = 'gen-2'
    rerender()

    expect(firstSignal.aborted).toBe(true)
    expect(result.current.loading).toBe(true)
    expect(result.current.datasets).toEqual([])
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.error).toBeNull()
    await waitFor(() => expect(mockListDatasets).toHaveBeenCalledTimes(2))
    expect((mockListDatasets.mock.calls[1][1] as AbortSignal).aborted).toBe(false)
  })

  it('aborts the catalog request when the subscriber unmounts', async () => {
    let signal: AbortSignal | undefined
    mockListDatasets.mockImplementation((_query: unknown, nextSignal: AbortSignal) => new Promise((_resolve, reject) => {
      signal = nextSignal
      nextSignal.addEventListener('abort', () => {
        reject(new Error('canceled'))
      })
    }))

    const { unmount } = renderHook(() => useLoadedDatasets())
    await waitFor(() => expect(signal).toBeDefined())
    unmount()

    expect(signal?.aborted).toBe(true)
    await act(async () => {
      await Promise.resolve()
    })
  })
})
