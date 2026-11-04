import { useEffect, useId, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Text,
} from '@fluentui/react-components'

import { convertersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ConverterInstance, SourceInstanceSpec } from '@/types'

import { useSaveConverterDialogStyles } from './SaveConverterDialog.styles'

interface SaveConverterDialogProps {
  converterType: string
  source: SourceInstanceSpec
  onClose: () => void
  onSaved: (converter: ConverterInstance, isCurrentOpening: boolean) => void
}

/** Saves the stage's applied settings without exposing or editing source inputs. */
export default function SaveConverterDialog({
  converterType, source, onClose, onSaved,
}: SaveConverterDialogProps) {
  const styles = useSaveConverterDialogStyles()
  const formId = useId()
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [showValidation, setShowValidation] = useState(false)
  const [error, setError] = useState<string>()
  const active = useRef(true)
  const pending = useRef(false)
  const errorRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])
  useEffect(() => { errorRef.current?.focus() }, [error])

  const close = (): void => {
    active.current = false
    onClose()
  }
  const save = async (): Promise<void> => {
    if (pending.current) return
    if (!name.trim()) {
      setShowValidation(true)
      return
    }
    pending.current = true
    setSubmitting(true)
    setError(undefined)
    try {
      const converter = await convertersApi.createConverter({ name: name.trim(), type: converterType, source })
      onSaved(converter, active.current)
    } catch (err: unknown) {
      if (active.current) setError(toApiError(err).detail)
    } finally {
      pending.current = false
      if (active.current) setSubmitting(false)
    }
  }

  return <Dialog open onOpenChange={(_, data) => { if (!data.open) close() }}>
    <DialogSurface>
      <DialogBody>
        <DialogTitle>Save to registry</DialogTitle>
        <DialogContent>
          <form id={formId} noValidate className={styles.content} onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}>
            <Text>Save these settings under a new name. The original converter stays unchanged.
              Saved converters are available until the backend restarts.</Text>
            {error && <MessageBar intent="error">
              <MessageBarBody ref={errorRef} tabIndex={-1} role="alert">{error}</MessageBarBody>
            </MessageBar>}
            <Field label="Registry name" required
              validationMessage={showValidation && !name.trim() ? 'Enter a registry name' : undefined}>
              <Input value={name} disabled={submitting} onChange={(_, data) => setName(data.value)} />
            </Field>
          </form>
        </DialogContent>
        <DialogActions>
          <Button onClick={close}>Cancel</Button>
          <Button appearance="primary" type="submit" form={formId}
            disabled={submitting} disabledFocusable={submitting}>{submitting ? 'Saving...' : 'Save'}</Button>
        </DialogActions>
      </DialogBody>
    </DialogSurface>
  </Dialog>
}
