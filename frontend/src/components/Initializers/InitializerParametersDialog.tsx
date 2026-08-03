import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  Text,
  Textarea,
} from '@fluentui/react-components'

import type { RegisteredInitializer } from '@/types'

import { useAdditionalInitializersStyles } from './AdditionalInitializers.styles'
import { formatSupportedParameterSummary } from './initializerFormatting'

interface InitializerParametersDialogProps {
  open: boolean
  mode: 'add' | 'edit'
  initializer: RegisteredInitializer | null
  initialParameters?: Record<string, unknown> | null
  submitting?: boolean
  onSubmit: (parameters: Record<string, unknown> | null) => void | Promise<void>
  onOpenChange: (open: boolean) => void
}

function serializeParameters(parameters?: Record<string, unknown> | null): string {
  return JSON.stringify(parameters ?? {}, null, 2)
}

function parseParametersText(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed) {
    return null
  }

  const parsed: unknown = JSON.parse(trimmed)
  if (parsed === null) {
    return null
  }
  if (typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Parameters must be a JSON object.')
  }
  return parsed as Record<string, unknown>
}

export default function InitializerParametersDialog({
  open,
  mode,
  initializer,
  initialParameters = null,
  submitting = false,
  onSubmit,
  onOpenChange,
}: InitializerParametersDialogProps) {
  const styles = useAdditionalInitializersStyles()
  const [parametersText, setParametersText] = useState(() => serializeParameters(initialParameters))
  const [error, setError] = useState<string | null>(null)

  const acceptsParameters = (initializer?.supported_parameters.length ?? 0) > 0

  const handleSubmit = async (): Promise<void> => {
    if (!acceptsParameters) {
      setError(null)
      await onSubmit(null)
      return
    }

    let parameters: Record<string, unknown> | null
    try {
      parameters = parseParametersText(parametersText)
    } catch (parseError) {
      setError(parseError instanceof Error ? parseError.message : 'Invalid initializer settings.')
      return
    }

    setError(null)
    await onSubmit(parameters)
  }

  const initializerName = initializer?.initializer_name ?? ''
  const title = mode === 'add' ? `Add ${initializerName} initializer` : `Edit ${initializerName} initializer`
  const submitLabel = mode === 'add' ? 'Add' : 'Save'

  return (
    <Dialog open={open} onOpenChange={(_, data) => onOpenChange(data.open)}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{title}</DialogTitle>
          <DialogContent className={styles.dialogContent}>
            {initializer && (
              <>
                <Text size={300}>{initializer.description || 'No description available.'}</Text>
                {initializer.required_env_vars.length > 0 && (
                  <Text size={200} className={styles.envVarText}>
                    Required env vars: {initializer.required_env_vars.join(', ')}
                  </Text>
                )}
                <div className={styles.parameterList}>
                  {formatSupportedParameterSummary(initializer).map((summary: string) => (
                    <Text key={summary} className={styles.parameterHint} size={200}>
                      {summary}
                    </Text>
                  ))}
                </div>
              </>
            )}
            {acceptsParameters ? (
              <Field label="Parameters JSON">
                <Textarea
                  className={styles.parametersEditor}
                  value={parametersText}
                  onChange={(_, data) => {
                    setParametersText(data.value)
                    setError(null)
                  }}
                  disabled={submitting}
                />
              </Field>
            ) : (
              <Text size={300} className={styles.parameterHint}>
                This initializer takes no parameters.
              </Text>
            )}
            {error && (
              <Text role="alert" className={styles.errorText}>
                {error}
              </Text>
            )}
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={() => onOpenChange(false)} disabled={submitting}>
              Cancel
            </Button>
            <Button
              appearance="primary"
              onClick={() => void handleSubmit()}
              disabled={submitting || !initializer}
            >
              {submitting ? `${submitLabel}...` : submitLabel}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
