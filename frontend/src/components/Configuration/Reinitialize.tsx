import { useEffect, useState } from 'react'

import {
  Button,
  MessageBar,
  MessageBarBody,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
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
}

const POLL_INTERVAL_MS = 1_000

export default function Reinitialize({ version, hasUnsavedChanges }: ReinitializeProps) {
  const styles = useReinitializeStyles()
  const [status, setStatus] = useState<RuntimeStatus | null>(null)
  const [warning, setWarning] = useState<RuntimeStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
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

  const apply = async (confirmed?: RuntimeStatus): Promise<void> => {
    if (hasUnsavedChanges) return
    setSubmitting(true)
    setError(null)
    try {
      if (!confirmed) {
        const current = await configurationApi.getRuntimeStatus()
        setStatus(current)
        setWarning(current)
        return
      }
      setWarning(null)
      setShowRuntimeStatus(true)
      setStatus(await configurationApi.reinitialize(version, true, confirmed.work_revision))
    } catch (reason) {
      setError(toApiError(reason).detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section aria-label="Reinitialize PyRIT" className={styles.root}>
      {status && (showRuntimeStatus || status.state === 'failed' || status.state === 'blocked' || !status.enabled) && (
        <MessageBar intent={status.state === 'failed' || status.state === 'blocked' ? 'error' : 'info'}>
          <MessageBarBody>
            Runtime: {status.state}. {status.message}
            {' '}Outstanding: {status.active_work.scenario_ids.length} scenarios,
            {' '}{status.active_work.preparing} preparing, {status.active_work.sends} sends,
            {' '}{status.active_work.estimates} estimates, {status.active_work.requests} requests.
            {!status.enabled && ' Reinitialization requires one backend worker and one replica.'}
          </MessageBarBody>
        </MessageBar>
      )}
      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      {hasUnsavedChanges && <Text>Save or explicitly discard unsaved edits before reinitializing.</Text>}
      <Button
        disabled={!status?.enabled || status.applying || submitting || hasUnsavedChanges || !version}
        onClick={() => { void apply() }}
      >
        {status?.state === 'blocked' || status?.state === 'failed' ? 'Retry reinitialization' : 'Reinitialize PyRIT'}
      </Button>
      {status?.state === 'blocked' && (
        <Button onClick={() => {
          void configurationApi.cancelPendingApply().then(setStatus).catch((reason: unknown) => {
            setError(toApiError(reason).detail)
          })
        }}>Cancel pending apply</Button>
      )}
      <ConfirmDialog
        open={warning !== null}
        title="Reinitialize PyRIT for all users?"
        confirmLabel={warning && (
          warning.active_work.scenario_ids.length > 0 || warning.active_work.preparing > 0
          || warning.active_work.sends > 0 || warning.active_work.requests > 0
        ) ? 'Stop scenarios and reinitialize' : 'Reinitialize PyRIT'}
        cancelLabel="Cancel"
        onConfirm={() => { if (warning) void apply(warning) }}
        onCancel={() => setWarning(null)}
      >
        <p>Runtime-only targets, converters and other components will be reset and must be recreated.
          Saved files, not editor drafts, will be applied. There is no rollback of initializer side effects.</p>
        <Table size="small" aria-label="Active runtime work" className={styles.workTable}>
          <TableHeader>
            <TableRow>
              <TableHeaderCell className={styles.workLabel}>Active work</TableHeaderCell>
              <TableHeaderCell>Current</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell className={styles.workLabel}>Scenarios</TableCell>
              <TableCell className={styles.scenarioList}>
                {warning?.active_work.scenario_ids.join(', ') || 'None'}
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell className={styles.workLabel}>Preparing</TableCell>
              <TableCell>{warning?.active_work.preparing ?? 0}</TableCell>
            </TableRow>
            <TableRow>
              <TableCell className={styles.workLabel}>In-flight sends</TableCell>
              <TableCell>{warning?.active_work.sends ?? 0}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
        <p>Scenarios will be cancelled, completed history preserved, and nothing automatically restarted.</p>
      </ConfirmDialog>
    </section>
  )
}
