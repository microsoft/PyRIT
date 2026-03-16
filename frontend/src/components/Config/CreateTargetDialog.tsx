import { useState } from 'react'
import {
  Dialog,
  DialogSurface,
  DialogTitle,
  DialogBody,
  DialogContent,
  DialogActions,
  Button,
  Input,
  Label,
  Select,
  tokens,
  Field,
  MessageBar,
  MessageBarBody,
} from '@fluentui/react-components'
import { targetsApi } from '../../services/api'
import { useCreateTargetDialogStyles } from './CreateTargetDialog.styles'

const SUPPORTED_TARGET_TYPES = [
  'OpenAIChatTarget',
  'OpenAICompletionTarget',
  'OpenAIImageTarget',
  'OpenAIVideoTarget',
  'OpenAITTSTarget',
  'OpenAIResponseTarget',
] as const

interface CreateTargetDialogProps {
  open: boolean
  onClose: () => void
  onCreated: () => void
}

export default function CreateTargetDialog({ open, onClose, onCreated }: CreateTargetDialogProps) {
  const styles = useCreateTargetDialogStyles()
  const [targetType, setTargetType] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [modelName, setModelName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{ targetType?: string; endpoint?: string }>({})

  const resetForm = () => {
    setTargetType('')
    setEndpoint('')
    setModelName('')
    setApiKey('')
    setError(null)
    setFieldErrors({})
  }

  const handleClose = () => {
    resetForm()
    onClose()
  }

  const handleSubmit = async () => {
    const errors: { targetType?: string; endpoint?: string } = {}
    if (!targetType) errors.targetType = 'Please select a target type'
    if (!endpoint) errors.endpoint = 'Please provide an endpoint URL'
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setFieldErrors({})

    setSubmitting(true)
    setError(null)

    try {
      const params: Record<string, unknown> = {
        endpoint,
      }
      if (modelName) params.model_name = modelName
      if (apiKey) params.api_key = apiKey

      await targetsApi.createTarget({
        type: targetType,
        params,
      })
      resetForm()
      onCreated()
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message)
      } else {
        setError('Failed to create target')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => { if (!data.open) handleClose() }}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Create New Target</DialogTitle>
          <DialogContent>
            <form className={styles.form} onSubmit={(e) => { e.preventDefault(); handleSubmit() }}>
              {error && (
                <MessageBar intent="error">
                  <MessageBarBody>{error}</MessageBarBody>
                </MessageBar>
              )}

              <Field
                label="Target Type"
                required
                validationMessage={fieldErrors.targetType}
                validationState={fieldErrors.targetType ? 'error' : 'none'}
              >
                <Select
                  value={targetType}
                  onChange={(_, data) => setTargetType(data.value)}
                >
                  <option value="">Select a target type</option>
                  {SUPPORTED_TARGET_TYPES.map((type) => (
                    <option key={type} value={type}>{type}</option>
                  ))}
                </Select>
              </Field>

              <Field
                label="Endpoint URL"
                required
                validationMessage={fieldErrors.endpoint}
                validationState={fieldErrors.endpoint ? 'error' : 'none'}
              >
                <Input
                  placeholder="https://your-resource.openai.azure.com/"
                  value={endpoint}
                  onChange={(_, data) => setEndpoint(data.value)}
                />
              </Field>

              <Field label="Model / Deployment Name">
                <Input
                  placeholder="e.g. gpt-4o, dall-e-3"
                  value={modelName}
                  onChange={(_, data) => setModelName(data.value)}
                />
              </Field>

              <Field label="API Key">
                <Input
                  type="password"
                  placeholder="API key (stored in memory only)"
                  value={apiKey}
                  onChange={(_, data) => setApiKey(data.value)}
                />
              </Field>

              <Label size="small" style={{ color: tokens.colorNeutralForeground3 }}>
                Targets can also be auto-populated by adding an initializer (e.g. <code>airt</code>) to your{' '}
                <code>~/.pyrit/.pyrit_conf</code> file, which reads endpoints from your <code>.env</code> and{' '}
                <code>.env.local</code> files. See{' '}
                <a href="https://github.com/Azure/PyRIT/blob/main/.pyrit_conf_example" target="_blank" rel="noopener noreferrer">
                  .pyrit_conf_example
                </a>.
              </Label>
            </form>
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={handleClose} disabled={submitting}>
              Cancel
            </Button>
            <Button appearance="primary" onClick={handleSubmit} disabled={submitting || !targetType || !endpoint}>
              {submitting ? 'Creating...' : 'Create Target'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
