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
  Radio,
  RadioGroup,
  Select,
  Switch,
  Text,
  tokens,
  Field,
  MessageBar,
  MessageBarBody,
} from '@fluentui/react-components'
import { targetsApi } from '@/services/api'
import { useCreateTargetDialogStyles } from './CreateTargetDialog.styles'

interface TargetTypeConfig {
  readonly kind: 'openai' | 'azureml'
  readonly supportsEntra: boolean
}

const TARGET_TYPE_CONFIG: Record<string, TargetTypeConfig> = {
  OpenAIChatTarget: { kind: 'openai', supportsEntra: true },
  OpenAICompletionTarget: { kind: 'openai', supportsEntra: true },
  OpenAIImageTarget: { kind: 'openai', supportsEntra: true },
  OpenAIVideoTarget: { kind: 'openai', supportsEntra: true },
  OpenAITTSTarget: { kind: 'openai', supportsEntra: true },
  OpenAIResponseTarget: { kind: 'openai', supportsEntra: true },
  AzureMLChatTarget: { kind: 'azureml', supportsEntra: true },
}

const SUPPORTED_TARGET_TYPES = Object.keys(TARGET_TYPE_CONFIG)

type AuthMode = 'api_key' | 'entra'

// Mirrors backend's hostname-suffix check (list in target_service.py).
// The backend still does the check and will reject unsupported endpoints, but this allows us to show a warning in the UI if the user selects Microsoft Entra authentication with a non-Azure OpenAI endpoint.
const AZURE_OPENAI_HOSTNAME_SUFFIXES = [
  '.openai.azure.com',
  '.ai.azure.com',
  '.services.ai.azure.com',
  '.cognitiveservices.azure.com',
]

// Mirrors backend's hostname-suffix check for Azure ML managed online endpoints
// (list in target_service.py). Used to warn the user when Microsoft Entra
// authentication is selected with a non-AML endpoint for AzureMLChatTarget.
const AZURE_ML_HOSTNAME_SUFFIXES = ['.inference.ml.azure.com']

function isAzureOpenAiEndpoint(endpoint: string): boolean {
  try {
    const host = new URL(endpoint).hostname.toLowerCase()
    return AZURE_OPENAI_HOSTNAME_SUFFIXES.some((s) => host.endsWith(s))
  } catch {
    return false
  }
}

