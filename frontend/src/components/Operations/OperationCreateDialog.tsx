import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import {
  Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle, DialogTrigger,
  Field, Input, MessageBar, MessageBarBody,
} from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { conflictingOperation, toApiError } from '@/services/errors'
import type { Operation } from '@/types'
import { useOperationsStyles } from './Operations.styles'

interface OperationCreateDialogProps {
  trigger?: React.ReactElement
  open?: boolean
  onOpenChange?: (open: boolean) => void
  returnFocusRef?: React.RefObject<HTMLInputElement | null>
  onCreated: (operation: Operation) => void
  onUseExisting?: (operation: Operation) => void
}

export default function OperationCreateDialog({
  trigger, open: controlledOpen, onOpenChange, returnFocusRef, onCreated, onUseExisting,
}: OperationCreateDialogProps) {
  const styles = useOperationsStyles()
  const [internalOpen, setInternalOpen] = useState(false)
  const open = controlledOpen ?? internalOpen
  const setOpen = (value: boolean): void => { setInternalOpen(value); onOpenChange?.(value) }
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [existing, setExisting] = useState<Operation | null>(null)
  const [saving, setSaving] = useState(false)
  const submitting = useRef(false)
  const triggerRef = useRef<HTMLElement | null>(null)
  const wasOpen = useRef(false)

  useEffect(() => {
    const target = returnFocusRef?.current ?? triggerRef.current
    if (wasOpen.current && !open && target?.isConnected) target.focus()
    wasOpen.current = open
  }, [open, returnFocusRef])

  const resetDraft = (): void => { setName(''); setError(''); setExisting(null) }
  const finish = (operation: Operation, callback: (operation: Operation) => void): void => {
    setOpen(false)
    resetDraft()
    callback(operation)
  }
  const create = async (): Promise<void> => {
    if (submitting.current) return
    submitting.current = true
    setSaving(true)
    setError('')
    setExisting(null)
    try {
      const operation = await operationsApi.create({ name })
      finish(operation, onCreated)
    } catch (cause: unknown) {
      const conflict = conflictingOperation(cause)
      if (conflict) setExisting(conflict)
      else setError(toApiError(cause).detail)
    } finally {
      submitting.current = false
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => {
      if (saving) return
      if (data.open) triggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      setOpen(data.open)
      if (!data.open) resetDraft()
    }}>
      {trigger ? <DialogTrigger disableButtonEnhancement>{trigger}</DialogTrigger> : <></>}
      <DialogSurface>
        <DialogBody>
          <DialogTitle>New operation</DialogTitle>
          <DialogContent>
            <form className={styles.form} onSubmit={event => { event.preventDefault(); void create() }}>
              {error && <MessageBar intent="error" aria-live="polite"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
              {existing && (
                <MessageBar intent="warning" aria-live="polite"><MessageBarBody>
                  An operation with this name already exists.{' '}
                  {onUseExisting ? (
                    <Button className={styles.button} onClick={() => { finish(existing, onUseExisting) }}>Use existing</Button>
                  ) : (
                    <Link to={`/operations/${encodeURIComponent(existing.id)}`} className={styles.link} translate="no"
                      onClick={() => { setOpen(false); resetDraft() }}>Open {existing.name}</Link>
                  )}
                </MessageBarBody></MessageBar>
              )}
              <Field label="Name" required hint="Names are unique, ignoring case and surrounding spaces.">
                <Input className={styles.input} value={name} disabled={saving} maxLength={128}
                  name="operation-name" autoComplete="off"
                  onChange={(_, data) => { setName(data.value); setExisting(null) }} />
              </Field>
              <div className={styles.actions}>
                <DialogTrigger disableButtonEnhancement>
                  <Button className={styles.button} disabled={saving}>Cancel</Button>
                </DialogTrigger>
                <Button type="submit" appearance="primary" className={styles.button}
                  disabledFocusable={saving} disabled={!name.trim()}>
                  {saving ? 'Creating…' : 'Create operation'}
                </Button>
              </div>
            </form>
          </DialogContent>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
