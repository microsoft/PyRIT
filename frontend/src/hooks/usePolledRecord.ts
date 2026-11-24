import { useCallback, useEffect, useRef, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
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

interface ScopedState<T> {
  /** The record key and runtime the state was loaded for. */
  readonly scope: string
  readonly state: PolledRecordState<T>
}

const LOADING_STATE: PolledRecordState<never> = { loadStatus: 'loading', record: null, error: null, stale: false }
const INVALID_KEY_STATE: PolledRecordState<never> = {
  loadStatus: 'not-found', record: null, error: 'The ID in this URL is missing or invalid.', stale: false,
}

/**
 * Loads one record and polls it while `isActive` holds. One failed refresh is
 * retried before the error is shown; a record already shown stays visible.
 * Loading waits for the backend runtime and starts over when the record key or
 * the runtime changes, so a record from another key or runtime is never shown.
 * `load` and `isActive` must be stable, such as module-level functions.
 */
export function usePolledRecord<T>(
  recordKey: string,
  load: (recordKey: string, signal: AbortSignal) => Promise<T>,
  isActive: (record: T) => boolean,
  intervalMs: number,
): UsePolledRecordResult<T> {
  const { ready, generation } = useRuntime()
  const keyValid = recordKey.trim() !== ''
  const scope = JSON.stringify([generation, recordKey])
  const [scoped, setScoped] = useState<ScopedState<T> | null>(null)
  const [epoch, setEpoch] = useState(0)
  const latestRef = useRef<{ readonly scope: string; readonly record: T } | null>(null)

  useEffect(() => {
    if (!keyValid || !ready) return
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
        latestRef.current = { scope, record }
        retried = false
        setScoped({ scope, state: { loadStatus: 'ready', record, error: null, stale: false } })
        if (isActive(record)) schedule()
      } catch (error: unknown) {
        if (!active || current.signal.aborted) return
        const apiError = toApiError(error)
        if (apiError.status !== 404 && !retried) {
          retried = true
          schedule()
          return
        }
        const previous = latestRef.current?.scope === scope ? latestRef.current.record : null
        setScoped({
          scope,
          state: {
            loadStatus: previous !== null ? 'ready' : apiError.status === 404 ? 'not-found' : 'error',
            record: previous,
            error: apiError.detail,
            stale: previous !== null,
          },
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
  }, [scope, recordKey, keyValid, ready, load, isActive, intervalMs, epoch])

  const retry = useCallback((): void => {
    setScoped((previous) => previous?.scope === scope ? {
      scope,
      state: {
        ...previous.state,
        loadStatus: previous.state.record !== null ? 'ready' : 'loading',
        error: null,
        stale: false,
      },
    } : previous)
    setEpoch((value) => value + 1)
  }, [scope])

  const applyRecord = useCallback((record: T): void => {
    latestRef.current = { scope, record }
    setScoped({ scope, state: { loadStatus: 'ready', record, error: null, stale: false } })
    setEpoch((value) => value + 1)
  }, [scope])

  const state: PolledRecordState<T> = !keyValid ? INVALID_KEY_STATE : scoped?.scope === scope ? scoped.state : LOADING_STATE
  return { state, retry, applyRecord }
}
