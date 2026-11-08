import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { Button, MessageBar, MessageBarActions, MessageBarBody } from '@fluentui/react-components'

import DeleteSavedEntryButton from '@/components/Registry/DeleteSavedEntryButton'
import { runtimeApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { RegistryRecipeKind, RestoreFailure, RuntimeReadiness } from '@/types'

import { useRuntimeBannerStyles } from './useRuntime.styles'

const POLL_INTERVAL_MS = 2_000
const RESTORE_FAILED_STATE = 'failed'
const KIND_LABELS: Record<RegistryRecipeKind, string> = {
  targets: 'Target',
  converters: 'Converter',
  scorers: 'Scorer',
  techniques: 'Technique',
}
const RuntimeContext = createContext<RuntimeReadiness>({ ready: true, state: 'ready', generation: '' })
const RuntimeRefreshContext = createContext<() => void>(() => {})

// eslint-disable-next-line react-refresh/only-export-components
export function useRuntime(): RuntimeReadiness {
  return useContext(RuntimeContext)
}

export function RuntimeProvider({ children }: { children: ReactNode }) {
  const [runtime, setRuntime] = useState<RuntimeReadiness>({ ready: false, state: 'connecting', generation: '' })
  const [refreshRequests, setRefreshRequests] = useState(0)
  useEffect(() => {
    let cancelled = false
    let polling = false
    const refresh = async (): Promise<void> => {
      if (polling) return
      polling = true
      try {
        const next = await runtimeApi.getReadiness()
        if (!cancelled) setRuntime(next)
      } catch {
        if (!cancelled) setRuntime((previous) => ({ ...previous, ready: false, state: 'unavailable' }))
      } finally {
        polling = false
      }
    }
    void refresh()
    const timer = setInterval(() => { void refresh() }, POLL_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(timer) }
  }, [refreshRequests])
  const requestRefresh = useCallback(() => { setRefreshRequests((count) => count + 1) }, [])
  return (
    <RuntimeContext.Provider value={runtime}>
      <RuntimeRefreshContext.Provider value={requestRefresh}>{children}</RuntimeRefreshContext.Provider>
    </RuntimeContext.Provider>
  )
}

interface RestoreFailures {
  failures: RestoreFailure[]
  listFailed: boolean
  retrying: boolean
  retryError: string | null
  retry: () => Promise<void>
  forget: (failure: RestoreFailure) => void
}

function useRestoreFailures(failed: boolean, generation: string): RestoreFailures {
  const requestRuntimeRefresh = useContext(RuntimeRefreshContext)
  const [failures, setFailures] = useState<RestoreFailure[]>([])
  const [listFailed, setListFailed] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)
  const listRevision = useRef(0)
  const retryInFlight = useRef(false)
  // The generation changes only when the runtime becomes ready or the backend restarts, so a new one
  // while failed means a later failure, even if no poll saw that happen.
  useEffect(() => {
    if (!failed) return
    let cancelled = false
    let fetching = false
    const refresh = async (): Promise<void> => {
      if (fetching || retryInFlight.current) return
      fetching = true
      const revision = listRevision.current
      try {
        const list = await runtimeApi.getRestoreFailures()
        // A retry that started after this request has newer results.
        if (!cancelled && revision === listRevision.current) {
          setFailures(list.items)
          setListFailed(false)
        }
      } catch {
        // Keep the last list and say it couldn't be refreshed; the next refresh tries again.
        if (!cancelled && revision === listRevision.current) setListFailed(true)
      } finally {
        fetching = false
      }
    }
    void refresh()
    // A restarted backend can fail again without the state changing, so keep the list current.
    const timer = setInterval(() => { void refresh() }, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
      // A retry still in flight belongs to this failure, not to a later one.
      listRevision.current += 1
      setFailures([])
      setListFailed(false)
      setRetryError(null)
    }
  }, [failed, generation])
  const retry = async (): Promise<void> => {
    if (retryInFlight.current) return
    retryInFlight.current = true
    listRevision.current += 1
    const revision = listRevision.current
    setRetrying(true)
    setRetryError(null)
    try {
      const result = await runtimeApi.retryRestore()
      if (revision === listRevision.current) {
        setFailures(result.items)
        setListFailed(false)
      }
    } catch (error) {
      if (revision === listRevision.current) setRetryError(toApiError(error).detail)
    } finally {
      retryInFlight.current = false
      setRetrying(false)
      requestRuntimeRefresh()
    }
  }
  // A deleted entry is gone from the server's list too, so it leaves this one without waiting for the next refresh.
  const forget = (deleted: RestoreFailure): void => {
    setFailures((current) => current.filter((failure) => (
      failure.kind !== deleted.kind || failure.name !== deleted.name
    )))
  }
  return { failures: failed ? failures : [], listFailed: failed && listFailed, retrying, retryError, retry, forget }
}

function describeFailure(failure: RestoreFailure): string {
  const subject = failure.name === null ? `Saved ${failure.kind}` : `${KIND_LABELS[failure.kind]} ${failure.name}`
  const dependency = failure.dependency
    ? ` (${KIND_LABELS[failure.dependency.kind].toLowerCase()} ${failure.dependency.name})`
    : ''
  return `${subject}: ${failure.message}${dependency}`
}

export function RuntimeBanner() {
  const runtime = useRuntime()
  const failed = runtime.state === RESTORE_FAILED_STATE
  const styles = useRuntimeBannerStyles()
  const { failures, listFailed, retrying, retryError, retry, forget } = useRestoreFailures(failed, runtime.generation)
  if (runtime.ready) return null
  if (failed) {
    return (
      <MessageBar intent="warning">
        <MessageBarBody>
          Some saved entries couldn&apos;t be restored, so new runs and sends are unavailable. Drafts are preserved.
          <div aria-live="polite">
            {failures.length > 0 && (
              // Focusable so the list can be scrolled from the keyboard.
              <ul aria-label="Saved entries that couldn't be restored" className={styles.failureList} tabIndex={0}>
                {failures.map((failure) => (
                  <li key={`${failure.kind}/${failure.name ?? ''}`}>
                    {describeFailure(failure)}
                    {failure.name !== null && (
                      <DeleteSavedEntryButton
                        kind={failure.kind}
                        name={failure.name}
                        className={styles.failureDelete}
                        onDeleted={() => forget(failure)}
                      />
                    )}
                  </li>
                ))}
              </ul>
            )}
            {listFailed && <div>The list of saved entries couldn&apos;t be loaded. Trying again.</div>}
          </div>
          Whoever saved an entry, or an administrator, can delete it here; an administrator can also fix its saved
          file. Then select Retry. Retry rebuilds only these entries.
          {retryError && <div role="alert">{retryError}</div>}
        </MessageBarBody>
        <MessageBarActions>
          <Button onClick={() => { void retry() }} disabled={retrying}>{retrying ? 'Retrying…' : 'Retry'}</Button>
        </MessageBarActions>
      </MessageBar>
    )
  }
  return (
    <MessageBar intent="warning">
      <MessageBarBody>
        PyRIT runtime: {runtime.state}. New runs and sends are unavailable.
        Administrators can repair saved sources and retry from Configuration. Drafts are preserved.
      </MessageBarBody>
    </MessageBar>
  )
}
