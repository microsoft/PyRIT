import { useRef, useState } from 'react'
import {
  Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle, DialogTrigger,
  MessageBar, MessageBarBody, Text, Tooltip,
} from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { OperationDetail, OperationUsage } from '@/types'
import { useOperationsStyles } from './Operations.styles'

interface OperationDeleteDialogProps {
  operation: OperationDetail
  /** Called when the server refuses because something now refers to the Operation. */
  onInUse: () => void
  onDeleted: () => void
}

function countLabel(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`
}

/** Why an Operation can't be deleted, or undefined when nothing refers to it. */
function deleteBlockedReason({ findings, attacks, scenario_runs: runs }: OperationUsage): string | undefined {
  const parts = [
    findings > 0 ? countLabel(findings, 'finding', 'findings') : '',
    attacks + runs > 0 ? countLabel(attacks + runs, 'linked attack or run', 'linked attacks or runs') : '',
  ].filter(Boolean)
  return parts.length > 0 ? `Has ${parts.join(' and ')}. Only empty operations can be deleted.` : undefined
}

export default function OperationDeleteDialog({ operation, onInUse, onDeleted }: OperationDeleteDialogProps) {
  const styles = useOperationsStyles()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const submitting = useRef(false)
  const blockedReason = deleteBlockedReason(operation.usage)

  const remove = async (): Promise<void> => {
    if (submitting.current) return
    submitting.current = true
    setDeleting(true)
    setError('')
    try {
      await operationsApi.delete(operation.id)
      setOpen(false)
      onDeleted()
    } catch (cause: unknown) {
      const apiError = toApiError(cause)
      if (apiError.status === 409) onInUse()
      setError(apiError.status === 409 ? 'This operation is no longer empty, so it can’t be deleted.' : apiError.detail)
    } finally {
      submitting.current = false
      setDeleting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => {
      if (deleting) return
      if (data.open) setError('')
      setOpen(data.open)
    }}>
      <DialogTrigger disableButtonEnhancement>
        <Tooltip relationship="description" content={blockedReason ?? 'Delete this empty operation.'}>
          <Button className={styles.button} disabledFocusable={blockedReason !== undefined}>Delete</Button>
        </Tooltip>
      </DialogTrigger>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Delete operation?</DialogTitle>
          <DialogContent className={styles.form}>
            <Text>
              Delete “<span translate="no">{operation.name}</span>”? No saved findings, attacks, or scanner runs
              use it. SDK attacks still running under it will save their results without an operation, and older
              history recorded under its name stays as it is. This cannot be undone.
            </Text>
            {error && <MessageBar intent="error" aria-live="polite"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
            <div className={styles.actions}>
              <DialogTrigger disableButtonEnhancement>
                <Button className={styles.button} disabled={deleting}>Cancel</Button>
              </DialogTrigger>
              <Button appearance="primary" className={styles.button}
                disabledFocusable={deleting || blockedReason !== undefined}
                onClick={() => { void remove() }}>{deleting ? 'Deleting…' : 'Delete operation'}</Button>
            </div>
          </DialogContent>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
