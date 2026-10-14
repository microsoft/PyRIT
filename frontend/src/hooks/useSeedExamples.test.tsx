import { act, renderHook, waitFor } from '@testing-library/react'

import type { SeedBrowserQuery } from '@/components/Datasets/seedExampleQuery'
import { datasetsApi } from '@/services/api'
import type { SeedExampleDetailResponse, SeedExampleListResponse, SeedExampleSummary } from '@/types'

import { useSeedExampleDetail, useSeedExamples } from './useSeedExamples'

jest.mock('@/services/api', () => ({
  datasetsApi: {
    listSeedExamples: jest.fn(),
    getSeedExample: jest.fn(),
  },
}))

const mockList = datasetsApi.listSeedExamples as jest.Mock
const mockDetail = datasetsApi.getSeedExample as jest.Mock

const QUERY: SeedBrowserQuery = {
  search: '',
  modalities: [],
  seedTypes: [],
  harmCategories: [],
  limit: 20,
  cursor: null,
  exampleId: null,
  cursorRepeated: false,
  exampleRepeated: false,
}

function summary(exampleId: string, preview: string): SeedExampleSummary {
  return {
    example_id: exampleId,
    name: null,
    preview,
    preview_truncated: false,
    modalities: ['text'],
    seed_types: ['prompt'],
    piece_count: 1,
    objective_count: 0,
    harm_categories: [],
    has_unlabeled_harm: false,
  }
}

function page(items: SeedExampleSummary[], total = items.length): SeedExampleListResponse {
  return {
    items,
    pagination: { limit: 20, has_more: false, next_cursor: null, prev_cursor: 'echo' },
    total,
  }
}

const DETAIL: SeedExampleDetailResponse = {
  ...summary('example-1', 'preview'),
  members: [],
}

describe('useSeedExamples', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('does not request seeds for an empty selection key or a repeated cursor', async () => {
    renderHook(() => useSeedExamples('', QUERY, 'gen-1'))
    renderHook(() => useSeedExamples('dataset:named:harmbench', { ...QUERY, cursorRepeated: true }, 'gen-1'))
    await act(async () => { await Promise.resolve() })
    expect(mockList).not.toHaveBeenCalled()
  })

  it('ignores a late response from the previous search', async () => {
    let resolveFirst: (value: SeedExampleListResponse) => void = () => {}
    mockList.mockImplementation((params: { search?: string }) => {
      if (!params.search) return new Promise((resolve) => { resolveFirst = resolve })
      return Promise.resolve(page([summary('example-2', 'second')]))
    })

    const { result, rerender } = renderHook(
      ({ query }: { query: SeedBrowserQuery }) => useSeedExamples('dataset:named:harmbench', query, 'gen-1'),
      { initialProps: { query: QUERY } },
    )
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(1))

    rerender({ query: { ...QUERY, search: 'policy' } })
    await waitFor(() => expect(result.current.page?.items[0]?.example_id).toBe('example-2'))

    await act(async () => { resolveFirst(page([summary('example-1', 'first')])) })
    expect(result.current.page?.items.map((item) => item.example_id)).toEqual(['example-2'])
  })

  it('drops the previous page when the runtime generation changes', async () => {
    mockList
      .mockResolvedValueOnce(page([summary('example-1', 'first')]))
      .mockResolvedValueOnce(page([summary('example-2', 'second')]))

    const { result, rerender } = renderHook(
      ({ generation }: { generation: string }) => useSeedExamples('dataset:named:harmbench', QUERY, generation),
      { initialProps: { generation: 'gen-1' } },
    )
    await waitFor(() => expect(result.current.page?.items[0]?.example_id).toBe('example-1'))

    rerender({ generation: 'gen-2' })
    expect(result.current.page).toBeNull()
    await waitFor(() => expect(result.current.page?.items[0]?.example_id).toBe('example-2'))
    expect(mockList).toHaveBeenCalledTimes(2)
  })

  it('keeps a cursor error on the same cursor when retried', async () => {
    const cursorError = {
      isAxiosError: true,
      response: { status: 400, data: { detail: 'The cursor is not valid for this dataset and these filters' } },
    }
    mockList.mockRejectedValue(cursorError)
    const query = { ...QUERY, cursor: 'stale-cursor' }
    const { result } = renderHook(() => useSeedExamples('dataset:named:harmbench', query, 'gen-1'))

    await waitFor(() => expect(result.current.error?.status).toBe(400))
    expect(result.current.page).toBeNull()
    result.current.retry()
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2))
    expect(mockList.mock.calls[1][0].cursor).toBe('stale-cursor')
  })
})

describe('useSeedExampleDetail', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('loads members with both the selection key and the example id', async () => {
    mockDetail.mockResolvedValue(DETAIL)
    const { result } = renderHook(() => useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, 'gen-1'))

    await waitFor(() => expect(result.current.detail).toEqual(DETAIL))
    expect(mockDetail).toHaveBeenCalledWith('example-1', 'dataset:named:harmbench', expect.any(AbortSignal))
  })

  it('reports a deleted example separately from other failures and clears it on runtime change', async () => {
    mockDetail
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 404, data: { detail: 'missing' } } })
      .mockResolvedValueOnce({ ...DETAIL, example_id: 'example-2' })

    const { result, rerender } = renderHook(
      ({ exampleId, generation }: { exampleId: string; generation: string }) => (
        useSeedExampleDetail('dataset:named:harmbench', exampleId, false, generation)
      ),
      { initialProps: { exampleId: 'example-1', generation: 'gen-1' } },
    )
    await waitFor(() => expect(result.current.notFound).toBe(true))
    expect(result.current.error).toBeNull()

    rerender({ exampleId: 'example-1', generation: 'gen-2' })
    expect(result.current.detail).toBeNull()
    expect(result.current.notFound).toBe(false)
    await waitFor(() => expect(result.current.detail?.example_id).toBe('example-2'))
  })

  it('reports other detail failures and retries the same example', async () => {
    mockDetail
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: { detail: 'unavailable' } } })
      .mockResolvedValueOnce(DETAIL)
    const { result } = renderHook(() => useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, 'gen-1'))

    await waitFor(() => expect(result.current.error?.status).toBe(500))
    expect(result.current.notFound).toBe(false)
    expect(result.current.detail).toBeNull()

    result.current.retry()
    await waitFor(() => expect(result.current.detail).toEqual(DETAIL))
    expect(result.current.error).toBeNull()
    expect(mockDetail).toHaveBeenCalledTimes(2)
  })

  it('does not request a detail when the example id is repeated or absent', async () => {
    renderHook(() => useSeedExampleDetail('dataset:named:harmbench', null, true, 'gen-1'))
    await act(async () => { await Promise.resolve() })
    expect(mockDetail).not.toHaveBeenCalled()
  })
})
