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

  it('refreshes one display url without clearing the open example or its stored value', async () => {
    const stored = 'https://account.blob.core.windows.net/container/pic.png'
    const expired = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=expired` }],
    } as SeedExampleDetailResponse
    const fresh = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=fresh` }],
    } as SeedExampleDetailResponse
    mockDetail.mockResolvedValueOnce(expired).mockResolvedValueOnce(fresh)
    const { result } = renderHook(() => useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, 'gen-1'))
    await waitFor(() => expect(result.current.detail?.members[0]?.value_url).toContain('sig=expired'))

    let next: string | null = null
    await act(async () => {
      next = await result.current.refreshDisplayUrls('image-1')
    })
    expect(next).toBe(`${stored}?sig=fresh`)
    expect(result.current.detail?.members[0]?.value).toBe(stored)
    expect(result.current.detail?.members[0]?.value_url).toBe(`${stored}?sig=fresh`)
    expect(result.current.loading).toBe(false)
    expect(mockDetail).toHaveBeenCalledTimes(2)
  })

  it('refreshes only the requested member when the response re-signs every blob', async () => {
    const image = 'https://account.blob.core.windows.net/container/pic.png'
    const audio = 'https://account.blob.core.windows.net/container/talk.wav'
    const opened = {
      ...DETAIL,
      members: [
        { id: 'image-1', value: image, value_url: `${image}?sig=old` },
        { id: 'audio-1', value: audio, value_url: `${audio}?sig=old` },
      ],
    } as SeedExampleDetailResponse
    const resigned = {
      ...DETAIL,
      members: [
        { id: 'image-1', value: image, value_url: `${image}?sig=new` },
        { id: 'audio-1', value: audio, value_url: `${audio}?sig=new` },
      ],
    } as SeedExampleDetailResponse
    mockDetail.mockResolvedValueOnce(opened).mockResolvedValueOnce(resigned)
    const { result } = renderHook(() => useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, 'gen-1'))
    await waitFor(() => expect(result.current.detail?.members).toHaveLength(2))

    await act(async () => {
      await result.current.refreshDisplayUrls('image-1')
    })
    expect(result.current.detail?.members[0]?.value_url).toBe(`${image}?sig=new`)
    expect(result.current.detail?.members[0]?.value).toBe(image)
    expect(result.current.detail?.members[1]?.value_url).toBe(`${audio}?sig=old`)
    expect(result.current.detail?.members[1]).toBe(opened.members[1])
  })

  it('keeps the open example when a display-url refresh fails', async () => {
    const stored = 'https://account.blob.core.windows.net/container/pic.png'
    const expired = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=expired` }],
    } as SeedExampleDetailResponse
    mockDetail.mockResolvedValueOnce(expired).mockRejectedValueOnce(new Error('unavailable'))
    const { result } = renderHook(() => useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, 'gen-1'))
    await waitFor(() => expect(result.current.detail).not.toBeNull())

    await act(async () => {
      await expect(result.current.refreshDisplayUrls('image-1')).rejects.toThrow('unavailable')
    })
    expect(result.current.detail?.members[0]?.value_url).toContain('sig=expired')
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('drops a refresh that resolves after the example is closed and reopened', async () => {
    const stored = 'https://account.blob.core.windows.net/container/pic.png'
    const opened = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=opened` }],
    } as SeedExampleDetailResponse
    const reopened = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=newly-opened` }],
    } as SeedExampleDetailResponse
    const stale = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=older-refresh` }],
    } as SeedExampleDetailResponse
    let resolveRefresh: (value: SeedExampleDetailResponse) => void = () => {}
    let refreshSignal: AbortSignal | undefined
    mockDetail
      .mockResolvedValueOnce(opened)
      .mockImplementationOnce((_exampleId: string, _selectionKey: string, signal: AbortSignal) => {
        refreshSignal = signal
        return new Promise((resolve) => { resolveRefresh = resolve })
      })
      .mockResolvedValueOnce(reopened)

    const { result, rerender } = renderHook(
      ({ exampleId }: { exampleId: string | null }) => (
        useSeedExampleDetail('dataset:named:harmbench', exampleId, false, 'gen-1')
      ),
      { initialProps: { exampleId: 'example-1' as string | null } },
    )
    await waitFor(() => expect(result.current.detail?.members[0]?.value_url).toContain('sig=opened'))
    const loadSignal = mockDetail.mock.calls[0]?.[2] as AbortSignal

    let refresh: Promise<string | null> = Promise.resolve(null)
    act(() => {
      refresh = result.current.refreshDisplayUrls('image-1')
    })
    const settled = refresh.then(
      (value) => value,
      (err: unknown) => err,
    )
    rerender({ exampleId: null })
    expect(loadSignal.aborted).toBe(true)
    expect(refreshSignal?.aborted).toBe(true)
    expect(result.current.detail).toBeNull()
    rerender({ exampleId: 'example-1' })
    await waitFor(() => expect(result.current.detail?.members[0]?.value_url).toBe(`${stored}?sig=newly-opened`))

    await act(async () => {
      resolveRefresh(stale)
    })
    await expect(settled).resolves.toMatchObject({ name: 'AbortError' })
    expect(result.current.detail?.members[0]?.value).toBe(stored)
    expect(result.current.detail?.members[0]?.value_url).toBe(`${stored}?sig=newly-opened`)
    expect(result.current.error).toBeNull()
  })

  it('drops a refresh that resolves after switching to another example', async () => {
    const stored = 'https://account.blob.core.windows.net/container/pic.png'
    const opened = {
      ...DETAIL,
      example_id: 'example-1',
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=first` }],
    } as SeedExampleDetailResponse
    const other = {
      ...DETAIL,
      example_id: 'example-2',
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=second` }],
    } as SeedExampleDetailResponse
    const stale = {
      ...DETAIL,
      example_id: 'example-1',
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=older-refresh` }],
    } as SeedExampleDetailResponse
    let resolveRefresh: (value: SeedExampleDetailResponse) => void = () => {}
    let refreshSignal: AbortSignal | undefined
    mockDetail
      .mockResolvedValueOnce(opened)
      .mockImplementationOnce((_exampleId: string, _selectionKey: string, signal: AbortSignal) => {
        refreshSignal = signal
        return new Promise((resolve) => { resolveRefresh = resolve })
      })
      .mockResolvedValueOnce(other)

    const { result, rerender } = renderHook(
      ({ exampleId }: { exampleId: string }) => (
        useSeedExampleDetail('dataset:named:harmbench', exampleId, false, 'gen-1')
      ),
      { initialProps: { exampleId: 'example-1' } },
    )
    await waitFor(() => expect(result.current.detail?.example_id).toBe('example-1'))
    const loadSignal = mockDetail.mock.calls[0]?.[2] as AbortSignal

    let refresh: Promise<string | null> = Promise.resolve(null)
    act(() => {
      refresh = result.current.refreshDisplayUrls('image-1')
    })
    const settled = refresh.then(
      (value) => value,
      (err: unknown) => err,
    )
    rerender({ exampleId: 'example-2' })
    expect(loadSignal.aborted).toBe(true)
    expect(refreshSignal?.aborted).toBe(true)
    await waitFor(() => expect(result.current.detail?.members[0]?.value_url).toBe(`${stored}?sig=second`))

    await act(async () => {
      resolveRefresh(stale)
    })
    await expect(settled).resolves.toMatchObject({ name: 'AbortError' })
    expect(result.current.detail?.example_id).toBe('example-2')
    expect(result.current.detail?.members[0]?.value_url).toBe(`${stored}?sig=second`)
    expect(result.current.error).toBeNull()
  })

  it('drops a refresh that resolves after the runtime generation changes', async () => {
    const stored = 'https://account.blob.core.windows.net/container/pic.png'
    const opened = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=opened` }],
    } as SeedExampleDetailResponse
    const nextRuntime = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=newly-opened` }],
    } as SeedExampleDetailResponse
    const stale = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=older-refresh` }],
    } as SeedExampleDetailResponse
    let resolveRefresh: (value: SeedExampleDetailResponse) => void = () => {}
    let refreshSignal: AbortSignal | undefined
    mockDetail
      .mockResolvedValueOnce(opened)
      .mockImplementationOnce((_exampleId: string, _selectionKey: string, signal: AbortSignal) => {
        refreshSignal = signal
        return new Promise((resolve) => { resolveRefresh = resolve })
      })
      .mockResolvedValueOnce(nextRuntime)

    const { result, rerender } = renderHook(
      ({ generation }: { generation: string }) => (
        useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, generation)
      ),
      { initialProps: { generation: 'gen-1' } },
    )
    await waitFor(() => expect(result.current.detail?.members[0]?.value_url).toContain('sig=opened'))
    const loadSignal = mockDetail.mock.calls[0]?.[2] as AbortSignal

    let refresh: Promise<string | null> = Promise.resolve(null)
    act(() => {
      refresh = result.current.refreshDisplayUrls('image-1')
    })
    const settled = refresh.then(
      (value) => value,
      (err: unknown) => err,
    )
    rerender({ generation: 'gen-2' })
    expect(loadSignal.aborted).toBe(true)
    expect(refreshSignal?.aborted).toBe(true)
    await waitFor(() => expect(result.current.detail?.members[0]?.value_url).toBe(`${stored}?sig=newly-opened`))

    await act(async () => {
      resolveRefresh(stale)
    })
    await expect(settled).resolves.toMatchObject({ name: 'AbortError' })
    expect(result.current.detail?.members[0]?.value_url).toBe(`${stored}?sig=newly-opened`)
    expect(result.current.error).toBeNull()
  })

  it('aborts an in-flight refresh when the detail hook unmounts', async () => {
    const stored = 'https://account.blob.core.windows.net/container/pic.png'
    const opened = {
      ...DETAIL,
      members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=opened` }],
    } as SeedExampleDetailResponse
    let resolveRefresh: (value: SeedExampleDetailResponse) => void = () => {}
    let refreshSignal: AbortSignal | undefined
    mockDetail
      .mockResolvedValueOnce(opened)
      .mockImplementationOnce((_exampleId: string, _selectionKey: string, signal: AbortSignal) => {
        refreshSignal = signal
        return new Promise((resolve) => { resolveRefresh = resolve })
      })

    const { result, unmount } = renderHook(() => (
      useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, 'gen-1')
    ))
    await waitFor(() => expect(result.current.detail).not.toBeNull())
    const loadSignal = mockDetail.mock.calls[0]?.[2] as AbortSignal

    let refresh: Promise<string | null> = Promise.resolve(null)
    act(() => {
      refresh = result.current.refreshDisplayUrls('image-1')
    })
    const settled = refresh.then(
      (value) => value,
      (err: unknown) => err,
    )
    unmount()
    expect(loadSignal.aborted).toBe(true)
    expect(refreshSignal?.aborted).toBe(true)

    await act(async () => {
      resolveRefresh({
        ...DETAIL,
        members: [{ id: 'image-1', value: stored, value_url: `${stored}?sig=older-refresh` }],
      } as SeedExampleDetailResponse)
    })
    await expect(settled).resolves.toMatchObject({ name: 'AbortError' })
  })

  it('does not abort another member when one member is retried', async () => {
    const image = 'https://account.blob.core.windows.net/container/pic.png'
    const audio = 'https://account.blob.core.windows.net/container/talk.wav'
    const opened = {
      ...DETAIL,
      members: [
        { id: 'image-1', value: image, value_url: `${image}?sig=old` },
        { id: 'audio-1', value: audio, value_url: `${audio}?sig=old` },
      ],
    } as SeedExampleDetailResponse
    const imageFresh = {
      ...opened,
      members: [
        { id: 'image-1', value: image, value_url: `${image}?sig=fresh` },
        { id: 'audio-1', value: audio, value_url: `${audio}?sig=other` },
      ],
    } as SeedExampleDetailResponse
    const audioFresh = {
      ...opened,
      members: [
        { id: 'image-1', value: image, value_url: `${image}?sig=ignored` },
        { id: 'audio-1', value: audio, value_url: `${audio}?sig=fresh` },
      ],
    } as SeedExampleDetailResponse
    let resolveImage: (value: SeedExampleDetailResponse) => void = () => {}
    let resolveAudio: (value: SeedExampleDetailResponse) => void = () => {}
    let imageSignal: AbortSignal | undefined
    let audioSignal: AbortSignal | undefined
    mockDetail
      .mockResolvedValueOnce(opened)
      .mockImplementationOnce((_exampleId: string, _selectionKey: string, signal: AbortSignal) => {
        imageSignal = signal
        return new Promise((resolve) => { resolveImage = resolve })
      })
      .mockImplementationOnce((_exampleId: string, _selectionKey: string, signal: AbortSignal) => {
        audioSignal = signal
        return new Promise((resolve) => { resolveAudio = resolve })
      })

    const { result } = renderHook(() => useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, 'gen-1'))
    await waitFor(() => expect(result.current.detail?.members).toHaveLength(2))

    let imageRefresh: Promise<string | null> = Promise.resolve(null)
    let audioRefresh: Promise<string | null> = Promise.resolve(null)
    act(() => {
      imageRefresh = result.current.refreshDisplayUrls('image-1')
      audioRefresh = result.current.refreshDisplayUrls('audio-1')
    })
    expect(imageSignal?.aborted).toBe(false)
    expect(audioSignal?.aborted).toBe(false)

    await act(async () => {
      resolveImage(imageFresh)
      await imageRefresh
    })
    expect(imageSignal?.aborted).toBe(false)
    expect(audioSignal?.aborted).toBe(false)
    expect(await imageRefresh).toBe(`${image}?sig=fresh`)
    expect(result.current.detail?.members[0]?.value_url).toBe(`${image}?sig=fresh`)
    expect(result.current.detail?.members[1]?.value_url).toBe(`${audio}?sig=old`)

    await act(async () => {
      resolveAudio(audioFresh)
      await audioRefresh
    })
    expect(audioSignal?.aborted).toBe(false)
    expect(await audioRefresh).toBe(`${audio}?sig=fresh`)
    expect(result.current.detail?.members[0]?.value_url).toBe(`${image}?sig=fresh`)
    expect(result.current.detail?.members[1]?.value_url).toBe(`${audio}?sig=fresh`)
  })

  it('does not request a detail when the example id is repeated or absent', async () => {
    renderHook(() => useSeedExampleDetail('dataset:named:harmbench', null, true, 'gen-1'))
    await act(async () => { await Promise.resolve() })
    expect(mockDetail).not.toHaveBeenCalled()
  })
})
