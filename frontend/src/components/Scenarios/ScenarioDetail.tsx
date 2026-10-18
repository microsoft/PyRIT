import { type FormEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  Radio,
  MessageBar,
  MessageBarBody,
  mergeClasses,
  Select,
  Spinner,
  Switch,
  Text,
  Tooltip,
  ToggleButton,
} from '@fluentui/react-components'
import {
  ArrowLeftRegular,
  ArrowSyncRegular,
  InfoRegular,
  SettingsRegular,
} from '@fluentui/react-icons'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import ConversationEditor, { type ConversationEditorHandle } from '@/components/Chat/ConversationEditor'
import TargetSelect from '@/components/Config/TargetSelect'
import { useConversationDraft } from '@/hooks/useConversationDraft'
import { useRuntime } from '@/hooks/useRuntime'
import { useScenarioDatasets } from '@/hooks/useScenarioDatasets'
import ParameterField from '@/components/Parameters/ParameterField'
import SingleStepSpinButton from '@/components/Parameters/SingleStepSpinButton'
import {
  buildParametersFromForm,
  getInitialFormValues,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import type { ViewName } from '@/components/Sidebar/Navigation'
import { attacksApi, scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  AddMessageResponse,
  Parameter,
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioRunEstimate,
  ScenarioRunEstimateResult,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunEstimateState,
  ScenarioTechniqueSummary,
  ScenarioDatasetRequest,
  ScenarioDatasetSettings,
  ScenarioDatasetOptions,
  ScenarioRunSizeEstimateResponse,
  TargetInstance,
} from '@/types'
import { newDraftMessage, toConversationDraft } from '@/utils/conversationDraft'
import { routerPathParamValue, scenarioRunRoutePath } from '@/utils/routeParams'
import { sameTarget, targetModelName } from '@/utils/targetIdentity'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import { ScenarioRunEstimateDetails } from './ScenarioRunEstimate'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'
import { techniqueSetName } from './scenarioTechniqueSets'
import ScenarioDatasetTable from './ScenarioDatasetTable'

/**
 * Common/opaque parameters the base scenario declares via
 * `Scenario._common_scenario_parameters` — the launch form exposes a
 * purpose-built control for each of these (target, techniques, datasets,
 * labels, concurrency, retries, baseline), and `technique_converters` has no
 * UI at all. They're hidden from the dynamic scenario-specific parameter list.
 */
const COMMON_SCENARIO_PARAMETER_NAMES = new Set([
  'objective_target',
  'scenario_techniques',
  'technique_converters',
  'dataset_config',
  'dataset_overrides',
  'memory_labels',
  'max_concurrency',
  'max_retries',
  'include_baseline',
])

const MIN_MAX_CONCURRENCY = 1
const MAX_MAX_CONCURRENCY = 100
const MIN_MAX_RETRIES = 0
const MAX_MAX_RETRIES = 20
const DEFAULT_MAX_CONCURRENCY = 10
const DEFAULT_MAX_RETRIES = 0
const ESTIMATE_DEBOUNCE_MS = 300

function supportsParameter(scenario: RegisteredScenario, name: string): boolean {
  return scenario.supported_parameter_names.includes(name)
}

function targetOptionLabel(target: TargetInstance): string {
  const modelName = targetModelName(target)
  return modelName
    ? `${target.target_registry_name} (${modelName})`
    : target.target_registry_name
}

/** Resolves a Fluent `SpinButton` change event to a numeric value, preferring the parsed `value` over the raw `displayValue`. */
function resolveSpinButtonValue(data: { value?: number | null; displayValue?: string }, previous: number): number {
  if (typeof data.value === 'number') {
    return data.value
  }
  const parsed = data.displayValue !== undefined ? Number(data.displayValue) : NaN
  return Number.isFinite(parsed) ? parsed : previous
}

type LoadStatus = 'loading' | 'success' | 'not-found' | 'error'

interface TechniqueOptions {
  techniques: ScenarioTechniqueSummary[]
  defaultTechniques: string[]
}

function uniqueTechniqueOptions(scenario: RegisteredScenario): TechniqueOptions {
  const aggregateNames = new Set(scenario.aggregate_techniques)
  const summariesByName = new Map(
    scenario.technique_summaries.map((summary) => [summary.name, summary]),
  )
  const techniques: ScenarioTechniqueSummary[] = []
  const seen = new Set<string>()
  for (const name of scenario.all_techniques) {
    if (!aggregateNames.has(name) && !seen.has(name)) {
      techniques.push(summariesByName.get(name) ?? { name, description: null, tags: [] })
      seen.add(name)
    }
  }
  const concreteNames = new Set(techniques.map((technique) => technique.name))
  const defaultTechniques = scenario.default_techniques.filter((name) => concreteNames.has(name))
  if (defaultTechniques.length === 0 && concreteNames.has(scenario.default_technique)) {
    defaultTechniques.push(scenario.default_technique)
  }
  return { techniques, defaultTechniques }
}

interface SelectableTechnique extends ScenarioTechniqueSummary {
  isBaseline: boolean
  disabled: boolean
}

const BASELINE_TECHNIQUE: ScenarioTechniqueSummary = {
  name: 'baseline',
  description: 'Sends each objective directly to the target for comparison.',
  tags: ['baseline', 'single_turn'],
}

function formatParameterPreview(value: ParameterFormValue | undefined): string {
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(', ') : 'Not set'
  }
  if (typeof value === 'object') {
    return value.type || 'Not set'
  }
  return value?.trim() || 'Not set'
}

function formatEffectiveParameterPreview(
  parameterName: string,
  value: ParameterFormValue | undefined,
  estimate: ScenarioRunEstimate | undefined,
): string {
  const configuredValue = formatParameterPreview(value)
  if (configuredValue !== 'Not set') {
    return configuredValue
  }
  const effectiveValue = estimate?.effectiveParameters[parameterName]
  if (Array.isArray(effectiveValue)) {
    return effectiveValue.length > 0 ? effectiveValue.join(', ') : 'Not set'
  }
  return effectiveValue?.toString() ?? 'Not set'
}
function estimateFromState(state: ScenarioRunEstimateState): ScenarioRunEstimate | undefined {
  switch (state.status) {
    case 'available':
    case 'conditional':
    case 'refreshing':
    case 'stale':
      return state.estimate
    default:
      return undefined
  }
}

function formatAtomicAttackCount(state: ScenarioRunEstimateState): string {
  const estimate = estimateFromState(state)
  if (!estimate) {
    return state.status === 'loading' ? 'Calculating...' : 'Unknown'
  }
  const prefix = estimate.approximate ? 'About ' : ''
  if (estimate.total !== null) {
    return `${estimate.approximate ? 'Up to ' : ''}${estimate.total.toLocaleString()}`
  }
  if (estimate.minimum != null && estimate.maximum != null) {
    return estimate.minimum === estimate.maximum
      ? `${prefix}${estimate.minimum.toLocaleString()}`
      : `${prefix}${estimate.minimum.toLocaleString()}-${estimate.maximum.toLocaleString()}`
  }
  if (estimate.minimum != null) {
    return `At least ${prefix.toLowerCase()}${estimate.minimum.toLocaleString()}`
  }
  if (estimate.maximum != null) {
    return `Up to ${estimate.maximum.toLocaleString()}`
  }
  return 'Varies'
}

