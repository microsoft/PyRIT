import { useEffect, useState } from 'react'

import {
  Button,
  MessageBar,
  MessageBarBody,
  Text,
} from '@fluentui/react-components'

import ConfirmDialog from '@/components/ConfirmDialog'
import { configurationApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { RuntimeStatus } from '@/types'

import { useReinitializeStyles } from './Reinitialize.styles'

interface ReinitializeProps {
  version: string
  hasUnsavedChanges: boolean
  liveReinitializationEnabled: boolean
}

const POLL_INTERVAL_MS = 1_000

export default function Reinitialize({
  version,
  hasUnsavedChanges,
  liveReinitializationEnabled,
}: ReinitializeProps) {
  const styles = useReinitializeStyles()
  const [status, setStatus] = useState<RuntimeStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [showConfirmation, setShowConfirmation] = useState(false)
  const [showRuntimeStatus, setShowRuntimeStatus] = useState(false)

  useEffect(() => {
    let cancelled = false
    const refresh = async (): Promise<void> => {
      try {
        const response = await configurationApi.getRuntimeStatus()
        if (!cancelled) setStatus(response)
      } catch (reason) {
        if (!cancelled) setError(toApiError(reason).detail)
      }
    }
    void refresh()
    const timer = setInterval(() => { void refresh() }, POLL_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(timer) }
  }, [])

  const apply = async (): Promise<void> => {
    if (hasUnsavedChanges || !liveReinitializationEnabled) return
    setSubmitting(true)
    setError(null)
    setShowConfirmation(false)
    setShowRuntimeStatus(true)
    try {
      setStatus(await configurationApi.reinitialize(version))
    } catch (reason) {
      setError(toApiError(reason).detail)
    } finally {
      setSubmitting(false)
    }
  }

  const statusIsError = status?.state === 'failed'
    || status?.state === 'restart-required'
    || status?.outcome === 'busy'
    || status?.outcome === 'invalid-configuration'

  return (
    <section aria-label="Reinitialize PyRIT" className={styles.root}>
      {status && (showRuntimeStatus || statusIsError || !status.enabled) && (
        <MessageBar intent={statusIsError ? 'error' : 'info'}>
          <MessageBarBody>
            Runtime: {status.state}. {status.message}
            {!status.enabled && ' Reinitialization requires one backend worker and one replica.'}
          </MessageBarBody>
        </MessageBar>
      )}
      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      {hasUnsavedChanges && <Text>Save or explicitly discard unsaved edits before reinitializing.</Text>}
      {!liveReinitializationEnabled && (
        <Text>Set <code>enable_live_reinitialization: true</code> in the saved configuration to enable this action.</Text>
      )}
      <Button
        disabled={
          !status?.enabled
          || status.applying
          || status.state === 'restart-required'
          || submitting
          || hasUnsavedChanges
          || !liveReinitializationEnabled
          || !version
        }
        onClick={() => setShowConfirmation(true)}
      >
        Reinitialize PyRIT
      </Button>
      <ConfirmDialog
        open={showConfirmation}
        title="Reinitialize PyRIT for all users?"
        confirmLabel="Reinitialize PyRIT"
        cancelLabel="Cancel"
        onConfirm={() => { void apply() }}
        onCancel={() => setShowConfirmation(false)}
      >
        <p>PyRIT will apply the saved configuration, environment sources, scripts, and initializers.
          Unsaved editor changes are not included.</p>
        <p>The runtime must be idle. If work is active, wait for it to finish or cancel it with its existing controls,
          then retry. Runtime-only components will be reset, but memory and completed history are preserved.</p>
        <p>If initialization fails after replacement starts, restart the backend. Initializer side effects cannot be
          rolled back.</p>
      </ConfirmDialog>
    </section>
  )
}
