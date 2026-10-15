import { useCallback, useEffect, useState } from 'react'

import type { SeedBrowserQuery } from '@/components/Datasets/seedExampleQuery'
import { datasetsApi } from '@/services/api'
import { toApiError, type ApiError } from '@/services/errors'
import type { SeedExampleDetailResponse, SeedExampleListResponse } from '@/types'

interface SeedExamplePageState {
  readonly page: SeedExampleListResponse | null
  readonly loading: boolean
  readonly error: ApiError | null
  readonly retry: () => void
}

interface SeedExampleDetailState {
  readonly detail: SeedExampleDetailResponse | null
  readonly loading: boolean
  readonly error: ApiError | null
  readonly notFound: boolean
  readonly retry: () => void
}

function listRequestKey(selectionKey: string, query: SeedBrowserQuery, generation: string): string {
  return [
    generation,
    selectionKey,
    query.search,
    query.modalities.join('\0'),
    query.seedTypes.join('\0'),
    query.harmCategories.join('\0'),
    String(query.limit),
    query.cursor ?? '',
    query.cursorRepeated ? 'repeated' : '',
  ].join('\n')
}

/**
 * Loads one page of logical examples. A missing key, a repeated cursor, or a
 * stale response is not shown. Runtime changes drop the previous page.
 */
export function useSeedExamples(
  selectionKey: string,
  query: SeedBrowserQuery,
  generation: string,
): SeedExamplePageState {
  const requestKey = listRequestKey(selectionKey, query, generation)
  const [activeRequest, setActiveRequest] = useState(requestKey)
  const [page, setPage] = useState<SeedExampleListResponse | null>(null)
  const [loading, setLoading] = useState(selectionKey !== '' && !query.cursorRepeated)
  const [error, setError] = useState<ApiError | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)

  if (activeRequest !== requestKey) {
    setActiveRequest(requestKey)
    setPage(null)
    setError(null)
    setLoading(selectionKey !== '' && !query.cursorRepeated)
  }

  useEffect(() => {
    if (!selectionKey || query.cursorRepeated) return
    const controller = new AbortController()
    const load = async (): Promise<void> => {
      try {
        const response = await datasetsApi.listSeedExamples({
          selection_key: selectionKey,
          limit: query.limit,
          cursor: query.cursor ?? undefined,
          search: query.search || undefined,
          modality: query.modalities,
          seed_type: query.seedTypes,
          harm_category: query.harmCategories,
        }, controller.signal)
        if (controller.signal.aborted) return
        setPage(response)
        setError(null)
      } catch (err: unknown) {
        if (controller.signal.aborted) return
        setPage(null)
        setError(toApiError(err))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => {
      controller.abort()
    }
  }, [requestKey, refetchCount, selectionKey, query, generation])

  const retry = useCallback(() => {
    setLoading(selectionKey !== '' && !query.cursorRepeated)
    setError(null)
    setRefetchCount((count) => count + 1)
  }, [query.cursorRepeated, selectionKey])

  return { page, loading, error, retry }
}

function detailRequestKey(selectionKey: string, exampleId: string | null, generation: string): string {
  return `${generation}\n${selectionKey}\n${exampleId ?? ''}`
}

/**
 * Loads stored members for one example in the selected dataset.
 * Detail from another example, dataset, or runtime is not reused.
 */
export function useSeedExampleDetail(
  selectionKey: string,
  exampleId: string | null,
  exampleRepeated: boolean,
  generation: string,
): SeedExampleDetailState {
  const requestKey = detailRequestKey(selectionKey, exampleId, generation)
  const [activeRequest, setActiveRequest] = useState(requestKey)
  const [detail, setDetail] = useState<SeedExampleDetailResponse | null>(null)
  const [loading, setLoading] = useState(exampleId != null && !exampleRepeated && selectionKey !== '')
  const [error, setError] = useState<ApiError | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [refetchCount, setRefetchCount] = useState(0)

  if (activeRequest !== requestKey) {
    setActiveRequest(requestKey)
    setDetail(null)
    setError(null)
    setNotFound(false)
    setLoading(exampleId != null && !exampleRepeated && selectionKey !== '')
  }

  useEffect(() => {
    if (!selectionKey || !exampleId || exampleRepeated) return
    const controller = new AbortController()
    const load = async (): Promise<void> => {
      try {
        const response = await datasetsApi.getSeedExample(exampleId, selectionKey, controller.signal)
        if (controller.signal.aborted) return
        setDetail(response)
        setError(null)
        setNotFound(false)
      } catch (err: unknown) {
        if (controller.signal.aborted) return
        const apiError = toApiError(err)
        setDetail(null)
        if (apiError.status === 404) {
          setNotFound(true)
          setError(null)
        } else {
          setNotFound(false)
          setError(apiError)
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => {
      controller.abort()
    }
  }, [requestKey, refetchCount, selectionKey, exampleId, exampleRepeated, generation])

  const retry = useCallback(() => {
    setLoading(exampleId != null && !exampleRepeated && selectionKey !== '')
    setError(null)
    setNotFound(false)
    setRefetchCount((count) => count + 1)
  }, [exampleId, exampleRepeated, selectionKey])

  return { detail, loading, error, notFound, retry }
}
