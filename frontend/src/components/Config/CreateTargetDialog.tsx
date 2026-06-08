import { useState, useEffect, useMemo } from 'react'
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
  Link,
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
import { DeleteRegular } from '@fluentui/react-icons'
import { targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { TargetInstance } from '@/types'
import { useCreateTargetDialogStyles } from './CreateTargetDialog.styles'
import { MAX_WEIGHT, parseWeight } from './weightValidation'

interface TargetTypeConfig {
  readonly kind: 'openai' | 'azureml' | 'roundrobin'
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
  RoundRobinTarget: { kind: 'roundrobin', supportsEntra: false },
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
  /** Existing targets, passed from the parent to avoid a redundant API call. */
  existingTargets?: TargetInstance[]
}

/** State for one selected inner target in the RoundRobinTarget form. */
interface SelectedInnerTarget {
  readonly registryName: string
  /**
   * Raw text the user has typed into the weight input. May be transiently
   * invalid (empty, "2.5", "99999999999", etc.) — the canonical numeric weight
   * is derived on demand via {@link parseWeight}. Keeping the raw string as
   * the single source of truth avoids the "user types 0 and the field
   * silently reverts" UX bug.
   */
  weightInput: string
}

/**
 * Resolve the effective underlying model for compatibility checks.
 *
 * Mirrors the backend's TARGET_EVAL_PARAM_FALLBACKS rule: when
 * underlying_model_name is empty (null, undefined, or empty string), fall back
 * to model_name. Without this fallback, two targets with the same model_name
 * but no underlying_model_name would compare as compatible on the frontend
 * even though they resolve to different actual models on the backend (or vice
 * versa), so the user would only see the failure as an opaque HTTP 400.
 *
 * Keep in sync with TARGET_EVAL_PARAM_FALLBACKS in
 * pyrit/models/identifiers/evaluation_identifier.py — the
 * TestFrontendBackendCompatibilitySync test guards against drift.
 */
function effectiveUnderlyingModel(t: TargetInstance): string | null {
  return t.underlying_model_name || t.model_name || null
}

/**
 * Check if two targets are compatible for grouping in a RoundRobinTarget.
 *
 * Must match the behavioral params that RoundRobinTarget validates on the backend:
 * same target_type + TARGET_EVAL_PARAMS (underlying_model_name, temperature, top_p),
 * with the underlying_model_name → model_name fallback from TARGET_EVAL_PARAM_FALLBACKS.
 *
 * NOTE: model_name itself is intentionally NOT compared directly — inner targets can
 * have different deployment names as long as the underlying model is the same.
 * Keep this in sync with RoundRobinTarget._validate_behavioral_consistency.
 */
function isCompatible(a: TargetInstance, b: TargetInstance): boolean {
  return (
    a.target_type === b.target_type &&
    effectiveUnderlyingModel(a) === effectiveUnderlyingModel(b) &&
    (a.temperature ?? null) === (b.temperature ?? null) &&
    (a.top_p ?? null) === (b.top_p ?? null)
  )
}

export default function CreateTargetDialog({ open, onClose, onCreated, existingTargets }: CreateTargetDialogProps) {
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

  // --- RoundRobin-specific state ---
  // The list of targets available for selection (fetched once when dialog opens).
  const [availableTargets, setAvailableTargets] = useState<TargetInstance[]>([])
  // Targets the user has picked for the RoundRobinTarget, with their weights.
  const [selectedInnerTargets, setSelectedInnerTargets] = useState<SelectedInnerTarget[]>([])

  const targetConfig = TARGET_TYPE_CONFIG[targetType]
  const isRoundRobin = targetConfig?.kind === 'roundrobin'
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

  // Fetch the available targets when the dialog opens with RoundRobin selected.
  // If the parent already passed targets, use those to avoid a redundant API call.
  useEffect(() => {
    if (!open || !isRoundRobin) return
    if (existingTargets && existingTargets.length > 0) {
      setAvailableTargets(existingTargets)
      return
    }
    let cancelled = false
    targetsApi.listTargets(200).then((res) => {
      if (!cancelled) setAvailableTargets(res.items)
    }).catch(() => {
      // Ignore fetch errors — the list will just be empty
    })
    return () => { cancelled = true }
  }, [open, isRoundRobin, existingTargets])

  // Compute which targets are eligible to be added next, based on compatibility
  // with the first selected target. We also exclude RoundRobinTargets (no nesting),
  // already-selected targets, and any target whose identifier_hash matches one already
  // selected (different registry names that resolve to the same backend config).
  const eligibleTargets = useMemo(() => {
    // Targets the user has already selected — exclude by registry name AND by hash so
    // aliases pointing at the same underlying endpoint don't show up as separate options.
    const selectedNames = new Set(selectedInnerTargets.map((t) => t.registryName))
    const selectedHashes = new Set(
      selectedInnerTargets
        .map((sel) => availableTargets.find((t) => t.target_registry_name === sel.registryName)?.identifier_hash)
        .filter((h): h is string => Boolean(h)),
    )
    const candidates = availableTargets.filter(
      (t) =>
        t.target_type !== 'RoundRobinTarget' &&
        !selectedNames.has(t.target_registry_name) &&
        !(t.identifier_hash && selectedHashes.has(t.identifier_hash)),
    )
    // If nothing is selected yet, all non-RRT candidates are eligible
    if (selectedInnerTargets.length === 0) return candidates
    // Otherwise, filter to only targets compatible with the first one
    const firstSelected = availableTargets.find(
      (t) => t.target_registry_name === selectedInnerTargets[0].registryName,
    )
    if (!firstSelected) return candidates
    return candidates.filter((t) => isCompatible(firstSelected, t))
  }, [availableTargets, selectedInnerTargets])

  const addInnerTarget = (registryName: string) => {
    setSelectedInnerTargets((prev) => [...prev, { registryName, weightInput: '1' }])
  }

  const removeInnerTarget = (registryName: string) => {
    setSelectedInnerTargets((prev) => prev.filter((t) => t.registryName !== registryName))
  }

  const setInnerTargetWeightInput = (registryName: string, weightInput: string) => {
    setSelectedInnerTargets((prev) =>
      prev.map((t) => (t.registryName === registryName ? { ...t, weightInput } : t)),
    )
  }

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
    setSelectedInnerTargets([])
  }

  const handleClose = () => {
    resetForm()
    onClose()
  }

  const handleSubmit = async () => {
    // For RoundRobinTarget, validation is different: we need ≥2 selected targets, not endpoint
    if (isRoundRobin) {
      if (selectedInnerTargets.length < 2) {
        setError('Please select at least 2 targets.')
        return
      }
      // Re-validate every weight at submit time. The Submit button's disabled
      // state usually catches this, but pressing Enter inside the weight input
      // triggers the form's onSubmit handler, bypassing the button.
      const parsedWeights: number[] = []
      for (const t of selectedInnerTargets) {
        const parsed = parseWeight(t.weightInput)
        if (!parsed.ok) {
          setError(`Invalid weight for "${t.registryName}": ${parsed.error}.`)
          return
        }
        parsedWeights.push(parsed.value)
      }

      setSubmitting(true)
      setError(null)

      try {
        await targetsApi.createTarget({
          type: 'RoundRobinTarget',
          params: {
            target_registry_names: selectedInnerTargets.map((t) => t.registryName),
            weights: parsedWeights,
          },
        })
        resetForm()
        onCreated()
      } catch (err) {
        // Surface the backend's RFC 7807 `detail` (e.g. RoundRobinTarget validation
        // messages) rather than the generic axios "Request failed with status code 400".
        setError(toApiError(err).detail)
      } finally {
        setSubmitting(false)
      }
      return
    }

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
      // Surface the backend's RFC 7807 `detail` (e.g. RoundRobinTarget validation
      // messages) rather than the generic axios "Request failed with status code 400".
      setError(toApiError(err).detail)
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

              {/* === RoundRobinTarget form: select existing targets === */}
              {isRoundRobin && (
                <>
                  <Field label="Add Target">
                    <Select
                      value=""
                      onChange={(_, data) => {
                        if (data.value) addInnerTarget(data.value)
                      }}
                      disabled={eligibleTargets.length === 0}
                    >
                      <option value="">
                        {eligibleTargets.length === 0
                          ? 'No compatible targets available'
                          : 'Select a target to add...'}
                      </option>
                      {eligibleTargets.map((t) => (
                        <option key={t.target_registry_name} value={t.target_registry_name}>
                          {t.target_registry_name} — {t.target_type}
                          {t.model_name ? ` (${t.model_name})` : ''}
                        </option>
                      ))}
                    </Select>
                  </Field>

                  {selectedInnerTargets.length > 0 && (
                    <div>
                      <Label size="small" style={{ marginBottom: '4px', display: 'block' }}>
                        Selected Targets ({selectedInnerTargets.length})
                        {selectedInnerTargets.length < 2 && (
                          <Text size={200} style={{ color: tokens.colorPaletteRedForeground1, marginLeft: '8px' }}>
                            — need at least 2
                          </Text>
                        )}
                      </Label>
                      <div className={styles.selectedTargetsList}>
                        {selectedInnerTargets.map((sel) => {
                          const target = availableTargets.find(
                            (t) => t.target_registry_name === sel.registryName,
                          )
                          const weightParse = parseWeight(sel.weightInput)
                          const weightError = weightParse.ok ? null : weightParse.error
                          return (
                            <div key={sel.registryName} className={styles.selectedTargetRow}>
                              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                  <Text size={200} style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    {target?.target_registry_name ?? sel.registryName}
                                    {target?.model_name ? ` (${target.model_name})` : ''}
                                  </Text>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                    <Label size="small">Weight:</Label>
                                    <Input
                                      type="number"
                                      value={sel.weightInput}
                                      min="1"
                                      max={String(MAX_WEIGHT)}
                                      step="1"
                                      aria-invalid={weightError !== null}
                                      aria-label={`Weight for ${sel.registryName}`}
                                      style={{ width: '70px' }}
                                      onChange={(_, data) =>
                                        setInnerTargetWeightInput(sel.registryName, data.value)
                                      }
                                    />
                                    <Button
                                      appearance="subtle"
                                      size="small"
                                      icon={<DeleteRegular />}
                                      aria-label={`Remove ${sel.registryName}`}
                                      onClick={() => removeInnerTarget(sel.registryName)}
                                    />
                                  </div>
                                </div>
                                {weightError && (
                                  <Text
                                    size={100}
                                    role="alert"
                                    style={{
                                      color: tokens.colorPaletteRedForeground1,
                                      marginTop: '2px',
                                      alignSelf: 'flex-end',
                                    }}
                                  >
                                    {weightError}
                                  </Text>
                                )}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* === Standard target form fields (hidden for RoundRobin) === */}
              {!isRoundRobin && (
                <>
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

              {/* Close the !isRoundRobin conditional wrapper */}
                </>
              )}

              {!isRoundRobin && (
              <Label size="small" style={{ color: tokens.colorNeutralForeground3 }}>
                Targets can also be auto-populated by adding an initializer (e.g. <code>airt</code>) to your{' '}
                <code>~/.pyrit/.pyrit_conf</code> file, which reads endpoints from your <code>.env</code> and{' '}
                <code>.env.local</code> files. See{' '}
                <Link href="https://github.com/microsoft/PyRIT/blob/main/.pyrit_conf_example" target="_blank" inline>
                  .pyrit_conf_example
                </Link>.
              </Label>
              )}
            </form>
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={handleClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              appearance="primary"
              onClick={handleSubmit}
              disabled={
                submitting ||
                !targetType ||
                (isRoundRobin
                  ? selectedInnerTargets.length < 2 ||
                    selectedInnerTargets.some((t) => !parseWeight(t.weightInput).ok)
                  : !endpoint || showEntraEndpointError)
              }
            >
              {submitting ? 'Creating...' : 'Create Target'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