function estimateNotes(state: ScenarioRunEstimateState): string | null {
  const estimate = estimateFromState(state)
  if (!estimate) {
    return state.status === 'unavailable' ? state.note ?? state.label : null
  }
  const notes = [
    estimate.note,
    ...estimate.components.map((component) => component.note),
  ].filter((note): note is string => Boolean(note))
  return notes.length > 0 ? notes.join('\n\n') : null
}

interface BuildEstimateRequestInput {
  scenario: RegisteredScenario
  targetName: string
  adversarialTargetName: string
  techniques: string[]
  dynamicParameters: Parameter[]
  scenarioParamValues: Record<string, ParameterFormValue>
  datasetEdits: ScenarioDatasetRequest
  datasetError: string | null
  includeBaseline: boolean
}

interface BuildRunRequestInput extends BuildEstimateRequestInput {
  operationId: string | null
  maxConcurrency: number
  maxRetries: number
  labels: Record<string, string>
}

type BuildEstimateRequestResult =
  | {
      ok: true
      request: ScenarioRunSizeEstimateRequest
    }
  | {
      ok: false
      error: string
    }

type BuildRunRequestResult =
  | {
      ok: true
      request: RunScenarioRequest
    }
  | {
      ok: false
      error: string
    }

type SuccessfulEstimateResult = Extract<
  ScenarioRunEstimateResult,
  { status: 'available' | 'conditional' }
>

type EstimateRequestState =
  | {
      status: 'resolved'
      requestKey: string
      result: ScenarioRunEstimateResult
    }
  | {
      status: 'error'
      requestKey: string
      error: string
    }

function buildEstimateRequest({
  scenario,
  targetName,
  adversarialTargetName,
  techniques,
  dynamicParameters,
  scenarioParamValues,
  datasetEdits,
  datasetError,
  includeBaseline,
}: BuildEstimateRequestInput): BuildEstimateRequestResult {
  if (supportsParameter(scenario, 'scenario_techniques') && techniques.length === 0) {
    return { ok: false, error: 'Select at least one technique.' }
  }
  if (supportsParameter(scenario, 'scenario_techniques')
    && scenario.max_concrete_techniques != null && techniques.length > scenario.max_concrete_techniques) {
    return { ok: false, error: `Select at most ${scenario.max_concrete_techniques} concrete technique(s).` }
  }

  let scenarioParams: Record<string, unknown> | null = null
  if (dynamicParameters.length > 0) {
    const result = buildParametersFromForm(dynamicParameters, scenarioParamValues)
    if (!result.ok) {
      return result
    }
    scenarioParams = result.parameters
  }

  const supportsDatasets = supportsParameter(scenario, 'dataset_config')
  if (supportsDatasets && datasetError) return { ok: false, error: datasetError }
  const request: ScenarioRunSizeEstimateRequest = {}
  if (supportsParameter(scenario, 'scenario_techniques')) {
    request.techniques = techniques
  }
  if (supportsParameter(scenario, 'include_baseline')) {
    request.include_baseline = includeBaseline
  }
  if (supportsParameter(scenario, 'objective_target') && targetName) {
    request.target_name = targetName
  }
  if (scenario.uses_default_adversarial_target && adversarialTargetName) {
    request.adversarial_target_name = adversarialTargetName
  }
  if (supportsDatasets && Object.keys(datasetEdits).length > 0) {
    request.datasets = datasetEdits
  }
  if (scenarioParams) {
    request.scenario_params = scenarioParams
  }
  return { ok: true, request }
}

function buildRunRequest(input: BuildRunRequestInput): BuildRunRequestResult {
  if (supportsParameter(input.scenario, 'objective_target') && !input.targetName) {
    return { ok: false, error: 'Select a target.' }
  }
  const estimateResult = buildEstimateRequest(input)
  if (!estimateResult.ok) {
    return estimateResult
  }
  if (
    supportsParameter(input.scenario, 'max_concurrency') && (
      !Number.isInteger(input.maxConcurrency)
      || input.maxConcurrency < MIN_MAX_CONCURRENCY
      || input.maxConcurrency > MAX_MAX_CONCURRENCY
    )
  ) {
    return {
      ok: false,
      error: `Max concurrency must be an integer from ${MIN_MAX_CONCURRENCY} to ${MAX_MAX_CONCURRENCY}.`,
    }
  }
  if (
    supportsParameter(input.scenario, 'max_retries') && (
      !Number.isInteger(input.maxRetries)
      || input.maxRetries < MIN_MAX_RETRIES
      || input.maxRetries > MAX_MAX_RETRIES
    )
  ) {
    return {
      ok: false,
      error: `Max retries must be an integer from ${MIN_MAX_RETRIES} to ${MAX_MAX_RETRIES}.`,
    }
  }

  const estimateRequest = estimateResult.request
  const request: RunScenarioRequest = {
    scenario_name: input.scenario.scenario_name,
    ...estimateRequest,
  }
  if (input.operationId) request.operation_id = input.operationId
  if (supportsParameter(input.scenario, 'max_concurrency')) {
    request.max_concurrency = input.maxConcurrency
  }
  if (supportsParameter(input.scenario, 'max_retries')) {
    request.max_retries = input.maxRetries
  }
  if (supportsParameter(input.scenario, 'memory_labels')) {
    request.labels = input.labels
  }
  return { ok: true, request }
}

interface ScenarioDetailProps {
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  /** The saved Operation that new runs join, or null for none. */
  operationId?: string | null
  /** False while the current generation's server defaults are still loading. */
  defaultsReady?: boolean
  onNavigate: (view: ViewName) => void
}

export default function ScenarioDetail(props: ScenarioDetailProps) {
  const { scenarioName: encodedScenarioName } = useParams<{ scenarioName: string }>()
  const [searchParams] = useSearchParams()
  const conversationIds = searchParams.getAll('prepended_conversation_id')
  const initialConversationId = conversationIds.length === 1 ? conversationIds[0].trim() : ''
  // A new scenario or conversation link starts a fresh configuration form.
  return <ScenarioDetailContent key={`${encodedScenarioName}:${initialConversationId}`}
    encodedScenarioName={encodedScenarioName} initialConversationId={initialConversationId} {...props} />
}

interface ScenarioDetailContentProps extends ScenarioDetailProps {
  encodedScenarioName: string | undefined
  initialConversationId: string
}

