import { createContext, useContext, useEffect, useId, useState } from 'react'
import type { ReactNode } from 'react'

import { Button, MessageBar, MessageBarActions, MessageBarBody, MessageBarTitle } from '@fluentui/react-components'

import DeleteSavedEntryButton from '@/components/Registry/DeleteSavedEntryButton'
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
  forget: (failure: RestoreFailure) => void
}

// A later apply or restart can fail again with the same count and generation, so the list is read on every
// poll while the banner shows.
function useRestoreFailures(): RestoreFailures {
  const [failures, setFailures] = useState<RestoreFailure[]>([])
  const [listFailed, setListFailed] = useState(false)
  useEffect(() => {
    let cancelled = false
    let fetching = false
    const load = async (): Promise<void> => {
      if (fetching) return
      fetching = true
      try {
        const list = await runtimeApi.getRestoreFailures()
        if (!cancelled) {
          setFailures(list.items)
          setListFailed(false)
        }
      } catch {
        // Keep the last list and say it couldn't be refreshed; the next poll tries again.
        if (!cancelled) setListFailed(true)
      } finally {
        fetching = false
      }
    }
    void load()
    const timer = setInterval(() => { void load() }, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])
  // A deleted entry is gone from the server's list too, so it leaves this one without waiting for the next refresh.
  const forget = (deleted: RestoreFailure): void => {
    setFailures((current) => current.filter((failure) => (
      failure.kind !== deleted.kind || failure.name !== deleted.name
    )))
  }
  return { failures, listFailed, forget }
}

function describeFailure(failure: RestoreFailure): string {
  const subject = failure.name === null ? `Saved ${failure.kind}` : `${KIND_LABELS[failure.kind]} ${failure.name}`
  // An administrator's message already names the entry it depends on; the explanation others get doesn't.
  const dependency = failure.dependency && !failure.message.includes(`'${failure.dependency.name}'`)
    ? ` (${KIND_LABELS[failure.dependency.kind].toLowerCase()} ${failure.dependency.name})`
    : ''
  return `${subject}: ${failure.message}${dependency}`
}

function RestoreFailuresBanner({ count }: { count: number }) {
  const styles = useRuntimeBannerStyles()
  const listId = useId()
  const [expanded, setExpanded] = useState(false)
  const { failures, listFailed, forget } = useRestoreFailures()
  const summary = count === 1
    ? "1 saved entry couldn't be restored, so new runs and sends are unavailable until it's fixed or deleted."
    : `${count} saved entries couldn't be restored, so new runs and sends are unavailable until they're fixed or deleted.`
  return (
    <MessageBar intent="warning" layout="multiline">
      <MessageBarBody>
        <MessageBarTitle>Some saved entries couldn&apos;t be restored</MessageBarTitle>
        {summary} Drafts are preserved.
        Whoever saved an entry, or an administrator, can delete it from the details, and an administrator can fix
        its file. Then apply the configuration or restart the backend.
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
              <li key={`${failure.kind}/${failure.name ?? ''}`}>
                {describeFailure(failure)}
                {failure.name !== null && (
                  <DeleteSavedEntryButton
                    kind={failure.kind}
                    name={failure.name}
                    className={styles.failureDelete}
                    onDeleted={() => { forget(failure) }}
                  />
                )}
              </li>
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
  if (runtime.ready) return null
  const failureCount = runtime.restore_failures ?? 0
  if (runtime.state === 'failed' && failureCount > 0) return <RestoreFailuresBanner count={failureCount} />
  if (runtime.state === 'failed') {
    // Deleting every entry that couldn't be restored leaves the runtime failed until it restores again.
    return (
      <MessageBar intent="warning">
        <MessageBarBody>
          PyRIT isn&apos;t ready, so new runs and sends are unavailable. Drafts are preserved. An administrator can
          apply the configuration or restart the backend.
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
