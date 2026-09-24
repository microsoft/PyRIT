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

function displayValue(value: string | null | undefined): string {
  return value || 'None'
}

function formatLastActivity(value: string): string {
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1_000))
  if (elapsedSeconds < 60) return 'Just now'
  const elapsedMinutes = Math.floor(elapsedSeconds / 60)
  return `${elapsedMinutes} minute${elapsedMinutes === 1 ? '' : 's'} ago`
}

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
          warning.scenario_queue.length > 0
        ) ? 'Stop scenarios and reinitialize' : 'Reinitialize PyRIT'}
        cancelLabel="Cancel"
        onConfirm={() => { if (warning) void apply(warning) }}
        onCancel={() => setWarning(null)}
      >
        <p>Runtime-only targets, converters and other components will be reset and must be recreated.
          Saved files, not editor drafts, will be applied. There is no rollback of initializer side effects.</p>
        <Text as="h3" weight="semibold" size={400}>Scenario Queue</Text>
        {warning && warning.scenario_queue.length > 0 ? (
          <Table size="small" aria-label="Scenario Queue" className={styles.workTable}>
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Scenario</TableHeaderCell>
                <TableHeaderCell>State</TableHeaderCell>
                <TableHeaderCell>Operator</TableHeaderCell>
                <TableHeaderCell>Operation</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {warning.scenario_queue.map((item) => (
                <TableRow key={`${item.scenario_result_id ?? item.scenario_name}-${item.state}`}>
                  <TableCell className={styles.identifier}>
                    {item.scenario_result_id || item.scenario_name}
                  </TableCell>
                  <TableCell>{item.state}</TableCell>
                  <TableCell>{displayValue(item.operator)}</TableCell>
                  <TableCell>{displayValue(item.operation)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Text italic>There are currently no scenarios in the queue.</Text>
        )}
        <Text as="h3" weight="semibold" size={400}>Active chats</Text>
        {warning && warning.active_chats.length > 0 ? (
          <Table size="small" aria-label="Active chats" className={styles.workTable}>
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Conversation</TableHeaderCell>
                <TableHeaderCell>Operator</TableHeaderCell>
                <TableHeaderCell>Operation</TableHeaderCell>
                <TableHeaderCell>Last activity</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {warning.active_chats.map((chat) => (
                <TableRow key={chat.conversation_id}>
                  <TableCell className={styles.identifier}>{chat.conversation_id}</TableCell>
                  <TableCell>{displayValue(chat.operator)}</TableCell>
                  <TableCell>{displayValue(chat.operation)}</TableCell>
                  <TableCell>{formatLastActivity(chat.last_activity)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Text italic>There are no active chats.</Text>
        )}
        <p>Scenarios will be cancelled, completed history preserved, and nothing automatically restarted.</p>
      </ConfirmDialog>
    </section>
  )
}
