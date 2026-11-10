import { createContext, useContext, useEffect, useId, useState } from 'react'
import type { ReactNode } from 'react'

import { Button, MessageBar, MessageBarActions, MessageBarBody, MessageBarTitle } from '@fluentui/react-components'

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
function useRestoreFailures(count: number, generation: string): RestoreFailures {
  const [failures, setFailures] = useState<RestoreFailure[]>([])
  const [listFailed, setListFailed] = useState(false)
  useEffect(() => {
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
  }, [count, generation])
  return { failures, listFailed }
}

function describeFailure(failure: RestoreFailure): string {
  const subject = failure.name === null ? `Saved ${failure.kind}` : `${KIND_LABELS[failure.kind]} ${failure.name}`
  // An administrator's message already names the entry it depends on; the explanation others get doesn't.
  const dependency = failure.dependency && !failure.message.includes(`'${failure.dependency.name}'`)
    ? ` (${KIND_LABELS[failure.dependency.kind].toLowerCase()} ${failure.dependency.name})`
    : ''
  return `${subject}: ${failure.message}${dependency}`
}

function RestoreFailuresBanner({ count, generation }: { count: number; generation: string }) {
  const styles = useRuntimeBannerStyles()
  const listId = useId()
  const [expanded, setExpanded] = useState(false)
  const { failures, listFailed } = useRestoreFailures(count, generation)
  const techniqueFailed = failures.some((failure) => failure.kind === 'techniques')
  const entries = count === 1 ? '1 saved entry' : `${count} saved entries`
  const impact = techniqueFailed
    ? `${entries} can't be used until fixed, and until then a scan has to name its techniques.`
    : `${entries} can't be used until fixed.`
  return (
    <MessageBar intent="warning" layout="multiline">
      <MessageBarBody>
        <MessageBarTitle>Some saved entries couldn&apos;t be restored</MessageBarTitle>
        {impact} An administrator can fix or delete their files, then apply the configuration or restart the backend.
        <div
          id={listId}
          role="region"
          aria-label="Saved entries that couldn't be restored"
          // Focusable so a long list can be scrolled from the keyboard.
          tabIndex={0}
          className={styles.failureList}
          hidden={!expanded}
        >
          <ul>
            {failures.map((failure) => (
              <li key={`${failure.kind}/${failure.name ?? ''}`}>{describeFailure(failure)}</li>
            ))}
          </ul>
        </div>
        <div aria-live="polite">
          {listFailed && `The list of saved entries couldn't be loaded. Trying again.`}
        </div>
      </MessageBarBody>
      <MessageBarActions>
        <Button
          size="small"
          aria-expanded={expanded}
          aria-controls={listId}
          onClick={() => { setExpanded((current) => !current) }}
        >
          {expanded ? 'Hide details' : 'Show details'}
        </Button>
      </MessageBarActions>
    </MessageBar>
  )
}

export function RuntimeBanner() {
  const runtime = useRuntime()
  const failureCount = runtime.restore_failures ?? 0
  if (runtime.ready) {
    if (failureCount === 0) return null
    return <RestoreFailuresBanner count={failureCount} generation={runtime.generation} />
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
