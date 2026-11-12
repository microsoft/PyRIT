import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { MessageBar, MessageBarBody } from '@fluentui/react-components'

import { runtimeApi } from '@/services/api'
import type { RuntimeReadiness } from '@/types'

const POLL_INTERVAL_MS = 2_000
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

export function RuntimeBanner() {
  const runtime = useRuntime()
  if (runtime.ready) return null
  // Multiline, since the text changes after the bar first lays out, and Fluent's automatic layout only rechecks
  // on a resize.
  if (runtime.state === 'failed' && (runtime.restore_failures ?? 0) > 0) {
    return (
      <MessageBar intent="warning" layout="multiline">
        <MessageBarBody>
          Some saved entries couldn&apos;t be restored, so new runs and sends are unavailable. An administrator has to
          view the failures in Configuration, fix or delete the files in registry_recipes_source, then reinitialize
          PyRIT or restart the backend. Drafts are preserved.
        </MessageBarBody>
      </MessageBar>
    )
  }
  if (runtime.state === 'failed') {
    // Deleting every entry that couldn't be restored leaves the runtime failed until it restores again.
    return (
      <MessageBar intent="warning">
        <MessageBarBody>
          PyRIT isn&apos;t ready, so new runs and sends are unavailable. Drafts are preserved. An administrator can
          reinitialize PyRIT or restart the backend.
        </MessageBarBody>
      </MessageBar>
    )
  }
  return (
    <MessageBar intent="warning" layout="multiline">
      <MessageBarBody>
        PyRIT runtime: {runtime.state}. New runs and sends are unavailable.
        Administrators can repair saved sources and retry from Configuration. Drafts are preserved.
      </MessageBarBody>
    </MessageBar>
  )
}