function ScenarioDetailContent({
  encodedScenarioName,
  initialConversationId,
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  defaultsReady = false,
  labels,
  operationId = null,
  onNavigate,
}: ScenarioDetailContentProps) {
  const styles = useScenarioDetailStyles()
  const decodedScenarioName = routerPathParamValue(encodedScenarioName)

  const [scenario, setScenario] = useState<RegisteredScenario | null>(null)
  const { generation, ready } = useRuntime()
  const [scenarioStatus, setScenarioStatus] = useState<LoadStatus>('loading')
  const [scenarioError, setScenarioError] = useState<string | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)

  useEffect(() => {
    if (!ready) return
    let cancelled = false
    scenariosApi
      .getScenario(decodedScenarioName)
      .then((data) => {
        if (cancelled) return
        if (!Array.isArray(data.supported_parameter_names)) {
          setScenario(null)
          setScenarioStatus('error')
          setScenarioError('The scenario catalog has no input support metadata. Update the backend and reload.')
          return
        }
        setScenario(data)
        setScenarioStatus('success')
        setScenarioError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const apiError = toApiError(err)
        setScenario(null)
        setScenarioStatus(apiError.status === 404 ? 'not-found' : 'error')
        setScenarioError(apiError.status === 404 ? null : apiError.detail)
      })
    return () => {
      cancelled = true
    }
  }, [decodedScenarioName, refetchCount, generation, ready])

  const handleRetry = (): void => {
    setScenarioStatus('loading')
    setScenarioError(null)
    setRefetchCount((count) => count + 1)
  }

  if (scenarioStatus === 'loading') {
    return (
      <section className={styles.root} data-testid="scenario-detail" aria-label="Scenario detail">
        <div className={styles.centeredState}>
          <Spinner label="Loading scenario..." />
        </div>
      </section>
    )
  }

  if (scenarioStatus === 'not-found') {
    return (
      <section className={styles.root} data-testid="scenario-detail" aria-label="Scenario detail">
        <div className={styles.content}>
          <Link to="/scanner" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scanners
          </Link>
          <div className={styles.centeredState} data-testid="scenario-not-found">
            <Text size={400}>Scenario &quot;{decodedScenarioName}&quot; was not found</Text>
            <Text size={200}>It may have been renamed or is no longer registered.</Text>
          </div>
        </div>
      </section>
    )
  }

  if (scenarioStatus === 'error') {
    return (
      <section className={styles.root} data-testid="scenario-detail" aria-label="Scenario detail">
        <div className={styles.content}>
          <Link to="/scanner" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scanners
          </Link>
          <div className={styles.centeredState} data-testid="scenario-error">
            <MessageBar intent="error">
              <MessageBarBody>{scenarioError}</MessageBarBody>
            </MessageBar>
            <Button
              className={styles.touchTarget}
              appearance="primary"
              icon={<ArrowSyncRegular />}
              onClick={handleRetry}
              data-testid="retry-btn"
            >
              Retry
            </Button>
          </div>
        </div>
      </section>
    )
  }

  // scenarioStatus === 'success' from here on; both values are set together.
  if (!scenario) {
    return null
  }

  return (
    <ScenarioLaunchForm
      key={scenario.scenario_name}
      scenario={scenario}
      initialConversationId={initialConversationId}
      targets={targets}
      defaultObjectiveTarget={defaultObjectiveTarget}
      defaultAdversarialTarget={defaultAdversarialTarget}
      labels={labels}
      operationId={operationId}
      defaultsReady={defaultsReady}
      onNavigate={onNavigate}
    />
  )
}

interface ScenarioLaunchFormProps {
  scenario: RegisteredScenario
  initialConversationId: string
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  operationId: string | null
  /** False while the current generation's server defaults are still loading. */
  defaultsReady?: boolean
  onNavigate: (view: ViewName) => void
}

