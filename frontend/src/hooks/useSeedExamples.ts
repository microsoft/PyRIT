import { useCallback, useEffect, useRef, useState } from 'react'

import type { SeedBrowserQuery } from '@/components/Datasets/seedExampleQuery'
import { datasetsApi } from '@/services/api'
import { toApiError, type ApiError } from '@/services/errors'
import type { SeedExampleDetailResponse, SeedExampleListResponse } from '@/types'

import { useAutoRefresh } from './useAutoRefresh'

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
  /** Refetch one member's display URL. Other members, and any newer example, keep their URLs. */
  readonly refreshDisplayUrls: (memberId: string) => Promise<string | null>
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
  const [refresh, setRefresh] = useState({ version: 0, quiet: false })
  const pending = useRef(false)

  if (activeRequest !== requestKey) {
    setActiveRequest(requestKey)
    setPage(null)
    setError(null)
    setLoading(selectionKey !== '' && !query.cursorRepeated)
  }

  useEffect(() => {
    if (!selectionKey || query.cursorRepeated) return
    const controller = new AbortController()
    pending.current = true
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
        const apiError = toApiError(err)
        if (!refresh.quiet || (apiError.status != null && apiError.status < 500)) setPage(null)
        setError(apiError)
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
          pending.current = false
        }
      }
    }
    void load()
    return () => {
      controller.abort()
      pending.current = false
    }
  }, [requestKey, refresh, selectionKey, query, generation])

  const retry = useCallback(() => {
    setLoading(selectionKey !== '' && !query.cursorRepeated)
    setError(null)
    setRefresh((current) => ({ version: current.version + 1, quiet: false }))
  }, [query.cursorRepeated, selectionKey])
  useAutoRefresh(() => {
    if (!pending.current) setRefresh((current) => ({ version: current.version + 1, quiet: true }))
  }, selectionKey !== '' && !query.cursorRepeated && !loading)

  return { page, loading, error, retry }
}

function detailRequestKey(selectionKey: string, exampleId: string | null, generation: string): string {
  return `${generation}\n${selectionKey}\n${exampleId ?? ''}`
}

function refreshAborted(): DOMException {
  return new DOMException('The example changed before the link refreshed.', 'AbortError')
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
  const [refresh, setRefresh] = useState({ version: 0, quiet: false })
  const pending = useRef(false)
  const lifecycleRef = useRef<AbortController | null>(null)
  const lifecycleKeyRef = useRef(requestKey)
  const memberRefreshRef = useRef(new Map<string, AbortController>())

  if (activeRequest !== requestKey) {
    setActiveRequest(requestKey)
    setDetail(null)
    setError(null)
    setNotFound(false)
    setLoading(exampleId != null && !exampleRepeated && selectionKey !== '')
  }

  useEffect(() => {
    const controller = new AbortController()
    lifecycleKeyRef.current = requestKey
    lifecycleRef.current = controller
    const members = memberRefreshRef.current
    const stopMembers = (): void => {
      for (const child of members.values()) child.abort()
      members.clear()
    }
    controller.signal.addEventListener('abort', stopMembers)
    const load = async (): Promise<void> => {
      if (!selectionKey || !exampleId || exampleRepeated) return
      pending.current = true
      try {
        const response = await datasetsApi.getSeedExample(exampleId, selectionKey, controller.signal)
        if (controller.signal.aborted) return
        setDetail(response)
        setError(null)
        setNotFound(false)
      } catch (err: unknown) {
        if (controller.signal.aborted) return
        const apiError = toApiError(err)
        if (!refresh.quiet || (apiError.status != null && apiError.status < 500)) setDetail(null)
        if (apiError.status === 404) {
          setNotFound(true)
          setError(null)
        } else {
          setNotFound(false)
          setError(apiError)
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
          pending.current = false
        }
      }
    }
    void load()
    return () => {
      controller.abort()
      pending.current = false
      controller.signal.removeEventListener('abort', stopMembers)
      if (lifecycleRef.current === controller) lifecycleRef.current = null
    }
  }, [requestKey, refresh, selectionKey, exampleId, exampleRepeated, generation])

  const retry = useCallback(() => {
    setLoading(exampleId != null && !exampleRepeated && selectionKey !== '')
    setError(null)
    setNotFound(false)
    setRefresh((current) => ({ version: current.version + 1, quiet: false }))
  }, [exampleId, exampleRepeated, selectionKey])
  useAutoRefresh(() => {
    if (!pending.current) setRefresh((current) => ({ version: current.version + 1, quiet: true }))
  }, selectionKey !== '' && exampleId != null && !exampleRepeated && !loading)

  const refreshDisplayUrls = useCallback(async (memberId: string): Promise<string | null> => {
    if (!selectionKey || !exampleId || exampleRepeated) {
      throw new Error('A dataset selection key is required to refresh a seed example.')
    }
    const lifecycle = lifecycleRef.current
    if (lifecycle == null || lifecycle.signal.aborted) throw refreshAborted()
    const requestedKey = requestKey
    const requestedExampleId = exampleId
    memberRefreshRef.current.get(memberId)?.abort()
    const controller = new AbortController()
    memberRefreshRef.current.set(memberId, controller)
    const followLifecycle = (): void => { controller.abort() }
    lifecycle.signal.addEventListener('abort', followLifecycle)
    const stillCurrent = (): boolean => (
      lifecycleRef.current === lifecycle
      && lifecycleKeyRef.current === requestedKey
      && !lifecycle.signal.aborted
      && !controller.signal.aborted
    )
    try {
      const response = await datasetsApi.getSeedExample(exampleId, selectionKey, controller.signal)
      if (!stillCurrent() || response.example_id !== requestedExampleId) throw refreshAborted()
      const refreshed = response.members.find((item) => item.id === memberId)
      if (refreshed == null) return null
      if (!stillCurrent()) throw refreshAborted()
      setDetail((current) => {
        if (!stillCurrent() || current == null || current.example_id !== requestedExampleId) return current
        let changed = false
        const members = current.members.map((item) => {
          if (item.id !== memberId || item.value_url === refreshed.value_url) return item
          changed = true
          return { ...item, value_url: refreshed.value_url }
        })
        if (!changed) return current
        return { ...current, members }
      })
      if (!stillCurrent()) throw refreshAborted()
      return refreshed.value_url ?? null
    } catch (err: unknown) {
      if (!stillCurrent()) throw refreshAborted()
      throw err
    } finally {
      lifecycle.signal.removeEventListener('abort', followLifecycle)
      if (memberRefreshRef.current.get(memberId) === controller) memberRefreshRef.current.delete(memberId)
    }
  }, [exampleId, exampleRepeated, requestKey, selectionKey])

  return { detail, loading, error, notFound, retry, refreshDisplayUrls }
}
