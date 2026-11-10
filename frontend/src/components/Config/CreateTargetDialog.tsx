import { useState, useEffect, useMemo, useRef } from 'react'
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
  MessageBarTitle,
  Tooltip,
} from '@fluentui/react-components'
import { DeleteRegular } from '@fluentui/react-icons'

import ParameterField from '@/components/Parameters/ParameterField'
import {
  buildParametersFromForm,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import { targetsApi } from '@/services/api'
import { isNotSaveableError, toApiError } from '@/services/errors'
import { listRegisteredTargets } from '@/services/targetRegistry'
import type { TargetAuthMode, TargetInstance, TargetTypeEntry } from '@/types'
import { suggestRegistryName, validateRegistryName } from '@/utils/registryName'
import { isGuiTarget } from '@/utils/targetVisibility'
import {
  targetIdentifierHash,
  targetModelName,
  targetType as getTargetType,
  targetUnderlyingModelName,
} from '@/utils/targetIdentity'
import { TARGET_ORIGIN_LABELS } from '@/utils/targetOrigin'
import { useCreateTargetDialogStyles } from './CreateTargetDialog.styles'
import {
  canConfigureTargetType,
  getTargetParameterPolicy,
  isMetadataDrivenTargetParameter,
} from './targetParameterPolicy'
import { MAX_WEIGHT, parseWeight } from './weightValidation'

const FALLBACK_TARGET_TYPES = [
  'GitHubCopilotTarget',
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

const FALLBACK_NO_KEY_TARGET_TYPES = new Set([
  'OpenAIChatTarget',
  'OpenAICompletionTarget',
  'OpenAIImageTarget',
  'OpenAIVideoTarget',
  'OpenAITTSTarget',
  'OpenAIResponseTarget',
])

const TARGET_DISPLAY_NAMES: Record<string, string> = {
  AzureMLChatTarget: 'Azure Machine Learning chat',
  GitHubCopilotTarget: 'GitHub Copilot',
  OpenAIChatTarget: 'OpenAI chat',
  OpenAICompletionTarget: 'OpenAI text completion',
  OpenAIImageTarget: 'OpenAI image',
  OpenAIResponseTarget: 'OpenAI Responses API',
  OpenAITTSTarget: 'OpenAI text to speech',
  OpenAIVideoTarget: 'OpenAI video',
  RoundRobinTarget: 'Weighted round robin',
}

const GITHUB_COPILOT_TOKEN_HINT =
  'Required unless GITHUB_TOKEN is configured for this CoPyRIT instance through its environment or loaded .env/.env.local files.'

const GITHUB_COPILOT_MODEL_HINT =
  'Model IDs are not validated when creating a target. An unrecognized ID may cause Copilot to use a different model. '
  + 'Verify the exact model ID is available to the GitHub account associated with this target\'s token.'

// Copilot has no endpoint or API-key parameter, even when registry metadata is unavailable.
const GITHUB_COPILOT_FALLBACK_ENTRY: TargetTypeEntry = {
  target_type: 'GitHubCopilotTarget',
  parameters: [
    {
      name: 'model_name',
      type_name: 'str',
      required: true,
      default: null,
      description: GITHUB_COPILOT_MODEL_HINT,
    },
    {
      name: 'github_token',
      type_name: 'str',
      required: false,
      default: null,
      sensitive: true,
      description: GITHUB_COPILOT_TOKEN_HINT,
    },
  ],
  supported_auth_modes: ['api_key'],
  description: 'Send text requests through the GitHub Copilot SDK.',
}

const FALLBACK_TARGET_TYPE_ENTRIES: TargetTypeEntry[] = FALLBACK_TARGET_TYPES.map((targetType) => (
  targetType === 'GitHubCopilotTarget' ? GITHUB_COPILOT_FALLBACK_ENTRY : {
    target_type: targetType,
    parameters: [],
    supported_auth_modes: [],
    description: null,
  }
))

type TypeMetadataStatus = 'loading' | 'loaded' | 'error'

const AUTH_MODE_LABELS: Record<TargetAuthMode, string> = {
  api_key: 'API key',
  identity: 'Microsoft Entra ID',
  none: 'No key',
}

// A server credential is sent as an API key, but the key stays on the server, so a saved target can use it.
type AuthChoice = TargetAuthMode | 'server_credential'

const SERVER_CREDENTIAL_LABEL = 'Server credential'

// A new target starts with the first of these that a saved target of its type can use.
const SAVEABLE_AUTH_MODE_PREFERENCE: readonly TargetAuthMode[] = ['identity', 'none']

const TEMPORARY_TARGET_NOTE = 'A Temporary target is gone after the backend restarts or is reinitialized.'

function getTargetDisplayName(targetType: string): string {
  return TARGET_DISPLAY_NAMES[targetType] ?? targetType
}

/** The text a suggested registry name is built from: the type's display name, or its class name without "Target". */
function getTargetNameLabel(targetType: string): string {
  return TARGET_DISPLAY_NAMES[targetType] ?? targetType.replace(/Target$/, '')
}

function getAuthDescription(entry: TargetTypeEntry, registryMetadataLoaded: boolean): string | null {
  if (entry.target_type === 'GitHubCopilotTarget') {
return 'Supported authentication: GitHub token'
  }
  const authModes = entry.supported_auth_modes
  if (authModes.length === 0) {
    // The built-in fallback entries also carry an empty list, so only registry data can say "no credentials".
    return registryMetadataLoaded ? 'No authentication required' : null
  }

  const labels = authModes.map((mode) => AUTH_MODE_LABELS[mode])
  if (entry.server_credentials?.length) labels.push(SERVER_CREDENTIAL_LABEL)
  return `Supported authentication: ${labels.join(' or ')}`
}

/**
 * Fallback for identity-based auth while registry metadata is unavailable.
 */
function defaultSupportsIdentity(targetType: string): boolean {
  return FALLBACK_IDENTITY_TARGET_TYPES.has(targetType)
}

function defaultSupportsNoKey(targetType: string): boolean {
  return FALLBACK_NO_KEY_TARGET_TYPES.has(targetType)
}

function getParameterLabel(name: string): string {
  return name
    .split('_')
    .map((word) => word.length === 1 ? word.toUpperCase() : `${word[0]?.toUpperCase() ?? ''}${word.slice(1)}`)
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

/** A save the backend refused, for the form values it refused. */
interface SaveRefusal {
  readonly formKey: string
  readonly reason: string
}

/** A name the backend said is taken. */
interface NameConflict {
  readonly name: string
  readonly detail: string
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
  // The Entra ID endpoint error waits until the endpoint field is left, so it doesn't flash while a host is typed.
  const [endpointFocused, setEndpointFocused] = useState(false)
  const [modelName, setModelName] = useState('')
  const [hasDifferentUnderlying, setHasDifferentUnderlying] = useState(false)
  const [underlyingModel, setUnderlyingModel] = useState('')
  // The auth mode the user picked; until then the dialog uses the default for the selected type.
  const [chosenAuthMode, setChosenAuthMode] = useState<AuthChoice | null>(null)
  const [serverCredentialName, setServerCredentialName] = useState('')
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
  const [registryName, setRegistryName] = useState('')
  // Until the user edits the name, the field shows a suggestion instead.
  const [nameEdited, setNameEdited] = useState(false)
  const [nameConflict, setNameConflict] = useState<NameConflict | null>(null)
  const [saveRefusal, setSaveRefusal] = useState<SaveRefusal | null>(null)
  const nameInputRef = useRef<HTMLInputElement>(null)

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
  const isGitHubCopilot = targetType === 'GitHubCopilotTarget'
  const isOpenAi = targetType.startsWith('OpenAI') || targetType === 'RealtimeTarget'
  const targetTypeEntry = targetTypeByName.get(targetType)
    ?? (isGitHubCopilot ? GITHUB_COPILOT_FALLBACK_ENTRY : undefined)
  const metadataDrivenParameters = useMemo(
    () => targetTypeEntry?.parameters.filter(
      (parameter) => isMetadataDrivenTargetParameter(targetType, parameter),
    ) ?? [],
    [targetType, targetTypeEntry],
  )
  const requiredMetadataParameters = metadataDrivenParameters.filter((parameter) => parameter.required)
  const githubTokenParameter = isGitHubCopilot
    ? metadataDrivenParameters.find((parameter) => parameter.name === 'github_token' && !parameter.required)
    : undefined
  const optionalMetadataParameters = metadataDrivenParameters.filter(
    (parameter) => !parameter.required && parameter !== githubTokenParameter,
  )
  const requiredMetadataParameterMissing = requiredMetadataParameters.some(
    (parameter) => !isParameterValueSet(parameterValues[parameter.name]),
  )
  const parameterByName = useMemo(
    () => new Map(targetTypeEntry?.parameters.map((parameter) => [parameter.name, parameter]) ?? []),
    [targetTypeEntry],
  )
  const endpointParameter = parameterByName.get('endpoint')
  const modelNameParameter = parameterByName.get('model_name')
  const underlyingModelParameter = parameterByName.get('underlying_model')
    ?? parameterByName.get('underlying_model_name')
  const apiKeyParameter = parameterByName.get('api_key')
  const customFunctionsParameter = parameterByName.get('custom_functions')
  const customFunctionsReason = customFunctionsParameter
    ? getTargetParameterPolicy(targetType, customFunctionsParameter.name)?.reason
    : null
  const metadataUnavailableForSelection = targetType !== '' && !targetTypeEntry
  const hasField = (name: string) => parameterByName.has(name)
    || (metadataUnavailableForSelection && !isRoundRobin)
  const hasEndpointField = hasField('endpoint')
  const hasModelNameField = hasField('model_name')
  const hasUnderlyingModelField = hasField('underlying_model') || hasField('underlying_model_name')
  const hasApiKeyField = hasField('api_key')
  const selectedTargetDisplayName = getTargetDisplayName(targetType)
  const selectedTargetAuthDescription = targetTypeEntry
    ? getAuthDescription(targetTypeEntry, typeMetadataAvailable)
    : null
  const supportsIdentity = targetTypeEntry
    ? targetTypeEntry.supported_auth_modes.includes('identity')
    : defaultSupportsIdentity(targetType)
  const supportsNoKey = targetTypeEntry
    ? targetTypeEntry.supported_auth_modes.includes('none')
    : defaultSupportsNoKey(targetType)
  const serverCredentials = targetTypeEntry?.server_credentials ?? []
  const supportsServerCredential = serverCredentials.length > 0
  const showAuthField = targetType !== '' && (supportsIdentity || supportsNoKey || supportsServerCredential)
  // Default to a mode a saved target of this type can use. It is derived, so it also applies when the type's
  // details arrive after the type was chosen.
  const defaultAuthMode = SAVEABLE_AUTH_MODE_PREFERENCE.find(
    (mode) => targetTypeEntry?.saveable_auth_modes?.includes(mode),
  ) ?? 'api_key'
  const chosenAuthModeSupported = chosenAuthMode === 'api_key'
    || (chosenAuthMode === 'identity' && supportsIdentity)
    || (chosenAuthMode === 'none' && supportsNoKey)
    || (chosenAuthMode === 'server_credential' && supportsServerCredential)
  const authMode: AuthChoice = chosenAuthMode !== null && chosenAuthModeSupported ? chosenAuthMode : defaultAuthMode
  const isIdentity = showAuthField && authMode === 'identity'
  const isNoKey = showAuthField && authMode === 'none'
  // The server only sends a credential's key to that credential's endpoint, so the endpoint follows the choice.
  const serverCredential = showAuthField && authMode === 'server_credential'
    ? serverCredentials.find((credential) => credential.name === serverCredentialName) ?? serverCredentials[0]
    : undefined
  const isServerCredential = serverCredential !== undefined
  const requestEndpoint = serverCredential?.endpoint ?? endpoint
  const endpointRequired = hasEndpointField
  // Without the type's details, still require the model every OpenAI type needs. The suggested name then includes
  // it, which keeps it off names such as `openai_chat` that startup initializers use.
  const modelNameRequired = modelNameParameter
    ? Boolean(modelNameParameter.required)
    : metadataUnavailableForSelection && isOpenAi
  const underlyingModelRequired = Boolean(underlyingModelParameter?.required)
  const apiKeyRequired = Boolean(apiKeyParameter?.required) && !isIdentity && !isNoKey && !isServerCredential
  const identityEndpointError: string | null = (() => {
    if (!isIdentity || endpoint === '') return null
    if (isOpenAi && !isAzureOpenAiEndpoint(endpoint)) {
      const noKeyHint = supportsNoKey ? ' For a server that needs no key, such as a local one, choose No key.' : ''
      return 'Identity-based auth only works with Azure OpenAI / AI Foundry endpoints (for example, *.openai.azure.com or *.ai.azure.com).'
        + noKeyHint
    }
    if (isAzureML && !isAzureMlEndpoint(endpoint)) {
      return 'Identity-based auth for AzureMLChatTarget only works with Azure ML managed online endpoints (for example, *.inference.ml.azure.com).'
    }
    return null
  })()
  const showIdentityEndpointError = identityEndpointError !== null
  // The parent's list is current; the round-robin list is a snapshot from when it was fetched.
  const takenNames = useMemo(
    () => new Set((existingTargets ?? availableTargets).map((t) => t.target_registry_name)),
    [existingTargets, availableTargets],
  )
  // Until edited, the name follows the type and model. That tells saved targets apart and leaves names such as
  // `openai_chat` to the startup initializers, whose target would otherwise stop a saved one of that name restoring.
  const suggestedName = targetType
    ? suggestRegistryName(getTargetNameLabel(targetType), takenNames, hasModelNameField ? modelName : '')
    : ''
  const nameValue = nameEdited ? registryName : suggestedName
  // The server stays authoritative; this only catches names already in the list the dialog has.
  const nameTaken = takenNames.has(nameValue)
  // A conflict applies to the name it was reported for, even if it arrives after the name was edited.
  const nameConflictDetail = nameConflict?.name === nameValue ? nameConflict.detail : null
  const nameMessage = nameConflictDetail
    ?? (nameTaken ? `A target named '${nameValue}' already exists.` : null)
    ?? (nameEdited ? validateRegistryName(nameValue) : null)
  const nameBlocked = nameConflictDetail !== null || nameTaken || validateRegistryName(nameValue) !== null
  const predictedTemporaryReason: string | null = (() => {
    if (!targetType) return null
    if (isRoundRobin) {
      const temporaryMembers = selectedInnerTargets
        .map((selected) => selected.registryName)
        .filter((name) => availableTargets.find((t) => t.target_registry_name === name)?.origin === 'temporary')
      if (temporaryMembers.length === 0) return null
      return `Uses temporary ${temporaryMembers.length === 1 ? 'target' : 'targets'} ${
        temporaryMembers.map((name) => `'${name}'`).join(', ')
      }, which won't exist after a restart.`
    }
    if (targetTypeEntry?.not_saveable_reason) return targetTypeEntry.not_saveable_reason
    const saveableAuthModes = targetTypeEntry?.saveable_auth_modes
    if (
      showAuthField
      && saveableAuthModes
      && authMode !== 'server_credential'
      && !saveableAuthModes.includes(authMode)
    ) {
      const choices = [
        ...saveableAuthModes.map((mode) => AUTH_MODE_LABELS[mode]),
        ...(supportsServerCredential ? [SERVER_CREDENTIAL_LABEL] : []),
      ].join(' or ')
      const current = authMode === 'api_key' ? 'an API key' : AUTH_MODE_LABELS[authMode]
      return `Saved targets can't use ${current} yet. Choose ${choices} to save this one.`
    }
    const typedCredential = hasApiKeyField && !isIdentity && !isNoKey && !isServerCredential && apiKey !== ''
      ? 'api_key'
      : metadataDrivenParameters.find((parameter) => (
        parameter.sensitive
        && isParameterValueSet(parameterValues[parameter.name])
        && !(isIdentity && parameter.identity_conflicting)
      ))?.name
    if (typedCredential) return `Saved targets can't hold credentials ('${typedCredential}') yet.`
    return null
  })()
  // A refused save only applies to the values it was refused for, so any edit lets the user try saving again.
  const formKey = JSON.stringify([
    targetType,
    endpoint,
    modelName,
    hasDifferentUnderlying,
    underlyingModel,
    authMode,
    serverCredential?.name,
    apiKey,
    parameterValues,
    selectedInnerTargets,
  ])
  const refusedReason = saveRefusal?.formKey === formKey ? saveRefusal.reason : null
  const temporaryReason = predictedTemporaryReason ?? refusedReason
  const submitLabel = submitting ? 'Creating...' : (temporaryReason === null ? 'Save Target' : 'Create as Temporary')
  const temporaryWarningRef = useRef<HTMLDivElement>(null)

  // The reason sits below the fields, so bring it into view when it appears or changes.
  useEffect(() => {
    if (temporaryReason) temporaryWarningRef.current?.scrollIntoView({ block: 'nearest' })
  }, [temporaryReason])

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
    listRegisteredTargets()
      .then((items) => {
        if (!cancelled) setAvailableTargets(items)
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
        isGuiTarget(t) &&
        getTargetType(t) !== 'RoundRobinTarget' &&
        !selectedNames.has(t.target_registry_name) &&
        !(targetIdentifierHash(t) && selectedHashes.has(targetIdentifierHash(t)!)),
    )
    // If nothing is selected yet, all non-RRT candidates are eligible
    if (selectedInnerTargets.length === 0) return candidates
    // Otherwise, filter to only targets compatible with the first one
    const firstSelected = availableTargets.find(
      (t) => t.target_registry_name === selectedInnerTargets[0]?.registryName,
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
    setChosenAuthMode(null)
    setServerCredentialName('')
    setApiKey('')
    setParameterValues({})
    setError(null)
    setFieldErrors({})
    setSelectedInnerTargets([])
    setEndpointFocused(false)
    setRegistryName('')
    setNameEdited(false)
    setNameConflict(null)
    setSaveRefusal(null)
  }

  const handleClose = () => {
    resetForm()
    onClose()
  }

  const handleCreateError = (err: unknown, persist: boolean) => {
    const apiError = toApiError(err)
    if (persist && isNotSaveableError(apiError)) {
      setSaveRefusal({ formKey, reason: apiError.detail })
      return
    }
    // A taken name is a 409 without a problem type; a typed 409 is the build-identity mismatch the client handles.
    if (apiError.status === 409 && apiError.type === undefined) {
      setNameConflict({ name: nameValue, detail: apiError.detail })
      // A conflict for a name that was edited away stays hidden, so it doesn't take focus either.
      if (nameInputRef.current?.value === nameValue) nameInputRef.current.focus()
      return
    }
    // Surface the backend's RFC 7807 `detail` (e.g. RoundRobinTarget validation
    // messages) rather than the generic axios "Request failed with status code 400".
    setError(apiError.detail)
  }

  const handleSubmit = async () => {
    if (nameBlocked) {
      // Keep the name shown, a suggestion included, so its message stays with it.
      setRegistryName(nameValue)
      setNameEdited(true)
      return
    }
    const persist = temporaryReason === null

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
          name: nameValue,
          type: 'RoundRobinTarget',
          params: {
            targets: selectedInnerTargets.map((t) => t.registryName),
            weights: parsedWeights,
          },
          persist,
        })
        resetForm()
        onCreated()
      } catch (err) {
        handleCreateError(err, persist)
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
    if (endpointRequired && !requestEndpoint) errors.endpoint = 'Please provide an endpoint URL'
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
      // Identity-based auth must be enforceable: strip any metadata-driven
      // parameter (e.g. AzureBlobStorageTarget's sas_token) that would let the
      // backend authenticate a different way than the selected identity.
      if (isIdentity) {
        for (const parameter of metadataDrivenParameters) {
          if (parameter.identity_conflicting) delete params[parameter.name]
        }
      }
      if (hasEndpointField && requestEndpoint) params.endpoint = requestEndpoint
      if (hasModelNameField && modelName) params.model_name = modelName
      if (hasApiKeyField && !isIdentity && !isNoKey && !isServerCredential && apiKey) params.api_key = apiKey

      if (
        (underlyingModelRequired || hasDifferentUnderlying)
        && underlyingModel
        && hasUnderlyingModelField
      ) {
        params[underlyingModelParameter?.name ?? 'underlying_model'] = underlyingModel
      }

      await targetsApi.createTarget({
        name: nameValue,
        type: targetType,
        params,
        ...(isIdentity ? { auth_mode: 'identity' as const } : {}),
        ...(isNoKey ? { auth_mode: 'none' as const } : {}),
        ...(serverCredential ? { auth_mode: 'api_key' as const, server_credential: serverCredential.name } : {}),
        persist,
      })

      resetForm()
      onCreated()
    } catch (err) {
      handleCreateError(err, persist)
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
                    // The picker also reports the type that is already selected, which must not clear what was typed.
                    if (!next || next === targetType) return
                    setTargetType(next)
                    setFieldErrors((previous) => ({ ...previous, targetType: undefined }))
                    setParameterValues({})
                    setChosenAuthMode(null)
                    setApiKey('')
                  }}
                >
                  {targetTypeOptions.map((entry) => {
                    const displayName = getTargetDisplayName(entry.target_type)
                    const authDescription = getAuthDescription(entry, typeMetadataAvailable)
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

              <Field
                className={styles.formField}
                label="Registry Name"
                hint="Lowercase letters, digits and underscores, starting with a letter."
                required
                validationMessage={nameMessage ?? undefined}
                validationState={nameMessage ? 'error' : 'none'}
              >
                <Input
                  ref={nameInputRef}
                  value={nameValue}
                  onChange={(_, data) => {
                    setRegistryName(data.value)
                    setNameEdited(true)
                  }}
                />
              </Field>

              {isGitHubCopilot && (
                <>
                  <MessageBar intent="info">
                    <MessageBarBody>
                      Enter a GitHub token, or leave blank to use <code>GITHUB_TOKEN</code> from
                      this instance&apos;s environment or loaded <code>.env</code>/<code>.env.local</code> files.
                      CoPyRIT does not use browser or saved Copilot logins.
                    </MessageBarBody>
                  </MessageBar>
                  <MessageBar intent="warning">
                    <MessageBarBody>
                      Tokens and created targets can be used by anyone with access to this CoPyRIT instance.
                    </MessageBarBody>
                  </MessageBar>
                </>
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
                          {t.origin === 'temporary' ? ` · ${TARGET_ORIGIN_LABELS.temporary}` : ''}
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
                          }${target?.origin === 'temporary' ? ` · ${TARGET_ORIGIN_LABELS.temporary}` : ''}`
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
                      hint={serverCredential
                        ? `Set by server credential '${serverCredential.name}', whose key is only sent to this address.`
                        : endpointParameter?.description || undefined}
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
                        value={requestEndpoint}
                        readOnly={isServerCredential}
                        onChange={(_, data) => setEndpoint(data.value)}
                        onFocus={() => setEndpointFocused(true)}
                        onBlur={() => setEndpointFocused(false)}
                      />
                    </Field>
                  )}

                  {hasModelNameField && (
                    <Field
                      label="Model / Deployment Name"
                      hint={isGitHubCopilot ? GITHUB_COPILOT_MODEL_HINT : modelNameParameter?.description || undefined}
                      required={modelNameRequired}
                      validationMessage={fieldErrors.modelName}
                      validationState={fieldErrors.modelName ? 'error' : 'none'}
                    >
                      <Input
                        placeholder={isAzureML
                          ? 'e.g. Llama-3.2-3B-Instruct'
                          : isGitHubCopilot ? 'Copilot model ID' : 'e.g. gpt-4o, my-deployment'}
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

                  {requiredMetadataParameters.map((parameter) => {
                    const identityConflict = isIdentity && Boolean(parameter.identity_conflicting)
                    return (
                      <ParameterField
                        key={parameter.name}
                        parameter={parameter}
                        value={parameterValues[parameter.name] ?? ''}
                        disabled={submitting || identityConflict}
                        label={getParameterLabel(parameter.name)}
                        showDefaultHint
                        allowEmptyList
                        testIdPrefix="target-param"
                        extraHint={identityConflict
                          ? 'Ignored with Identity-based authentication.'
                          : undefined}
                        onChange={(name, value) => setParameterValues((current) => ({
                          ...current,
                          [name]: value,
                        }))}
                      />
                    )
                  })}

                  {githubTokenParameter && (
                    <ParameterField
                      parameter={{ ...githubTokenParameter, description: GITHUB_COPILOT_TOKEN_HINT }}
                      value={parameterValues.github_token ?? ''}
                      disabled={submitting}
                      label="GitHub Token"
                      testIdPrefix="target-param"
                      onChange={(name, value) => setParameterValues((current) => ({
                        ...current,
                        [name]: value,
                      }))}
                    />
                  )}

                  {showAuthField && (
                    <Field label="Authentication">
                      <RadioGroup
                        value={authMode}
                        onChange={(_, data) => {
                          const next = data.value as AuthChoice
                          setChosenAuthMode(next)
                          if (next === 'none') {
                            setApiKey('')
                          }
                          if (next === 'server_credential') {
                            setApiKey('')
                            const picked = serverCredentials.find((credential) => (
                              credential.name === serverCredentialName
                            )) ?? serverCredentials[0]
                            if (picked) {
                              setServerCredentialName(picked.name)
                              // Kept when the user switches back to typing a key for the same address.
                              setEndpoint(picked.endpoint)
                            }
                          }
                          if (next === 'identity') {
                            setApiKey('')
                            setParameterValues((current) => {
                              const cleared = { ...current }
                              for (const parameter of metadataDrivenParameters) {
                                  if (parameter.identity_conflicting) delete cleared[parameter.name]
                              }
                              return cleared
                            })
                          }
                        }}
                      >
                        <Radio value="api_key" label="API Key" />
                        {supportsIdentity && (
                          <Radio value="identity" label="Identity-based (Microsoft Entra ID)" />
                        )}
                        {supportsNoKey && (
                          <Radio value="none" label="No key (for servers that need none)" />
                        )}
                        {supportsServerCredential && (
                          <Radio value="server_credential" label="Server credential (a key kept on this server)" />
                        )}
                      </RadioGroup>
                    </Field>
                  )}

                  {serverCredential && (
                    <Field
                      label="Server credential"
                      hint="The key stays on the server. A saved target reads it again each time the backend starts."
                    >
                      <Select
                        value={serverCredential.name}
                        disabled={submitting}
                        onChange={(_, data) => {
                          const picked = serverCredentials.find((credential) => credential.name === data.value)
                          if (!picked) return
                          setServerCredentialName(picked.name)
                          setEndpoint(picked.endpoint)
                        }}
                      >
                        {serverCredentials.map((credential) => (
                          <option key={credential.name} value={credential.name}>
                            {credential.name} ({credential.endpoint})
                          </option>
                        ))}
                      </Select>
                    </Field>
                  )}

                  {showIdentityEndpointError && !endpointFocused && (
                    <MessageBar intent="error" className={styles.warningMessage} role="status">
                      <MessageBarBody className={styles.warningMessageBody}>
                        {identityEndpointError}
                      </MessageBarBody>
                    </MessageBar>
                  )}

                  {hasApiKeyField && !isIdentity && !isNoKey && !isServerCredential && (
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
                        onChange={(_, data) => {
                          setApiKey(data.value)
                          setChosenAuthMode('api_key')
                        }}
                      />
                    </Field>
                  )}

                  {(optionalMetadataParameters.length > 0 || customFunctionsReason) && (
                    <details className={styles.advancedSettings}>
                      <summary className={styles.advancedSettingsSummary}>
                        Advanced settings
                      </summary>
                      <div className={styles.advancedSettingsFields}>
                        {optionalMetadataParameters.map((parameter) => {
                          const identityConflict = isIdentity && Boolean(parameter.identity_conflicting)
                          return (
                            <ParameterField
                              key={parameter.name}
                              parameter={parameter}
                              value={parameterValues[parameter.name] ?? ''}
                              disabled={submitting || identityConflict}
                              label={getParameterLabel(parameter.name)}
                              showDefaultHint
                              allowEmptyList
                              testIdPrefix="target-param"
                              extraHint={identityConflict
                                ? 'Ignored with Identity-based authentication.'
                                : undefined}
                              onChange={(name, value) => setParameterValues((current) => ({
                                ...current,
                                [name]: value,
                              }))}
                            />
                          )
                        })}
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

              {temporaryReason && (
                <MessageBar intent="warning" className={styles.warningMessage} ref={temporaryWarningRef} role="status">
                  <MessageBarBody className={styles.warningMessageBody}>
                    <MessageBarTitle>This target can&apos;t be saved</MessageBarTitle>
                    {temporaryReason} {TEMPORARY_TARGET_NOTE}
                  </MessageBarBody>
                </MessageBar>
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
                  || nameBlocked
              }
            >
              {submitLabel}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