function isAzureMlEndpoint(endpoint: string): boolean {
  try {
    const host = new URL(endpoint).hostname.toLowerCase()
    return AZURE_ML_HOSTNAME_SUFFIXES.some((s) => host.endsWith(s))
  } catch {
    return false
  }
}

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
  const [hasDifferentUnderlying, setHasDifferentUnderlying] = useState(false)
  const [underlyingModel, setUnderlyingModel] = useState('')
  const [authMode, setAuthMode] = useState<AuthMode>('api_key')
  const [apiKey, setApiKey] = useState('')
  const [maxNewTokens, setMaxNewTokens] = useState('400')
  const [temperature, setTemperature] = useState('1.0')
  const [topP, setTopP] = useState('1.0')
  const [repetitionPenalty, setRepetitionPenalty] = useState('1.0')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{ targetType?: string; endpoint?: string }>({})

  const targetConfig = TARGET_TYPE_CONFIG[targetType]
  const isAzureML = targetConfig?.kind === 'azureml'
  const isOpenAi = targetConfig?.kind === 'openai'
  const supportsEntra = targetConfig?.supportsEntra ?? false
  const showAuthField = targetType !== '' && supportsEntra
  const isEntra = showAuthField && authMode === 'entra'
  const entraEndpointError: string | null = (() => {
    if (!isEntra || endpoint === '') return null
    if (isOpenAi && !isAzureOpenAiEndpoint(endpoint)) {
      return 'Entra auth only works with Azure OpenAI / AI Foundry endpoints (for example, *.openai.azure.com or *.ai.azure.com).'
    }
    if (isAzureML && !isAzureMlEndpoint(endpoint)) {
      return 'Entra auth for AzureMLChatTarget only works with Azure ML managed online endpoints (for example, *.inference.ml.azure.com).'
    }
    return null
  })()
  const showEntraEndpointError = entraEndpointError !== null

  const resetForm = () => {
    setTargetType('')
    setEndpoint('')
    setModelName('')
    setHasDifferentUnderlying(false)
    setUnderlyingModel('')
    setAuthMode('api_key')
    setApiKey('')
    setMaxNewTokens('400')
    setTemperature('1.0')
    setTopP('1.0')
    setRepetitionPenalty('1.0')
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
      if (!isEntra && apiKey) params.api_key = apiKey

      if (hasDifferentUnderlying && underlyingModel) params.underlying_model = underlyingModel

      if (isAzureML) {
        const parsedMaxNewTokens = parseInt(maxNewTokens, 10)
        if (!isNaN(parsedMaxNewTokens)) params.max_new_tokens = parsedMaxNewTokens
        const parsedTemperature = parseFloat(temperature)
        if (!isNaN(parsedTemperature)) params.temperature = parsedTemperature
        const parsedTopP = parseFloat(topP)
        if (!isNaN(parsedTopP)) params.top_p = parsedTopP
        const parsedRepetitionPenalty = parseFloat(repetitionPenalty)
        if (!isNaN(parsedRepetitionPenalty)) params.repetition_penalty = parsedRepetitionPenalty
      }

      await targetsApi.createTarget({
        type: targetType,
        params,
        ...(isEntra ? { auth_mode: 'entra' as const } : {}),
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
                  onChange={(_, data) => {
                    const next = data.value
                    setTargetType(next)
                    if (!(TARGET_TYPE_CONFIG[next]?.supportsEntra ?? false)) {
                      setAuthMode('api_key')
                    }
                  }}
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
                  placeholder={isAzureML
                    ? 'https://your-model.region.inference.ml.azure.com/score'
                    : 'https://your-resource.openai.azure.com/'}
                  value={endpoint}
                  onChange={(_, data) => setEndpoint(data.value)}
                />
              </Field>

              <Field label="Model / Deployment Name">
                <Input
                  placeholder={isAzureML ? 'e.g. Llama-3.2-3B-Instruct' : 'e.g. gpt-4o, my-deployment'}
                  value={modelName}
                  onChange={(_, data) => setModelName(data.value)}
                />
              </Field>

              <div>
                <Switch
                  checked={hasDifferentUnderlying}
                  onChange={(_, data) => {
                    setHasDifferentUnderlying(data.checked)
                    if (!data.checked) setUnderlyingModel('')
                  }}
                  label="Underlying model differs from deployment name"
                />
                <Text size={200} style={{ color: tokens.colorNeutralForeground3, display: 'block', marginTop: '2px' }}>
                  On Azure, the deployment name (e.g. my-gpt4-deployment) may differ from the actual model (e.g. gpt-4o).
                </Text>
              </div>

              {hasDifferentUnderlying && (
                <Field label="Underlying Model">
                  <Input
                    placeholder="e.g. gpt-4o-2024-08-06"
                    value={underlyingModel}
                    onChange={(_, data) => setUnderlyingModel(data.value)}
                  />
                </Field>
              )}

              {isAzureML && (
                <>
                  <Field label="Max New Tokens">
                    <Input
                      type="number"
                      placeholder="400"
                      value={maxNewTokens}
                      onChange={(_, data) => setMaxNewTokens(data.value)}
                    />
                  </Field>

                  <Field label="Temperature">
                    <Input
                      type="number"
                      placeholder="1.0"
                      value={temperature}
                      onChange={(_, data) => setTemperature(data.value)}
                    />
                  </Field>

                  <Field label="Top P">
                    <Input
                      type="number"
                      placeholder="1.0"
                      value={topP}
                      onChange={(_, data) => setTopP(data.value)}
                    />
                  </Field>

                  <Field label="Repetition Penalty">
                    <Input
                      type="number"
                      placeholder="1.0"
                      value={repetitionPenalty}
                      onChange={(_, data) => setRepetitionPenalty(data.value)}
                    />
                  </Field>
                </>
              )}

              {showAuthField && (
                <Field label="Authentication">
                  <RadioGroup
                    value={authMode}
                    onChange={(_, data) => {
                      const next = data.value as AuthMode
                      setAuthMode(next)
                      if (next === 'entra') setApiKey('')
                    }}
                  >
                    <Radio value="api_key" label="API Key" />
                    <Radio value="entra" label="Microsoft Entra Authentication" />
                  </RadioGroup>
                </Field>
              )}

              {showEntraEndpointError && (
                <MessageBar intent="error" className={styles.warningMessage}>
                  <MessageBarBody className={styles.warningMessageBody}>
                    {entraEndpointError}
                  </MessageBarBody>
                </MessageBar>
              )}

              {!isEntra && (
                <Field label="API Key">
                  <Input
                    type="password"
                    placeholder="API key (stored in memory only)"
                    value={apiKey}
                    onChange={(_, data) => setApiKey(data.value)}
                  />
                </Field>
              )}

              <Label size="small" style={{ color: tokens.colorNeutralForeground3 }}>
                Targets can also be auto-populated by adding an initializer (e.g. <code>airt</code>) to your{' '}
                <code>~/.pyrit/.pyrit_conf</code> file, which reads endpoints from your <code>.env</code> and{' '}
                <code>.env.local</code> files. See{' '}
                <a href="https://github.com/microsoft/PyRIT/blob/main/.pyrit_conf_example" target="_blank" rel="noopener noreferrer">
                  .pyrit_conf_example
                </a>.
              </Label>
            </form>
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={handleClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              appearance="primary"
              onClick={handleSubmit}
              disabled={submitting || !targetType || !endpoint || showEntraEndpointError}
            >
              {submitting ? 'Creating...' : 'Create Target'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
