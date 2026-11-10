import { useCallback, useEffect, useRef, useState } from 'react'

import { toApiError } from '@/services/errors'

export type PolledRecordLoadStatus = 'loading' | 'ready' | 'not-found' | 'error'

export interface PolledRecordState<T> {
  readonly loadStatus: PolledRecordLoadStatus
  readonly record: T | null
  readonly error: string | null
  /** True when the shown record could not be refreshed. */
  readonly stale: boolean
}

export interface UsePolledRecordResult<T> {
  readonly state: PolledRecordState<T>
  /** Reloads after an error, keeping any record already shown. */
  readonly retry: () => void
  /** Shows a record returned by an action, then reloads and resumes polling if it is active. */
  readonly applyRecord: (record: T) => void
}

/**
 * Loads one record and polls it while `isActive` holds. One failed refresh is
 * retried before the error is shown; a record already shown stays visible.
 * `load` and `isActive` must be stable, such as module-level functions.
 */
export function usePolledRecord<T>(
  recordKey: string,
  load: (recordKey: string, signal: AbortSignal) => Promise<T>,
  isActive: (record: T) => boolean,
  intervalMs: number,
): UsePolledRecordResult<T> {
  const keyValid = recordKey.trim() !== ''
  const [state, setState] = useState<PolledRecordState<T>>(() => keyValid
    ? { loadStatus: 'loading', record: null, error: null, stale: false }
    : { loadStatus: 'not-found', record: null, error: 'The ID in this URL is missing or invalid.', stale: false })
  const [epoch, setEpoch] = useState(0)
  const latestRef = useRef<T | null>(null)

  useEffect(() => {
    if (!keyValid) return
    let active = true
    let retried = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let controller: AbortController | null = null

    const schedule = (): void => {
      timer = setTimeout(() => {
        timer = null
        void fetchRecordAsync()
      }, intervalMs)
    }

    const fetchRecordAsync = async (): Promise<void> => {
      const current = new AbortController()
      controller = current
      try {
        const record = await load(recordKey, current.signal)
        if (!active) return
        latestRef.current = record
        retried = false
        setState({ loadStatus: 'ready', record, error: null, stale: false })
        if (isActive(record)) schedule()
      } catch (error: unknown) {
        if (!active || current.signal.aborted) return
        const apiError = toApiError(error)
        if (apiError.status !== 404 && !retried) {
          retried = true
          schedule()
          return
        }
        const previous = latestRef.current
        setState({
          loadStatus: previous !== null ? 'ready' : apiError.status === 404 ? 'not-found' : 'error',
          record: previous,
          error: apiError.detail,
          stale: previous !== null,
        })
        if (apiError.status !== 404 && previous !== null && isActive(previous)) schedule()
      }
    }

    void fetchRecordAsync()
    return () => {
      active = false
      if (timer !== null) clearTimeout(timer)
      controller?.abort()
    }
  }, [recordKey, keyValid, load, isActive, intervalMs, epoch])

  const retry = useCallback((): void => {
    setState((previous) => ({
      ...previous,
      loadStatus: previous.record !== null ? 'ready' : 'loading',
      error: null,
      stale: false,
    }))
    setEpoch((value) => value + 1)
  }, [])

  const applyRecord = useCallback((record: T): void => {
    latestRef.current = record
    setState({ loadStatus: 'ready', record, error: null, stale: false })
    setEpoch((value) => value + 1)
  }, [])

  return { state, retry, applyRecord }
}
