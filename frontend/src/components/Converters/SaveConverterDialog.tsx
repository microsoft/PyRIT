import { useEffect, useId, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, MessageBarTitle, Text,
} from '@fluentui/react-components'

import { convertersApi } from '@/services/api'
import { isNotSaveableError, toApiError } from '@/services/errors'
import type { ConverterInstance, SourceInstanceSpec } from '@/types'
import { validateRegistryName } from '@/utils/registryName'

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
  // A refusal comes from the converter being copied or the changes, so a new name doesn't lift it.
  const [refusal, setRefusal] = useState<string | null>(null)
  const [nameConflict, setNameConflict] = useState<{ name: string, detail: string } | null>(null)
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
  const persist = refusal === null
  const trimmed = name.trim()
  const nameMessage = nameConflict?.name === trimmed ? nameConflict.detail
    : !trimmed ? 'Enter a registry name'
      : persist ? validateRegistryName(trimmed) : null
  const save = async (): Promise<void> => {
    if (pending.current) return
    if (nameMessage !== null) {
      setShowValidation(true)
      return
    }
    pending.current = true
    setSubmitting(true)
    setError(undefined)
    try {
      const converter = await convertersApi.createConverter({ name: trimmed, type: converterType, source, persist })
      onSaved(converter, active.current)
    } catch (err: unknown) {
      if (active.current) {
        const apiError = toApiError(err)
        if (persist && isNotSaveableError(apiError)) {
          setRefusal(apiError.detail)
        } else if (apiError.status === 409 && apiError.type === undefined) {
          setNameConflict({ name: trimmed, detail: apiError.detail })
          setShowValidation(true)
        } else {
          setError(apiError.detail)
        }
      }
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
            <Text>Save these settings under a new name. The original converter stays unchanged. The new converter
              comes back after the backend restarts or is reinitialized, rebuilt from the original with these changes.</Text>
            {refusal !== null && <MessageBar intent="warning" role="status">
              <MessageBarBody>
                <MessageBarTitle>This converter can&apos;t be saved</MessageBarTitle>
                {refusal} A Temporary converter is gone after the backend restarts or is reinitialized.
              </MessageBarBody>
            </MessageBar>}
            {error && <MessageBar intent="error">
              <MessageBarBody ref={errorRef} tabIndex={-1} role="alert">{error}</MessageBarBody>
            </MessageBar>}
            <Field label="Registry name" required hint="Lowercase letters, digits, and underscores, starting with a letter."
              validationMessage={showValidation && nameMessage !== null ? nameMessage : undefined}>
              <Input value={name} disabled={submitting} onChange={(_, data) => setName(data.value)} />
            </Field>
          </form>
        </DialogContent>
        <DialogActions>
          <Button onClick={close}>Cancel</Button>
          <Button appearance="primary" type="submit" form={formId}
            disabled={submitting} disabledFocusable={submitting}>
            {submitting ? 'Saving...' : persist ? 'Save' : 'Create as Temporary'}
          </Button>
        </DialogActions>
      </DialogBody>
    </DialogSurface>
  </Dialog>
}
