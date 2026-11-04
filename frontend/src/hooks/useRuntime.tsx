import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { MessageBar, MessageBarBody } from '@fluentui/react-components'

import { runtimeApi } from '@/services/api'
import type { RegistryRecipeKind, RestoreFailure, RuntimeReadiness } from '@/types'

const POLL_INTERVAL_MS = 2_000
const RESTORE_FAILED_STATE = 'failed'
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

function useRestoreFailures(failed: boolean): RestoreFailure[] {
  const [failures, setFailures] = useState<RestoreFailure[]>([])
  useEffect(() => {
    if (!failed) return
    let cancelled = false
    let fetching = false
    const refresh = async (): Promise<void> => {
      if (fetching) return
      fetching = true
      try {
        const list = await runtimeApi.getRestoreFailures()
        if (!cancelled) setFailures(list.items)
      } catch {
        // Keep the last list; the next refresh tries again.
      } finally {
        fetching = false
      }
    }
    void refresh()
    // A restarted backend can fail again without the state changing, so keep the list current.
    const timer = setInterval(() => { void refresh() }, POLL_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(timer); setFailures([]) }
  }, [failed])
  return failed ? failures : []
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
  const failures = useRestoreFailures(failed)
  if (runtime.ready) return null
  if (failed) {
    return (
      <MessageBar intent="warning">
        <MessageBarBody>
          Some saved entries couldn&apos;t be restored, so new runs and sends are unavailable. Drafts are preserved.
          <div aria-live="polite">
            {failures.length > 0 && (
              <ul aria-label="Saved entries that couldn't be restored">
                {failures.map((failure) => (
                  <li key={`${failure.kind}/${failure.name ?? ''}`}>{describeFailure(failure)}</li>
                ))}
              </ul>
            )}
          </div>
          An administrator can fix or delete these saved files, then restart the backend.
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
