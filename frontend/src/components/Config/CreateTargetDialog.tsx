import { useState, useEffect, useMemo } from 'react'
import {
  Dialog,
  DialogSurface,
  DialogTitle,
  DialogBody,
  DialogContent,
  DialogActions,
  Button,
  Dropdown,
  Input,
  Label,
  Link,
  Option,
  Radio,
  RadioGroup,
  Select,
  Spinner,
  Switch,
  Text,
  tokens,
  Field,
  MessageBar,
  MessageBarBody,
  Tooltip,
} from '@fluentui/react-components'
import { DeleteRegular } from '@fluentui/react-icons'

import ParameterField from '@/components/Parameters/ParameterField'
import {
  buildParametersFromForm,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import { targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { TargetInstance, TargetTypeEntry } from '@/types'
import {
  targetIdentifierHash,
  targetModelName,
  targetType as getTargetType,
  targetUnderlyingModelName,
} from '@/utils/targetIdentity'
import { useCreateTargetDialogStyles } from './CreateTargetDialog.styles'
import {
  canConfigureTargetType,
  getTargetParameterPolicy,
  isMetadataDrivenTargetParameter,
} from './targetParameterPolicy'
import { MAX_WEIGHT, parseWeight } from './weightValidation'

const FALLBACK_TARGET_TYPES = [
  'OpenAIChatTarget',
  'OpenAICompletionTarget',
  'OpenAIImageTarget',
  'OpenAIVideoTarget',
  'OpenAITTSTarget',
  'OpenAIResponseTarget',
  'AzureMLChatTarget',
  'RoundRobinTarget',
]

const FALLBACK_IDENTITY_TARGET_TYPES = new Set([
  'OpenAIChatTarget',
  'OpenAICompletionTarget',
  'OpenAIImageTarget',
  'OpenAIVideoTarget',
  'OpenAITTSTarget',
  'OpenAIResponseTarget',
  'AzureMLChatTarget',
])

const TARGET_DISPLAY_NAMES: Record<string, string> = {
  AzureMLChatTarget: 'Azure Machine Learning chat',
  OpenAIChatTarget: 'OpenAI chat',
  OpenAICompletionTarget: 'OpenAI text completion',
  OpenAIImageTarget: 'OpenAI image',
  OpenAIResponseTarget: 'OpenAI Responses API',
  OpenAITTSTarget: 'OpenAI text to speech',
  OpenAIVideoTarget: 'OpenAI video',
  RoundRobinTarget: 'Weighted round robin',
}

const FALLBACK_TARGET_TYPE_ENTRIES: TargetTypeEntry[] = FALLBACK_TARGET_TYPES.map((targetType) => ({
  target_type: targetType,
  parameters: [],
  supported_auth_modes: [],
  description: null,
}))

type AuthMode = 'api_key' | 'identity'
type TypeMetadataStatus = 'loading' | 'loaded' | 'error'

function getTargetDisplayName(targetType: string): string {
  return TARGET_DISPLAY_NAMES[targetType] ?? targetType
}

function getAuthDescription(authModes: TargetTypeEntry['supported_auth_modes']): string | null {
  if (authModes.length === 0) return null

  const labels = authModes.map((mode) => (
    mode === 'identity' ? 'Microsoft Entra ID' : 'API key'
  ))
  return `Supported authentication: ${labels.join(' or ')}`
}

/**
 * Fallback for identity-based auth while registry metadata is unavailable.
 */
function defaultSupportsIdentity(targetType: string): boolean {
  return FALLBACK_IDENTITY_TARGET_TYPES.has(targetType)
}

function getParameterLabel(name: string): string {
  return name
    .split('_')
    .map((word) => word.length === 1 ? word.toUpperCase() : `${word[0].toUpperCase()}${word.slice(1)}`)
    .join(' ')
}

function isParameterValueSet(value: ParameterFormValue | undefined): boolean {
  if (typeof value === 'string') {
    return value.trim().length > 0
  }
  if (Array.isArray(value)) {
    return value.length > 0
  }
  return value !== undefined && value.type.length > 0
}

// Mirrors backend's hostname-suffix check (list in target_service.py).
// The backend still does the check and will reject unsupported endpoints, but this allows us to show a warning in the UI if the user selects identity-based authentication with a non-Azure OpenAI endpoint.
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
  return targetUnderlyingModelName(t) || targetModelName(t) || null
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
    getTargetType(a) === getTargetType(b) &&
    effectiveUnderlyingModel(a) === effectiveUnderlyingModel(b) &&
    (a.identifier.temperature ?? null) === (b.identifier.temperature ?? null) &&
    (a.identifier.top_p ?? null) === (b.identifier.top_p ?? null)
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
  const [parameterValues, setParameterValues] = useState<Record<string, ParameterFormValue>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{
    targetType?: string
    endpoint?: string
    modelName?: string
    underlyingModel?: string
    apiKey?: string
  }>({})

  // --- RoundRobin-specific state ---
  // The list of targets available for selection (fetched once when dialog opens).
  const [availableTargets, setAvailableTargets] = useState<TargetInstance[]>([])
  // Targets the user has picked for the RoundRobinTarget, with their weights.
  const [selectedInnerTargets, setSelectedInnerTargets] = useState<SelectedInnerTarget[]>([])

  // --- Target type metadata state ---
  // Available target types + their auth facts, fetched from the backend registry.
  const [targetTypeEntries, setTargetTypeEntries] = useState<TargetTypeEntry[]>([])
  const [typeMetadataStatus, setTypeMetadataStatus] = useState<TypeMetadataStatus>('loading')
  const targetTypeByName = useMemo(
    () => new Map(targetTypeEntries.map((entry) => [entry.target_type, entry])),
    [targetTypeEntries],
  )

  // Reset the type metadata back to its loading state whenever the dialog is
  // opened, so a stale error/entries from a previous session isn't shown while
  // the refetch is in flight. Adjusted during render (rather than in the effect
  // below) to avoid a cascading render.
  const [seenOpen, setSeenOpen] = useState(open)
  if (open !== seenOpen) {
    setSeenOpen(open)
    if (open) {
      setTargetTypeEntries([])
      setTypeMetadataStatus('loading')
    }
  }

  // Fetch the target type metadata once when the dialog opens. The registry is
  // the authority on which types exist and which auth modes they support.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    targetsApi.listTargetTypes()
      .then((res) => {
        if (!cancelled) {
          setTargetTypeEntries(res.items)
          setTypeMetadataStatus('loaded')
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTargetTypeEntries([])
          setTypeMetadataStatus('error')
        }
      })
    return () => { cancelled = true }
  }, [open])

  const registeredTargetTypeOptions = useMemo(() => {
    return targetTypeEntries.filter(canConfigureTargetType)
  }, [targetTypeEntries])
  const typeMetadataAvailable = registeredTargetTypeOptions.length > 0
  const typeMetadataUnavailable = typeMetadataStatus !== 'loading' && !typeMetadataAvailable
  const targetTypeOptions = typeMetadataAvailable
    ? registeredTargetTypeOptions
    : FALLBACK_TARGET_TYPE_ENTRIES

  const isRoundRobin = targetType === 'RoundRobinTarget'
  const isAzureML = targetType === 'AzureMLChatTarget'
  const isOpenAi = targetType.startsWith('OpenAI') || targetType === 'RealtimeTarget'
  const targetTypeEntry = targetTypeByName.get(targetType)
  const metadataDrivenParameters = useMemo(
    () => targetTypeEntry?.parameters.filter(
      (parameter) => isMetadataDrivenTargetParameter(targetType, parameter),
    ) ?? [],
    [targetType, targetTypeEntry],
  )
  const requiredMetadataParameters = metadataDrivenParameters.filter((parameter) => parameter.required)
  const optionalMetadataParameters = metadataDrivenParameters.filter((parameter) => !parameter.required)
  const requiredMetadataParameterMissing = requiredMetadataParameters.some(
    (parameter) => !isParameterValueSet(parameterValues[parameter.name]),
  )
  const endpointParameter = targetTypeEntry?.parameters.find((parameter) => parameter.name === 'endpoint')
  const modelNameParameter = targetTypeEntry?.parameters.find((parameter) => parameter.name === 'model_name')
  const underlyingModelParameter = targetTypeEntry?.parameters.find(
    (parameter) => parameter.name === 'underlying_model' || parameter.name === 'underlying_model_name',
  )
  const apiKeyParameter = targetTypeEntry?.parameters.find((parameter) => parameter.name === 'api_key')
  const customFunctionsParameter = targetTypeEntry?.parameters.find(
    (parameter) => parameter.name === 'custom_functions',
  )
  const customFunctionsReason = customFunctionsParameter
    ? getTargetParameterPolicy(targetType, customFunctionsParameter.name)?.reason
    : null
  const metadataUnavailableForSelection = targetType !== '' && !targetTypeEntry
  const hasEndpointField = endpointParameter !== undefined
    || (metadataUnavailableForSelection && !isRoundRobin)
  const hasModelNameField = modelNameParameter !== undefined
    || (metadataUnavailableForSelection && !isRoundRobin)
  const hasUnderlyingModelField = underlyingModelParameter !== undefined
    || (metadataUnavailableForSelection && !isRoundRobin)
  const hasApiKeyField = apiKeyParameter !== undefined
    || (metadataUnavailableForSelection && !isRoundRobin)
  const selectedTargetDisplayName = getTargetDisplayName(targetType)
  const selectedTargetAuthDescription = targetTypeEntry
    ? getAuthDescription(targetTypeEntry.supported_auth_modes)
    : null
  const supportsIdentity = targetTypeEntry
    ? targetTypeEntry.supported_auth_modes.includes('identity')
    : defaultSupportsIdentity(targetType)
  const showAuthField = targetType !== '' && supportsIdentity
  const isIdentity = showAuthField && authMode === 'identity'
  const endpointRequired = Boolean(endpointParameter?.required)
    || (isIdentity && hasEndpointField)
    || (metadataUnavailableForSelection && hasEndpointField)
  const modelNameRequired = Boolean(modelNameParameter?.required)
  const underlyingModelRequired = Boolean(underlyingModelParameter?.required)
  const apiKeyRequired = Boolean(apiKeyParameter?.required) && !isIdentity
  const identityEndpointError: string | null = (() => {
    if (!isIdentity || endpoint === '') return null
    if (isOpenAi && !isAzureOpenAiEndpoint(endpoint)) {
      return 'Identity-based auth only works with Azure OpenAI / AI Foundry endpoints (for example, *.openai.azure.com or *.ai.azure.com).'
    }
    if (isAzureML && !isAzureMlEndpoint(endpoint)) {
      return 'Identity-based auth for AzureMLChatTarget only works with Azure ML managed online endpoints (for example, *.inference.ml.azure.com).'
    }
    return null
  })()
  const showIdentityEndpointError = identityEndpointError !== null

  // Fetch the available targets when the dialog opens with RoundRobin selected.
  // If the parent already passed targets, derive availableTargets from them
  // directly via the "adjust state during render" pattern to avoid an effect.
  const [seenExistingTargets, setSeenExistingTargets] = useState<TargetInstance[] | null>(null)
  if (
    open
    && isRoundRobin
    && existingTargets
    && existingTargets.length > 0
    && existingTargets !== seenExistingTargets
  ) {
    setSeenExistingTargets(existingTargets)
    setAvailableTargets(existingTargets)
  }

  useEffect(() => {
    if (!open || !isRoundRobin) return
    if (existingTargets && existingTargets.length > 0) return
    let cancelled = false
    targetsApi.listTargets(200)
      .then((res) => {
        if (!cancelled) setAvailableTargets(res.items)
      })
      .catch(() => {
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
        .map((sel) => {
          const t = availableTargets.find((t) => t.target_registry_name === sel.registryName)
          return t ? targetIdentifierHash(t) : null
        })
        .filter((h): h is string => Boolean(h)),
    )
    const candidates = availableTargets.filter(
      (t) =>
        getTargetType(t) !== 'RoundRobinTarget' &&
        !selectedNames.has(t.target_registry_name) &&
        !(targetIdentifierHash(t) && selectedHashes.has(targetIdentifierHash(t)!)),
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
    setParameterValues({})
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
            targets: selectedInnerTargets.map((t) => t.registryName),
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

    const errors: {
      targetType?: string
      endpoint?: string
      modelName?: string
      underlyingModel?: string
      apiKey?: string
    } = {}
    if (!targetType) errors.targetType = 'Please select a target type'
    if (endpointRequired && !endpoint) errors.endpoint = 'Please provide an endpoint URL'
    if (modelNameRequired && !modelName) errors.modelName = 'Please provide a model name'
    if (underlyingModelRequired && !underlyingModel) {
      errors.underlyingModel = 'Please provide the underlying model'
    }
    if (apiKeyRequired && !apiKey) errors.apiKey = 'Please provide an API key'
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setFieldErrors({})

    const metadataParams = buildParametersFromForm(metadataDrivenParameters, parameterValues)
    if (!metadataParams.ok) {
      setError(metadataParams.error)
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      const params: Record<string, unknown> = { ...(metadataParams.parameters ?? {}) }
      if (hasEndpointField && endpoint) params.endpoint = endpoint
      if (hasModelNameField && modelName) params.model_name = modelName
      if (hasApiKeyField && !isIdentity && apiKey) params.api_key = apiKey

      if (
        (underlyingModelRequired || hasDifferentUnderlying)
        && underlyingModel
        && hasUnderlyingModelField
      ) {
        params[underlyingModelParameter?.name ?? 'underlying_model'] = underlyingModel
      }

      await targetsApi.createTarget({
        type: targetType,
        params,
        ...(isIdentity ? { auth_mode: 'identity' as const } : {}),
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
      <DialogSurface className={styles.dialogSurface}>
        <DialogBody>
          <DialogTitle>Create New Target</DialogTitle>
          <DialogContent className={styles.dialogContent}>
            <form
              className={styles.form}
              data-testid="create-target-form"
              onSubmit={(e) => { e.preventDefault(); handleSubmit() }}
            >
              {error && (
                <MessageBar intent="error">
                  <MessageBarBody>{error}</MessageBarBody>
                </MessageBar>
              )}

              {typeMetadataStatus === 'loading' && (
                <Spinner size="tiny" label="Loading target details..." labelPosition="after" />
              )}

              {typeMetadataUnavailable && (
                <MessageBar intent="warning">
                  <MessageBarBody>
                    Target details could not be loaded. You can still select a supported target type,
                    but its registry description and authentication options are unavailable.
                  </MessageBarBody>
                </MessageBar>
              )}

              <Field
                className={styles.formField}
                label="Target Type"
                hint="Each option shows what the target does, its implementation class, and how it authenticates."
                required
                validationMessage={fieldErrors.targetType}
                validationState={fieldErrors.targetType ? 'error' : 'none'}
              >
                <Dropdown
                  aria-label="Target Type"
                  className={styles.fullWidthSelect}
                  listbox={{ className: styles.targetTypeListbox }}
                  placeholder="Select a target type"
                  positioning={{ matchTargetSize: 'width' }}
                  selectedOptions={targetType ? [targetType] : []}
                  value={targetType ? selectedTargetDisplayName : ''}
                  onOptionSelect={(_, data) => {
                    const next = data.optionValue
                    if (!next) return
                    setTargetType(next)
                    setParameterValues({})
                    const nextEntry = targetTypeByName.get(next)
                    const nextSupportsIdentity = nextEntry
                      ? nextEntry.supported_auth_modes.includes('identity')
                      : defaultSupportsIdentity(next)
                    if (!nextSupportsIdentity) {
                      setAuthMode('api_key')
                    }
                  }}
                >
                  {targetTypeOptions.map((entry) => {
                    const displayName = getTargetDisplayName(entry.target_type)
                    const authDescription = getAuthDescription(entry.supported_auth_modes)
                    const accessibleDescription = [
                      displayName,
                      entry.description,
                      `Implementation: ${entry.target_type}`,
                      authDescription,
                    ].filter((value): value is string => Boolean(value)).join('. ')

                    return (
                      <Option
                        aria-label={accessibleDescription}
                        key={entry.target_type}
                        text={displayName}
                        value={entry.target_type}
                      >
                        <div className={styles.targetTypeOption}>
                          <div className={styles.targetTypeOptionHeader}>
                            <Text weight="semibold">{displayName}</Text>
                            <code className={styles.targetTypeIdentifier}>{entry.target_type}</code>
                          </div>
                          {entry.description && (
                            <Text size={200} className={styles.targetTypeDescription}>
                              {entry.description}
                            </Text>
                          )}
                          {authDescription && (
                            <Text size={200} className={styles.targetTypeAuth}>
                              {authDescription}
                            </Text>
                          )}
                        </div>
                      </Option>
                    )
                  })}
                </Dropdown>
              </Field>

              {targetType && (
                <section
                  aria-label="Selected target details"
                  aria-live="polite"
                  className={styles.selectedTargetDetails}
                >
                  <div className={styles.targetTypeOptionHeader}>
                    <Text weight="semibold">{selectedTargetDisplayName}</Text>
                    <code className={styles.targetTypeIdentifier}>{targetType}</code>
                  </div>
                  {targetTypeEntry?.description ? (
                    <Text size={200} className={styles.targetTypeDescription}>
                      {targetTypeEntry.description}
                    </Text>
                  ) : (
                    <Text size={200} className={styles.targetTypeDescription}>
                      Registry details are unavailable for this target.
                    </Text>
                  )}
                  {selectedTargetAuthDescription && (
                    <Text size={200} className={styles.targetTypeAuth}>
                      {selectedTargetAuthDescription}
                    </Text>
                  )}
                </section>
              )}

              {/* === RoundRobinTarget form: select existing targets === */}
              {isRoundRobin && (
                <>
                  <Field className={styles.formField} label="Add Target">
                    <Select
                      className={styles.fullWidthSelect}
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
                          {t.target_registry_name} — {getTargetType(t)}
                          {targetModelName(t) ? ` (${targetModelName(t)})` : ''}
                        </option>
                      ))}
                    </Select>
                  </Field>

                  {selectedInnerTargets.length > 0 && (
                    <div className={styles.selectedTargetsSection}>
                      <Label size="small" className={styles.selectedTargetsLabel}>
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
                          const selectedTargetLabel = `${target?.target_registry_name ?? sel.registryName}${
                            target && targetModelName(target) ? ` (${targetModelName(target)})` : ''
                          }`
                          const weightParse = parseWeight(sel.weightInput)
                          const weightError = weightParse.ok ? null : weightParse.error
                          return (
                            <div key={sel.registryName} className={styles.selectedTargetRow}>
                              <Tooltip
                                content={<span className={styles.targetNameTooltip}>{selectedTargetLabel}</span>}
                                relationship="description"
                              >
                                <Text
                                  as="span"
                                  size={200}
                                  className={styles.selectedTargetName}
                                  tabIndex={0}
                                  aria-label={`Selected target: ${selectedTargetLabel}`}
                                >
                                  {selectedTargetLabel}
                                </Text>
                              </Tooltip>
                              <div className={styles.selectedTargetControlGroup}>
                                <div className={styles.selectedTargetControls}>
                                  <Label size="small">Weight:</Label>
                                  <Input
                                    className={styles.weightInput}
                                    type="number"
                                    value={sel.weightInput}
                                    min="1"
                                    max={String(MAX_WEIGHT)}
                                    step="1"
                                    aria-invalid={weightError !== null}
                                    aria-label={`Weight for ${sel.registryName}`}
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
                                    className={styles.touchTarget}
                                  />
                                </div>
                                {weightError && (
                                  <Text
                                    size={100}
                                    role="alert"
                                    className={styles.weightError}
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
                  {hasEndpointField && (
                    <Field
                      label="Endpoint URL"
                      hint={endpointParameter?.description || undefined}
                      required={endpointRequired}
                      validationMessage={fieldErrors.endpoint}
                      validationState={fieldErrors.endpoint ? 'error' : 'none'}
                    >
                      <Input
                        placeholder={isAzureML
                          ? 'https://your-model.region.inference.ml.azure.com/score'
                          : isOpenAi
                            ? 'https://your-resource.openai.azure.com/'
                            : 'https://example.com/'}
                        value={endpoint}
                        onChange={(_, data) => setEndpoint(data.value)}
                      />
                    </Field>
                  )}

                  {hasModelNameField && (
                    <Field
                      label="Model / Deployment Name"
                      hint={modelNameParameter?.description || undefined}
                      required={modelNameRequired}
                      validationMessage={fieldErrors.modelName}
                      validationState={fieldErrors.modelName ? 'error' : 'none'}
                    >
                      <Input
                        placeholder={isAzureML
                          ? 'e.g. Llama-3.2-3B-Instruct'
                          : 'e.g. gpt-4o, my-deployment'}
                        value={modelName}
                        onChange={(_, data) => setModelName(data.value)}
                      />
                    </Field>
                  )}

                  {hasUnderlyingModelField && !underlyingModelRequired && (
                    <div>
                      <Switch
                        checked={hasDifferentUnderlying}
                        onChange={(_, data) => {
                          setHasDifferentUnderlying(data.checked)
                          if (!data.checked) setUnderlyingModel('')
                        }}
                        label="Underlying model differs from deployment name"
                      />
                      <Text
                        size={200}
                        style={{
                          color: tokens.colorNeutralForeground3,
                          display: 'block',
                          marginTop: '2px',
                        }}
                      >
                        On Azure, the deployment name may differ from the actual model.
                      </Text>
                    </div>
                  )}

                  {hasUnderlyingModelField && (underlyingModelRequired || hasDifferentUnderlying) && (
                    <Field
                      label="Underlying Model"
                      hint={underlyingModelParameter?.description || undefined}
                      required={underlyingModelRequired}
                      validationMessage={fieldErrors.underlyingModel}
                      validationState={fieldErrors.underlyingModel ? 'error' : 'none'}
                    >
                      <Input
                        placeholder="e.g. gpt-4o-2024-08-06"
                        value={underlyingModel}
                        onChange={(_, data) => setUnderlyingModel(data.value)}
                      />
                    </Field>
                  )}

                  {requiredMetadataParameters.map((parameter) => (
                    <ParameterField
                      key={parameter.name}
                      parameter={parameter}
                      value={parameterValues[parameter.name] ?? ''}
                      disabled={submitting}
                      label={getParameterLabel(parameter.name)}
                      showDefaultHint
                      allowEmptyList
                      testIdPrefix="target-param"
                      onChange={(name, value) => setParameterValues((current) => ({
                        ...current,
                        [name]: value,
                      }))}
                    />
                  ))}

                  {showAuthField && (
                    <Field label="Authentication">
                      <RadioGroup
                        value={authMode}
                        onChange={(_, data) => {
                          const next = data.value as AuthMode
                          setAuthMode(next)
                          if (next === 'identity') setApiKey('')
                        }}
                      >
                        <Radio value="api_key" label="API Key" />
                        <Radio value="identity" label="Identity-based (Microsoft Entra ID)" />
                      </RadioGroup>
                    </Field>
                  )}

                  {showIdentityEndpointError && (
                    <MessageBar intent="error" className={styles.warningMessage}>
                      <MessageBarBody className={styles.warningMessageBody}>
                        {identityEndpointError}
                      </MessageBarBody>
                    </MessageBar>
                  )}

                  {hasApiKeyField && !isIdentity && (
                    <Field
                      label="API Key"
                      hint={apiKeyParameter?.description || undefined}
                      required={apiKeyRequired}
                      validationMessage={fieldErrors.apiKey}
                      validationState={fieldErrors.apiKey ? 'error' : 'none'}
                    >
                      <Input
                        type="password"
                        placeholder="API key (stored in memory only)"
                        value={apiKey}
                        onChange={(_, data) => setApiKey(data.value)}
                      />
                    </Field>
                  )}

                  {(optionalMetadataParameters.length > 0 || customFunctionsReason) && (
                    <details className={styles.advancedSettings}>
                      <summary className={styles.advancedSettingsSummary}>
                        Advanced settings
                      </summary>
                      <div className={styles.advancedSettingsFields}>
                        {optionalMetadataParameters.map((parameter) => (
                          <ParameterField
                            key={parameter.name}
                            parameter={parameter}
                            value={parameterValues[parameter.name] ?? ''}
                            disabled={submitting}
                            label={getParameterLabel(parameter.name)}
                            showDefaultHint
                            allowEmptyList
                            testIdPrefix="target-param"
                            onChange={(name, value) => setParameterValues((current) => ({
                              ...current,
                              [name]: value,
                            }))}
                          />
                        ))}
                        {customFunctionsReason && (
                          <MessageBar intent="info">
                            <MessageBarBody>
                              <strong>Custom Functions:</strong> {customFunctionsReason}
                            </MessageBarBody>
                          </MessageBar>
                        )}
                      </div>
                    </details>
                  )}
                </>
              )}

              {!isRoundRobin && (
              <Label size="small" style={{ color: tokens.colorNeutralForeground3 }}>
                Targets can also be auto-populated by adding the <code>target</code> initializer to your{' '}
                <code>~/.pyrit/.pyrit_conf</code> file, which registers available prompt targets from endpoints in{' '}
                your <code>.env</code> and <code>.env.local</code> files. See{' '}
                <Link
                  href="https://github.com/microsoft/PyRIT/blob/main/.pyrit_conf_example"
                  target="_blank"
                  rel="noopener noreferrer"
                  inline
                >
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
                  : (endpointRequired && !endpoint) || showIdentityEndpointError)
                  || (modelNameRequired && !modelName)
                  || (underlyingModelRequired && !underlyingModel)
                  || (apiKeyRequired && !apiKey)
                  || requiredMetadataParameterMissing
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