function ScenarioLaunchForm({
  scenario,
  initialConversationId,
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  labels,
  operationId,
  defaultsReady = false,
  onNavigate,
}: ScenarioLaunchFormProps) {
  const runtime = useRuntime()
  const [selectionGeneration, setSelectionGeneration] = useState(runtime.generation)
  const staleSelection = selectionGeneration !== runtime.generation
  const styles = useScenarioDetailStyles()
  const navigate = useNavigate()
  const formId = `scenario-launch-${encodeURIComponent(scenario.scenario_name).replace(/%/g, '-')}`

  const { techniques: techniqueOptions, defaultTechniques } = useMemo(
    () => uniqueTechniqueOptions(scenario),
    [scenario],
  )
  const dynamicParameters = useMemo(
    () => scenario.supported_parameters.filter(
      (parameter) => supportsParameter(scenario, parameter.name)
        && !COMMON_SCENARIO_PARAMETER_NAMES.has(parameter.name),
    ),
    [scenario],
  )
  const conversationConvertersParameter = dynamicParameters.some((parameter) => parameter.name === 'prepended_conversation_id')
    ? dynamicParameters.find((parameter) => parameter.name === 'apply_converters_to_prepended_history')
    : undefined
  const supportsObjectiveTarget = supportsParameter(scenario, 'objective_target')
  const supportsTechniques = supportsParameter(scenario, 'scenario_techniques')
  const supportsBaseline = supportsParameter(scenario, 'include_baseline')
  const supportsDatasets = supportsParameter(scenario, 'dataset_config')
  const supportsConcurrency = supportsParameter(scenario, 'max_concurrency')
  const supportsRetries = supportsParameter(scenario, 'max_retries')
  const isBaselineForbidden = scenario.baseline_policy === 'forbidden'
  const singleTechnique = scenario.max_concrete_techniques === 1

  const [targetName, setTargetName] = useState(() => {
    if (defaultObjectiveTarget && targets.some((target: TargetInstance) =>
      sameTarget(target, defaultObjectiveTarget))) {
      return defaultObjectiveTarget.target_registry_name
    }
    return ''
  })
  const adversarialTargets = targets.filter(
    (target: TargetInstance) => target.capabilities?.supports_multi_turn === true,
  )
  const [adversarialTargetName, setAdversarialTargetName] = useState(() => {
    if (defaultAdversarialTarget && adversarialTargets.some((target: TargetInstance) =>
      sameTarget(target, defaultAdversarialTarget))) {
      return defaultAdversarialTarget.target_registry_name
    }
    return ''
  })
  const [selectedTechniques, setSelectedTechniques] = useState<string[]>(() => defaultTechniques)
  const unavailableSelection = (
    supportsObjectiveTarget && targetName !== ''
    && !targets.some((target) => target.target_registry_name === targetName)
  ) || (supportsTechniques
    && selectedTechniques.some((name) => !techniqueOptions.some((technique) => technique.name === name)))
  const [baselineChecked, setBaselineChecked] = useState(
    () => !isBaselineForbidden && scenario.include_baseline_by_default,
  )
  const [datasetEdits, setDatasetEdits] = useState<ScenarioDatasetRequest>({})
  const [datasetError, setDatasetError] = useState<string | null>(null)
  const datasetMetadata = useScenarioDatasets()
  const [datasetProjection, setDatasetProjection] = useState<ScenarioRunSizeEstimateResponse | null>(null)
  const datasetDefaults: ScenarioDatasetSettings = datasetProjection?.dataset_defaults ?? scenario.dataset_defaults ?? {
    sources: scenario.default_datasets.map((name) => ({ name, max_size: 'all', fetch: 'if_missing', configured_provider: false })),
    max_per_dataset: 'all', max_total: scenario.default_run_size.dataset_limit.value ?? 'all',
    fetch: 'if_missing', filters: {},
  }
  const effectiveDatasets = datasetProjection?.effective_datasets ?? datasetDefaults
  const datasetOptions: ScenarioDatasetOptions = datasetProjection?.dataset_options ?? scenario.dataset_options ?? {
    mode: 'any', allowed_names: [], per_dataset_limit: true,
    total_limit: scenario.default_run_size.dataset_limit.state !== 'not_applicable',
    supports_overrides: true, filters: true,
  }
  const selectedDatasetNames = (datasetEdits.sources ?? datasetDefaults.sources).map((source) => source.name)
  const datasetSelectionKey = JSON.stringify(selectedDatasetNames)
  const reloadDatasetMetadata = datasetMetadata.reload
  const previousDatasetSelection = useRef(datasetSelectionKey)
  useEffect(() => {
    if (previousDatasetSelection.current !== datasetSelectionKey) {
      previousDatasetSelection.current = datasetSelectionKey
      reloadDatasetMetadata()
    }
  }, [datasetSelectionKey, reloadDatasetMetadata])
  const [maxConcurrency, setMaxConcurrency] = useState(DEFAULT_MAX_CONCURRENCY)
  const [maxRetries, setMaxRetries] = useState(DEFAULT_MAX_RETRIES)
  const [scenarioParamValues, setScenarioParamValues] = useState<Record<string, ParameterFormValue>>(() =>
    getInitialFormValues(dynamicParameters, initialConversationId ? { prepended_conversation_id: initialConversationId } : undefined),
  )
  const [validationError, setValidationError] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const newAttackContext = useMemo(() => ({
    generation: runtime.generation,
    ready: runtime.ready && defaultsReady && !staleSelection,
    labels,
  }), [runtime.generation, runtime.ready, defaultsReady, staleSelection, labels])
  const conversation = useConversationDraft(newAttackContext)
  const conversationEditorRef = useRef<ConversationEditorHandle>(null)
  const conversationContextRef = useRef(newAttackContext)
  useLayoutEffect(() => { conversationContextRef.current = newAttackContext }, [newAttackContext])
  const conversationLoadSequence = useRef(0)
  useEffect(() => () => { conversationLoadSequence.current += 1 }, [])
  const [conversationParameter, setConversationParameter] = useState('')
  const [loadingConversation, setLoadingConversation] = useState(false)
  const [conversationError, setConversationError] = useState<string | null>(null)
  const conversationBusy = loadingConversation || conversation.draft !== null
  const configuredConversationId = scenarioParamValues.prepended_conversation_id
  const canEditConversation = typeof configuredConversationId === 'string' && Boolean(configuredConversationId.trim())

  const beginConversation = async (parameterName: string, edit: boolean): Promise<void> => {
    if (conversationBusy || previewOpen || isSubmittingRef.current) return
    const sequence = ++conversationLoadSequence.current
    const initialContext = conversationContextRef.current
    setConversationParameter(parameterName)
    setConversationError(null)
    setLoadingConversation(edit)
    try {
      const value = scenarioParamValues[parameterName]
      if (edit && (typeof value !== 'string' || !value.trim())) {
        throw new Error('Enter a saved conversation ID before editing.')
      }
      const source = edit && typeof value === 'string' ? await attacksApi.getConversation(value.trim()) : null
      if (sequence !== conversationLoadSequence.current) return
      const currentContext = conversationContextRef.current
      if (!currentContext.ready || currentContext.generation !== initialContext.generation) {
        throw new Error('Runtime or default labels changed. Retry after default labels finish loading.')
      }
      conversation.begin({
        messages: source ? toConversationDraft(source.messages.messages) : [newDraftMessage()],
        objective: '', initialObjective: '',
        sourceAttackId: source?.attack.attack_result_id ?? null,
        sourceConversationId: source?.messages.conversation_id ?? null,
        target: null, labels: currentContext.labels,
      })
    } catch (error) {
      if (sequence === conversationLoadSequence.current) setConversationError(toApiError(error).detail)
    } finally {
      if (sequence === conversationLoadSequence.current) setLoadingConversation(false)
    }
  }
  const [estimateRequestState, setEstimateRequestState] = useState<EstimateRequestState | null>(null)
  const [lastGoodEstimate, setLastGoodEstimate] = useState<SuccessfulEstimateResult | null>(null)
  // Synchronous guard against a double-submit racing ahead of the state update.
  const isSubmittingRef = useRef(false)
  const estimateSequenceRef = useRef(0)
  const launchButtonRef = useRef<HTMLButtonElement | null>(null)

  const restoreLaunchFocus = (): void => {
    const focus = () => {
      launchButtonRef.current?.focus()
    }
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(focus)
    } else {
      setTimeout(focus, 0)
    }
  }

  const handleDismissPreview = (): void => {
    if (!submitting) {
      setPreviewOpen(false)
      restoreLaunchFocus()
    }
  }

  const selectableTechniques = useMemo<SelectableTechnique[]>(
    () => [
      ...(supportsBaseline ? [{
        ...BASELINE_TECHNIQUE,
        isBaseline: true,
        disabled: isBaselineForbidden,
      }] : []),
      ...(supportsTechniques ? techniqueOptions.map((technique) => ({
        ...technique,
        isBaseline: false,
        disabled: false,
      })) : []),
    ],
    [isBaselineForbidden, supportsBaseline, supportsTechniques, techniqueOptions],
  )
  const techniques = selectedTechniques
  const datasetSizeLabel = !datasetOptions.total_limit ? 'Not applicable'
    : effectiveDatasets.max_total === 'all' ? 'No total limit' : String(effectiveDatasets.max_total)
  const estimateResult = useMemo(
    () => buildEstimateRequest({
      scenario,
      targetName,
      adversarialTargetName,
      techniques,
      dynamicParameters,
      scenarioParamValues,
      datasetEdits,
      datasetError,
      includeBaseline: isBaselineForbidden ? false : baselineChecked,
    }),
    [
      adversarialTargetName,
      baselineChecked,
      datasetEdits,
      datasetError,
      dynamicParameters,
      isBaselineForbidden,
      scenario,
      scenarioParamValues,
      targetName,
      techniques,
    ],
  )
  const requestResult = useMemo(
    () => buildRunRequest({
      scenario,
      targetName,
      adversarialTargetName,
      techniques,
      dynamicParameters,
      scenarioParamValues,
      datasetEdits,
      datasetError,
      maxConcurrency,
      maxRetries,
      includeBaseline: isBaselineForbidden ? false : baselineChecked,
      labels,
      operationId,
    }),
    [
      adversarialTargetName,
      baselineChecked,
      datasetEdits,
      datasetError,
      dynamicParameters,
      isBaselineForbidden,
      labels,
      maxConcurrency,
      maxRetries,
      operationId,
      scenario,
      scenarioParamValues,
      targetName,
      techniques,
    ],
  )
  const estimateRequest = useMemo(
    () => estimateResult.ok ? estimateResult.request : null,
    [estimateResult],
  )
  const estimateRequestKey = useMemo(
    () => estimateRequest === null
      ? null
      : JSON.stringify({ scenarioName: scenario.scenario_name, request: estimateRequest, generation: runtime.generation }),
    [estimateRequest, scenario.scenario_name, runtime.generation],
  )

  useEffect(() => {
    if (!runtime.ready || staleSelection || estimateRequest === null || estimateRequestKey === null) {
      return
    }

    const requestSequence = estimateSequenceRef.current + 1
    estimateSequenceRef.current = requestSequence
    const controller = new AbortController()

    const debounceTimer = window.setTimeout(() => {
      scenariosApi
        .estimateRun(scenario.scenario_name, estimateRequest, controller.signal)
        .then((response) => {
          if (
            controller.signal.aborted
            || requestSequence !== estimateSequenceRef.current
          ) {
            return
          }
          const result = mapScenarioRunEstimate(response, 'request')
          setDatasetProjection(response)
          setEstimateRequestState({
            status: 'resolved',
            requestKey: estimateRequestKey,
            result,
          })
          if (result.status === 'available' || result.status === 'conditional') {
            setLastGoodEstimate(result)
          }
        })
        .catch((err: unknown) => {
          if (
            controller.signal.aborted
            || requestSequence !== estimateSequenceRef.current
          ) {
            return
          }
          const error = toApiError(err)
          setEstimateRequestState({
            status: 'error',
            requestKey: estimateRequestKey,
            error: error.detail,
          })
          if (estimateRequest.datasets && (error.status === 400 || error.status === 422)) {
            const { datasets, ...defaultRequest } = estimateRequest
            void datasets
            // Keep invalid edits visible while refreshing parameter-aware allowed sources.
            void scenariosApi.estimateRun(scenario.scenario_name, defaultRequest, controller.signal)
              .then((response) => {
                if (controller.signal.aborted || requestSequence !== estimateSequenceRef.current) return
                setDatasetProjection((previous) => ({
                  ...response,
                  effective_datasets: previous?.effective_datasets ?? response.effective_datasets,
                }))
              })
              .catch(() => { /* The original validation error remains authoritative. */ })
          }
        })
    }, ESTIMATE_DEBOUNCE_MS)

    return () => {
      window.clearTimeout(debounceTimer)
      controller.abort()
    }
  }, [estimateRequest, estimateRequestKey, scenario.scenario_name, runtime.ready, staleSelection])

  let estimateState: ScenarioRunEstimateState
  if (!estimateResult.ok) {
    estimateState = {
      status: 'unavailable',
      scope: 'request',
      label: 'Complete the required configuration to request an estimate.',
      note: estimateResult.error,
    }
  } else if (
    estimateRequestState?.requestKey === estimateRequestKey
    && estimateRequestState.status === 'resolved'
  ) {
    estimateState = estimateRequestState.result
  } else if (
    estimateRequestState?.requestKey === estimateRequestKey
    && estimateRequestState.status === 'error'
  ) {
    estimateState = lastGoodEstimate
      ? {
          status: 'stale',
          estimate: lastGoodEstimate.estimate,
          label: 'Showing the last successful estimate.',
          error: estimateRequestState.error,
        }
      : {
          status: 'unavailable',
          scope: 'request',
          label: 'The backend estimate could not be refreshed.',
          note: estimateRequestState.error,
        }
  } else if (lastGoodEstimate) {
    estimateState = {
      status: 'refreshing',
      estimate: lastGoodEstimate.estimate,
      label: 'Updating for the current configuration…',
    }
  } else {
    estimateState = { status: 'loading', scope: 'request' }
  }

  const handleTechniqueChange = (technique: SelectableTechnique, checked: boolean): void => {
    if (technique.isBaseline) {
      setBaselineChecked(checked)
    } else {
      setSelectedTechniques((current) => {
        if (checked) {
          if (singleTechnique) return [technique.name]
          if (scenario.max_concrete_techniques != null && current.length >= scenario.max_concrete_techniques) {
            return current
          }
          return current.includes(technique.name)
            ? current
            : [...current, technique.name]
        }
        return current.filter((name) => name !== technique.name)
      })
    }
    setValidationError(null)
  }

  const isTechniqueSelected = (technique: SelectableTechnique): boolean => (
    technique.isBaseline ? baselineChecked : selectedTechniques.includes(technique.name)
  )

  const handleTagChange = (tag: string): void => {
    const members = selectableTechniques.filter(
      (technique) => !technique.disabled && technique.tags.includes(tag),
    )
    const shouldSelect = members.some((technique) => !isTechniqueSelected(technique))
    const memberNames = new Set(
      members.filter((technique) => !technique.isBaseline).map((technique) => technique.name),
    )
    if (shouldSelect && scenario.max_concrete_techniques != null
      && new Set([...selectedTechniques, ...memberNames]).size > scenario.max_concrete_techniques) {
      return
    }
    setSelectedTechniques((current) => {
      const selected = new Set(current)
      for (const name of memberNames) {
        if (shouldSelect) selected.add(name)
        else selected.delete(name)
      }
      return techniqueOptions.map((technique) => technique.name).filter((name) => selected.has(name))
    })
    if (members.some((technique) => technique.isBaseline)) {
      setBaselineChecked(shouldSelect)
    }
    setValidationError(null)
  }

  const updateScenarioParam = (name: string, value: ParameterFormValue): void => {
    setScenarioParamValues((current) => ({ ...current, [name]: value }))
  }

  const handleLaunchConfirmed = async (): Promise<void> => {
    if (isSubmittingRef.current || conversationBusy || !runtime.ready || !defaultsReady || staleSelection || unavailableSelection) {
      return
    }

    setApiError(null)
    if (!requestResult.ok) {
      setValidationError(requestResult.error)
      setPreviewOpen(false)
      return
    }

    isSubmittingRef.current = true
    setSubmitting(true)
    setValidationError(null)

    try {
      const summary = await scenariosApi.startRun(requestResult.request)
      datasetMetadata.reload()
      setPreviewOpen(false)
      navigate(scenarioRunRoutePath(summary.scenario_result_id), {
        state: { scenarioName: scenario.scenario_name },
      })
    } catch (err) {
      setApiError(toApiError(err).detail)
    } finally {
      isSubmittingRef.current = false
      setSubmitting(false)
    }
  }

  const handleFormSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (conversationBusy || isSubmittingRef.current) return
    setApiError(null)
    if (!requestResult.ok) {
      setValidationError(requestResult.error)
      return
    }
    setValidationError(null)
    setPreviewOpen(true)
  }

  const techniqueSelectionOverLimit = supportsTechniques && scenario.max_concrete_techniques != null
    && selectedTechniques.length > scenario.max_concrete_techniques
  const techniqueSelectionError = supportsTechniques && selectedTechniques.length === 0
    ? 'Select at least one attack technique.'
    : techniqueSelectionOverLimit
    ? `Select at most ${scenario.max_concrete_techniques} concrete technique(s).`
    : null
  const displayedEstimateNotes = estimateNotes(estimateState)
  const displayedEstimate = estimateFromState(estimateState)
  const selectedTechniqueCount = (supportsTechniques ? selectedTechniques.length : 0)
    + (supportsBaseline && baselineChecked && !isBaselineForbidden ? 1 : 0)
  const previewDatasets = (datasetEdits.sources ?? datasetDefaults.sources).map((source) => source.name)
  const previewDatasetNames = previewDatasets.length > 0 ? previewDatasets : scenario.default_datasets
  const previewFilters = { ...datasetDefaults.filters, ...datasetEdits.filters }
  const previewHarmCategories = previewFilters.harm_categories ?? []
  const previewDataTypes = previewFilters.data_types ?? []

  return (
    <section
      className={styles.root}
      data-testid="scenario-detail"
      aria-labelledby="scenario-detail-title"
    >
      <div className={styles.content}>
        <Link to="/scanner" className={styles.backLink}>
          <ArrowLeftRegular /> Back to scanners
        </Link>

        <div className={styles.headerText}>
          <Text id="scenario-detail-title" as="h1" size={600} weight="semibold">
            {scenario.scenario_name}
          </Text>
        </div>

        <div className={styles.layout}>
          <form
            id={formId}
            className={styles.formColumn}
            aria-label="Scenario run configuration"
            onSubmit={handleFormSubmit}
            noValidate
          >
            {validationError && !requestResult.ok && (
              <MessageBar intent="warning">
                <MessageBarBody role="alert">{requestResult.error}</MessageBarBody>
              </MessageBar>
            )}
            {(staleSelection || (targetName && unavailableSelection)) && (
              <MessageBar intent="warning">
                <MessageBarBody>
                  PyRIT was reinitialized. Review the refreshed target and technique selections before starting.
                  Your parameter drafts have been preserved.
                  <Button onClick={() => {
                    setSelectedTechniques(selectedTechniques.filter((name) =>
                      techniqueOptions.some((technique) => technique.name === name)))
                    setSelectionGeneration(runtime.generation)
                  }}>
                    I have reviewed the refreshed selections
                  </Button>
                </MessageBarBody>
              </MessageBar>
            )}
            {apiError && (
              <MessageBar intent="error">
                <MessageBarBody role="alert">{apiError}</MessageBarBody>
              </MessageBar>
            )}

            <section className={styles.section} aria-label="Scenario description">
              <MarkdownContent
                content={normalizeScenarioMarkdown(
                  scenario.description_markdown || scenario.description,
                )}
                className={styles.description}
                testId="scenario-detail-description"
              />
            </section>

            {supportsObjectiveTarget && (
            <section className={styles.section} aria-labelledby="target-section-title">
              <Text id="target-section-title" as="h2" size={400} weight="semibold">Objective Target</Text>
              <Field hint="The registered target this scenario will run against.">
                <Select
                  className={styles.control}
                  value={targetName}
                  disabled={submitting}
                  onChange={(_, data) => { setTargetName(data.value); setSelectionGeneration(runtime.generation) }}
                  data-testid="scenario-target-select"
                  aria-label="Objective Target"
                >
                  {!targets.some((target) => target.target_registry_name === targetName) && (
                    <option value={targetName} disabled>
                      {targets.length === 0 ? 'No targets configured' : 'Select an available target'}
                    </option>
                  )}
                  {targets.map((target) => (
                    <option key={target.target_registry_name} value={target.target_registry_name}>
                      {targetOptionLabel(target)}
                    </option>
                  ))}
                </Select>
              </Field>
              {targets.length === 0 && (
                <Button
                  className={styles.touchTarget}
                  appearance="secondary"
                  icon={<SettingsRegular />}
                  type="button"
                  onClick={() => onNavigate('registry')}
                >
                  Configure target to launch
                </Button>
              )}
            </section>
            )}

            {(supportsTechniques || supportsBaseline) && (
            <section className={styles.section} aria-labelledby="techniques-section-title">
              <Text id="techniques-section-title" as="h2" size={400} weight="semibold">
                Techniques
              </Text>
              {supportsTechniques && <Text size={200} className={styles.hint}>
                {singleTechnique
                  ? 'Select exactly one technique. Baseline comparison is optional and separate.'
                  : 'Select individual techniques, or use a tag to select or clear all techniques with that tag.'}
              </Text>}
              {techniqueSelectionError && (
                <Text className={styles.errorText} role="alert">
                  {techniqueSelectionError}
                </Text>
              )}
              <div className={styles.techniqueList} role="group" aria-label="Techniques">
                {selectableTechniques.map((technique) => {
                  const selected = isTechniqueSelected(technique)
                  return (
                    <div className={styles.techniqueOption} key={technique.name}>
                    {singleTechnique && !technique.isBaseline ? (
                      <Radio
                        className={styles.selectionControl}
                        name={`${formId}-technique`}
                        label={technique.name}
                        value={technique.name}
                        checked={selected && !techniqueSelectionOverLimit}
                        disabled={submitting || technique.disabled}
                        onChange={() => handleTechniqueChange(technique, true)}
                        data-testid={`technique-${technique.name}`}
                      />
                    ) : <Checkbox
                      className={styles.selectionControl}
                      label={technique.name}
                      checked={selected}
                      disabled={submitting || technique.disabled || (!technique.isBaseline && !selected
                        && scenario.max_concrete_techniques != null
                        && selectedTechniques.length >= scenario.max_concrete_techniques)}
                      onChange={(_, data) => handleTechniqueChange(technique, data.checked === true)}
                      data-testid={technique.isBaseline ? 'baseline-checkbox' : `technique-${technique.name}`}
                    />}
                    <div className={styles.techniqueDetails}>
                      {technique.description && (
                        <Text size={200} className={styles.hint}>{technique.description}</Text>
                      )}
                      {technique.tags.length > 0 && (
                        <div className={styles.techniqueTags} aria-label={`${technique.name} tags`}>
                          {technique.tags.map((tag) => {
                            const tagMembers = selectableTechniques.filter(
                              (candidate) => !candidate.disabled && candidate.tags.includes(tag),
                            )
                            const tagSelected = tagMembers.length > 0 && tagMembers.every(isTechniqueSelected)
                            const exceedsLimit = scenario.max_concrete_techniques != null
                              && new Set([
                                ...selectedTechniques,
                                ...tagMembers.filter((candidate) => !candidate.isBaseline).map((candidate) => candidate.name),
                              ]).size > scenario.max_concrete_techniques
                            if (singleTechnique) {
                              return <Badge key={tag} size="small" appearance="outline">{techniqueSetName(tag)}</Badge>
                            }
                            return (
                              <ToggleButton
                                className={styles.techniqueTag}
                                key={tag}
                                size="small"
                                appearance="outline"
                                checked={tagSelected}
                                disabled={submitting || tagMembers.length === 0 || (!tagSelected && exceedsLimit)}
                                onClick={() => handleTagChange(tag)}
                                aria-label={`${tagSelected ? 'Clear' : 'Select'} ${techniqueSetName(tag)} techniques`}
                              >
                                {techniqueSetName(tag)}
                              </ToggleButton>
                            )
                          })}
                        </div>
                      )}
                      {technique.disabled && (
                        <Text size={200} className={styles.hint}>
                          This scenario does not support a baseline comparison.
                        </Text>
                      )}
                    </div>
                  </div>
                  )
                })}
              </div>
            </section>
            )}

            {(supportsDatasets || datasetDefaults.sources.length > 0) && (
            <section className={styles.section} aria-labelledby="datasets-section-title">
              <Text id="datasets-section-title" as="h2" size={400} weight="semibold">Datasets</Text>
              <ScenarioDatasetTable
                request={datasetEdits} defaults={datasetDefaults} effective={effectiveDatasets}
                options={supportsDatasets ? datasetOptions : { ...datasetOptions, mode: 'fixed', supports_overrides: false }}
                datasets={datasetMetadata.items} disabled={submitting}
                loading={datasetMetadata.loading} metadataError={datasetMetadata.error}
                preparing={datasetMetadata.preparing} loadError={datasetMetadata.loadError}
                loadEnabled={runtime.ready && !staleSelection && estimateRequest !== null}
                onLoad={() => { if (estimateRequest) void datasetMetadata.loadSelected(scenario.scenario_name, estimateRequest) }}
                onReload={datasetMetadata.reload} onChange={setDatasetEdits} onValidationChange={setDatasetError}
              />
            </section>
            )}

            {(dynamicParameters.length > 0 || scenario.uses_default_adversarial_target
              || supportsConcurrency || supportsRetries) && (
            <section className={styles.section} aria-labelledby="parameters-section-title">
              <Text id="parameters-section-title" as="h2" size={400} weight="semibold">
                Parameters
              </Text>
              <div className={styles.dynamicParameters}>
                {dynamicParameters.filter((parameter) => parameter.name !== conversationConvertersParameter?.name).map((parameter) => (
                  <div key={parameter.name} className={styles.dynamicParameters}>
                    <ParameterField
                      parameter={parameter}
                      value={scenarioParamValues[parameter.name]}
                      disabled={submitting || loadingConversation}
                      onChange={updateScenarioParam}
                      testIdPrefix="scenario-param"
                    />
                    {parameter.name === 'prepended_conversation_id' && <>
                      <div className={styles.conversationActions}>
                        <Button type="button" className={styles.touchTarget}
                          disabled={submitting || conversationBusy || previewOpen || !newAttackContext.ready}
                          onClick={() => { void beginConversation(parameter.name, false) }}>
                          Add new conversation
                        </Button>
                        <Button type="button" className={styles.touchTarget}
                          disabled={submitting || conversationBusy || previewOpen || !newAttackContext.ready || !canEditConversation}
                          onClick={() => { void beginConversation(parameter.name, true) }}>
                          Edit conversation
                        </Button>
                        {conversationConvertersParameter && (
                          <Tooltip relationship="description"
                            content="Apply request converters to stored history when the scan runs. The source conversation does not change. New outgoing turns are always converted.">
                            <Switch label="Apply converters to conversation" className={styles.selectionControl}
                              checked={scenarioParamValues[conversationConvertersParameter.name] === 'true'}
                              disabled={submitting || loadingConversation || !newAttackContext.ready || !canEditConversation}
                              onChange={(_, data) => updateScenarioParam(conversationConvertersParameter.name, String(data.checked))} />
                          </Tooltip>
                        )}
                      </div>
                      {loadingConversation && <Spinner size="tiny" label="Loading conversation..." />}
                      {conversationError && <MessageBar intent="error">
                        <MessageBarBody>{conversationError}</MessageBarBody>
                      </MessageBar>}
                    </>}
                  </div>
                ))}
                {scenario.uses_default_adversarial_target && (
                  <TargetSelect
                    targets={adversarialTargets}
                    value={adversarialTargetName}
                    onChange={(target: TargetInstance | null) => {
                      setAdversarialTargetName(target?.target_registry_name ?? '')
                    }}
                    label="Adversarial Target"
                    hint="The registered target this scenario uses to generate attacks."
                    placeholder="Use server default"
                    disabled={submitting}
                  />
                )}
                {supportsConcurrency && <Field label="Max concurrency">
                  <SingleStepSpinButton
                    className={styles.numberInput}
                    value={maxConcurrency}
                    min={MIN_MAX_CONCURRENCY}
                    max={MAX_MAX_CONCURRENCY}
                    disabled={submitting}
                    onChange={(_, data) => setMaxConcurrency(resolveSpinButtonValue(data, maxConcurrency))}
                    data-testid="max-concurrency-input"
                  />
                </Field>}
                {supportsRetries && <Field label="Max retries">
                  <SingleStepSpinButton
                    className={styles.numberInput}
                    value={maxRetries}
                    min={MIN_MAX_RETRIES}
                    max={MAX_MAX_RETRIES}
                    disabled={submitting}
                    onChange={(_, data) => setMaxRetries(resolveSpinButtonValue(data, maxRetries))}
                    data-testid="max-retries-input"
                  />
                </Field>}
              </div>
            </section>
            )}

            <section
              className={styles.section}
              aria-labelledby="run-estimate-title"
              data-testid="run-estimate"
            >
              <div className={styles.estimateHeader}>
                <Text id="run-estimate-title" as="h2" size={400} weight="semibold">
                  Run estimate
                </Text>
                {displayedEstimateNotes && (
                  <Tooltip
                    content={displayedEstimateNotes}
                    relationship="description"
                    positioning="above"
                  >
                    <Button
                      appearance="subtle"
                      icon={<InfoRegular />}
                      aria-label="Estimate notes"
                      size="small"
                    />
                  </Tooltip>
                )}
              </div>
              <dl className={styles.costEstimateList}>
                <div className={mergeClasses(styles.costEstimateRow, styles.totalEstimateRow)}>
                  <dt>Total atomic attacks</dt>
                  <dd>{formatAtomicAttackCount(estimateState)}</dd>
                </div>
                {supportsDatasets && <div className={styles.costEstimateRow}>
                  <dt>Dataset size</dt>
                  <dd>
                    {datasetSizeLabel}
                  </dd>
                </div>}
                {(supportsTechniques || supportsBaseline) && <div className={styles.costEstimateRow}>
                  <dt>Number techniques</dt>
                  <dd>{selectedTechniqueCount}</dd>
                </div>}
                {dynamicParameters.map((parameter) => (
                  <div className={styles.costEstimateRow} key={parameter.name}>
                    <dt>{parameter.name}</dt>
                    <dd>
                      {formatEffectiveParameterPreview(
                        parameter.name,
                        scenarioParamValues[parameter.name],
                        displayedEstimate,
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
              {estimateState.status === 'refreshing' && (
                <div className={styles.inlineStatus}>
                  <Spinner size="tiny" />
                  <Text size={200} className={styles.hint}>Updating estimate...</Text>
                </div>
              )}
              {estimateState.status === 'stale' && (
                <Text size={200} className={styles.warningText}>
                  {estimateState.error}
                </Text>
              )}
            </section>

            <section className={styles.launchSection} aria-label="Launch scan">
              <Button
                ref={launchButtonRef}
                className={styles.launchButton}
                appearance="primary"
                type="submit"
                disabled={!runtime.ready || !defaultsReady || staleSelection || unavailableSelection || submitting || conversationBusy || techniqueSelectionError !== null || datasetMetadata.preparing || (supportsDatasets && datasetError !== null)}
                data-testid="launch-scenario-btn"
              >
                Launch scan
              </Button>
            </section>
          </form>

          {conversation.draft && <Dialog open onOpenChange={(_, data) => {
            if (!data.open) conversationEditorRef.current?.cancel()
          }}>
            <DialogSurface className={styles.conversationSurface}>
              <DialogBody>
                <DialogTitle>{conversation.draft.sourceConversationId ? 'Edit conversation' : 'New conversation'}</DialogTitle>
                <DialogContent className={styles.conversationContent}>
                  <ConversationEditor key={conversation.draft.id} ref={conversationEditorRef} controller={conversation}
                    saveDestination="new_attack" saveLabel="Add to attack"
                    newAttackDisabledReason={!newAttackContext.ready ? 'Default labels are not ready.' : undefined}
                    onSaved={(response: AddMessageResponse) => {
                      updateScenarioParam(conversationParameter, response.messages.conversation_id)
                      conversation.discard()
                    }} />
                </DialogContent>
              </DialogBody>
            </DialogSurface>
          </Dialog>}

          <Dialog
            open={previewOpen}
            onOpenChange={(_, data) => {
              if (!submitting) {
                setPreviewOpen(data.open)
                if (!data.open) {
                  restoreLaunchFocus()
                }
              }
            }}
          >
            <DialogSurface>
              <DialogBody>
                <DialogTitle>Run preview</DialogTitle>
                <DialogContent className={styles.dialogContent}>
                  <dl className={styles.previewList}>
                    <div className={styles.previewGroup}>
                      {supportsObjectiveTarget && <>
                        <dt>Objective Target</dt>
                        <dd>{targetName}</dd>
                      </>}
                      {scenario.uses_default_adversarial_target && (
                        <>
                          <dt>Adversarial Target</dt>
                          <dd>{adversarialTargetName || 'Use server default'}</dd>
                        </>
                      )}
                    </div>
                    {(supportsTechniques || supportsBaseline) && <div className={styles.previewGroup}>
                      <dt>Techniques</dt>
                      <dd>
                        <div className={styles.previewBadges}>
                          {supportsBaseline && baselineChecked && !isBaselineForbidden
                            && <Badge appearance="outline">baseline</Badge>}
                          {supportsTechniques && selectedTechniques.map((name) => (
                            <Badge key={name} appearance="outline">{name}</Badge>
                          ))}
                        </div>
                      </dd>
                    </div>}
                    {supportsDatasets && <>
                    <div className={styles.previewGroup}>
                      <dt>Datasets</dt>
                      <dd>
                        <div className={styles.previewStack}>
                          <Text>
                            {previewDatasetNames.length > 0
                              ? previewDatasetNames.join(', ')
                              : 'No datasets declared'}
                          </Text>
                          <Text size={200} className={styles.hint}>
                            {previewDatasets.length > 0 ? 'Custom override' : 'Scenario defaults'}
                            {` - dataset size: ${datasetSizeLabel}`}
                          </Text>
                        </div>
                      </dd>
                    </div>
                    <div className={styles.previewGroup}>
                      <dt>Dataset filters</dt>
                      <dd>
                        {previewHarmCategories.length > 0 || previewDataTypes.length > 0 ? (
                          <dl className={styles.parameterPreview}>
                            {previewHarmCategories.length > 0 && (
                              <div className={styles.parameterPreviewRow}>
                                <dt>Harm categories</dt>
                                <dd>{previewHarmCategories.join(', ')}</dd>
                              </div>
                            )}
                            {previewDataTypes.length > 0 && (
                              <div className={styles.parameterPreviewRow}>
                                <dt>Data types</dt>
                                <dd>{previewDataTypes.join(', ')}</dd>
                              </div>
                            )}
                          </dl>
                        ) : (
                          'None'
                        )}
                      </dd>
                    </div>
                    </>}
                    <div className={styles.previewGroup}>
                      <dt>Parameters</dt>
                      <dd>
                        {dynamicParameters.length > 0 ? (
                          <dl className={styles.parameterPreview}>
                            {dynamicParameters.map((parameter) => (
                              <div className={styles.parameterPreviewRow} key={parameter.name}>
                                <dt>{parameter.name}</dt>
                                <dd>
                                  {formatEffectiveParameterPreview(
                                    parameter.name,
                                    scenarioParamValues[parameter.name],
                                    displayedEstimate,
                                  )}
                                </dd>
                              </div>
                            ))}
                          </dl>
                        ) : (
                          'No scenario-specific parameters'
                        )}
                      </dd>
                    </div>
                  </dl>
                  <ScenarioRunEstimateDetails state={estimateState} />
                </DialogContent>
                <DialogActions>
                  <Button
                    appearance="secondary"
                    disabled={submitting}
                    onClick={handleDismissPreview}
                  >
                    Cancel
                  </Button>
                  <Button
                    appearance="primary"
                    disabled={submitting || conversationBusy || !runtime.ready || !defaultsReady || !requestResult.ok}
                    onClick={() => void handleLaunchConfirmed()}
                    data-testid="confirm-launch-scenario-btn"
                  >
                    {submitting ? 'Launching...' : 'Launch scan'}
                  </Button>
                </DialogActions>
              </DialogBody>
            </DialogSurface>
          </Dialog>
        </div>
      </div>
    </section>
  )
}
