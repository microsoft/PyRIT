import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { MessageBar, MessageBarBody } from '@fluentui/react-components'

import { runtimeApi } from '@/services/api'
import type { RegistryRecipeKind, RestoreFailure, RuntimeReadiness } from '@/types'

import { useRuntimeBannerStyles } from './useRuntime.styles'

const POLL_INTERVAL_MS = 2_000
const KIND_LABELS: Record<RegistryRecipeKind, string> = {
  targets: 'Target',
  converters: 'Converter',
  scorers: 'Scorer',
  techniques: 'Technique',
}
const RuntimeContext = createContext<RuntimeReadiness>({ ready: true, state: 'ready', generation: '' })

// eslint-disable-next-line react-refresh/only-export-components
export function useRuntime(): RuntimeReadiness {
  return useContext(RuntimeContext)
}

export function RuntimeProvider({ children }: { children: ReactNode }) {
  const [runtime, setRuntime] = useState<RuntimeReadiness>({ ready: false, state: 'connecting', generation: '' })
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
  }, [])
  return <RuntimeContext.Provider value={runtime}>{children}</RuntimeContext.Provider>
}

interface RestoreFailures {
  failures: RestoreFailure[]
  listFailed: boolean
}

// The list is read when the count or the runtime generation changes, rather than on every readiness poll.
function useRestoreFailures(active: boolean, count: number, generation: string): RestoreFailures {
  const [failures, setFailures] = useState<RestoreFailure[]>([])
  const [listFailed, setListFailed] = useState(false)
  useEffect(() => {
    if (!active) return
    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    const load = async (): Promise<void> => {
      try {
        const list = await runtimeApi.getRestoreFailures()
        if (!cancelled) {
          setFailures(list.items)
          setListFailed(false)
        }
      } catch {
        // Keep the last list and say it couldn't be refreshed; try again shortly.
        if (!cancelled) {
          setListFailed(true)
          retryTimer = setTimeout(() => { void load() }, POLL_INTERVAL_MS)
        }
      }
    }
    void load()
    return () => {
      cancelled = true
      clearTimeout(retryTimer)
    }
  }, [active, count, generation])
  return { failures: active ? failures : [], listFailed: active && listFailed }
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
  const failureCount = runtime.restore_failures ?? 0
  const styles = useRuntimeBannerStyles()
  const { failures, listFailed } = useRestoreFailures(
    runtime.ready && failureCount > 0,
    failureCount,
    runtime.generation,
  )
  if (runtime.ready) {
    if (failureCount === 0) return null
    const techniqueFailed = failures.some((failure) => failure.kind === 'techniques')
    return (
      <MessageBar intent="warning">
        <MessageBarBody>
          Some saved entries couldn&apos;t be restored, so they can&apos;t be used until an administrator fixes or
          deletes their saved files, then applies the configuration or restarts the backend.
          {techniqueFailed && ' Until then, a scan has to name its techniques instead of choosing all, default, or a tag.'}
          <div aria-live="polite">
            {failures.length > 0 && (
              // Focusable so the list can be scrolled from the keyboard.
              <ul aria-label="Saved entries that couldn't be restored" className={styles.failureList} tabIndex={0}>
                {failures.map((failure) => (
                  <li key={`${failure.kind}/${failure.name ?? ''}`}>{describeFailure(failure)}</li>
                ))}
              </ul>
            )}
            {listFailed && <div>The list of saved entries couldn&apos;t be loaded. Trying again.</div>}
          </div>
        </MessageBarBody>
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
