import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'

import {
  Badge,
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  MessageBar,
  MessageBarBody,
  mergeClasses,
  Select,
  Spinner,
  Text,
  Tooltip,
} from '@fluentui/react-components'
import {
  ArrowLeftRegular,
  ArrowSyncRegular,
  InfoRegular,
  SettingsRegular,
} from '@fluentui/react-icons'
import { Link, useNavigate, useParams } from 'react-router'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import TargetSelect from '@/components/Config/TargetSelect'
import { useRuntime } from '@/hooks/useRuntime'
import ParameterField from '@/components/Parameters/ParameterField'
import SingleStepSpinButton from '@/components/Parameters/SingleStepSpinButton'
import {
  getInitialFormValues,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import type { ViewName } from '@/components/Sidebar/Navigation'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  Parameter,
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioRunEstimate,
  ScenarioRunEstimateResult,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunEstimateState,
  TargetInstance,
} from '@/types'
import { routerPathParamValue, scenarioRunRoutePath } from '@/utils/routeParams'
import { sameTarget, targetModelName } from '@/utils/targetIdentity'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import ScenarioDatasetFields from './ScenarioDatasetFields'
import { ScenarioRunEstimateDetails } from './ScenarioRunEstimate'
import ScenarioTechniqueSelector from './ScenarioTechniqueSelector'
import {
  buildScenarioConfig,
  datasetSizeNotApplicable,
  defaultMaxDatasetSize,
  dynamicScenarioParameters,
  parseDatasetNames,
  uniqueTechniqueOptions,
} from './scenarioConfigForm'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'
import {
  DEFAULT_MAX_CONCURRENCY,
  DEFAULT_MAX_RETRIES,
  MAX_MAX_CONCURRENCY,
  MAX_MAX_RETRIES,
  MIN_MAX_CONCURRENCY,
  MIN_MAX_RETRIES,
  resolveSpinButtonValue,
} from './scenarioRunLimits'

const ESTIMATE_DEBOUNCE_MS = 300

function targetOptionLabel(target: TargetInstance): string {
  const modelName = targetModelName(target)
  return modelName
    ? `${target.target_registry_name} (${modelName})`
    : target.target_registry_name
}


type LoadStatus = 'loading' | 'success' | 'not-found' | 'error'

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
  datasetOverride: string
  maxDatasetSize: string
  harmCategoriesFilter: string
  dataTypesFilter: string
  includeBaseline: boolean
}

interface BuildRunRequestInput extends BuildEstimateRequestInput {
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

function buildEstimateRequest(input: BuildEstimateRequestInput): BuildEstimateRequestResult {
  const configResult = buildScenarioConfig(input)
  if (!configResult.ok) {
    return configResult
  }
  const { scenario, targetName, adversarialTargetName } = input
  const request: ScenarioRunSizeEstimateRequest = { ...configResult.config }
  if (targetName) {
    request.target_name = targetName
  }
  if (scenario.uses_default_adversarial_target && adversarialTargetName) {
    request.adversarial_target_name = adversarialTargetName
  }
  return { ok: true, request }
}

function buildRunRequest(input: BuildRunRequestInput): BuildRunRequestResult {
  if (!input.targetName) {
    return { ok: false, error: 'Select a target.' }
  }
  const estimateResult = buildEstimateRequest(input)
  if (!estimateResult.ok) {
    return estimateResult
  }
  if (
    !Number.isInteger(input.maxConcurrency)
    || input.maxConcurrency < MIN_MAX_CONCURRENCY
    || input.maxConcurrency > MAX_MAX_CONCURRENCY
  ) {
    return {
      ok: false,
      error: `Max concurrency must be an integer from ${MIN_MAX_CONCURRENCY} to ${MAX_MAX_CONCURRENCY}.`,
    }
  }
  if (
    !Number.isInteger(input.maxRetries)
    || input.maxRetries < MIN_MAX_RETRIES
    || input.maxRetries > MAX_MAX_RETRIES
  ) {
    return {
      ok: false,
      error: `Max retries must be an integer from ${MIN_MAX_RETRIES} to ${MAX_MAX_RETRIES}.`,
    }
  }

  const estimateRequest = estimateResult.request
  const request: RunScenarioRequest = {
    scenario_name: input.scenario.scenario_name,
    target_name: input.targetName,
    techniques: estimateRequest.techniques,
    max_concurrency: input.maxConcurrency,
    max_retries: input.maxRetries,
    include_baseline: estimateRequest.include_baseline,
    labels: input.labels,
  }
  if (estimateRequest.adversarial_target_name !== undefined) {
    request.adversarial_target_name = estimateRequest.adversarial_target_name
  }
  if (estimateRequest.dataset_names !== undefined) {
    request.dataset_names = estimateRequest.dataset_names
  }
  if (estimateRequest.max_dataset_size !== undefined) {
    request.max_dataset_size = estimateRequest.max_dataset_size
  }
  if (estimateRequest.dataset_filters !== undefined) {
    request.dataset_filters = estimateRequest.dataset_filters
  }
  if (estimateRequest.scenario_params !== undefined) {
    request.scenario_params = estimateRequest.scenario_params
  }
  return { ok: true, request }
}

interface ScenarioDetailProps {
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  /** False while the current generation's server defaults are still loading. */
  defaultsReady?: boolean
  onNavigate: (view: ViewName) => void
}

export default function ScenarioDetail(props: ScenarioDetailProps) {
  const { scenarioName: encodedScenarioName } = useParams<{ scenarioName: string }>()
  // Keying on the raw URL param forces a full remount (and state reset to the
  // initial "loading" values) whenever the route navigates from one scenario
  // detail page directly to another.
  return <ScenarioDetailContent key={encodedScenarioName} encodedScenarioName={encodedScenarioName} {...props} />
}

interface ScenarioDetailContentProps extends ScenarioDetailProps {
  encodedScenarioName: string | undefined
}

function ScenarioDetailContent({
  encodedScenarioName,
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  defaultsReady = false,
  labels,
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
      targets={targets}
      defaultObjectiveTarget={defaultObjectiveTarget}
      defaultAdversarialTarget={defaultAdversarialTarget}
      labels={labels}
      defaultsReady={defaultsReady}
      onNavigate={onNavigate}
    />
  )
}

interface ScenarioLaunchFormProps {
  scenario: RegisteredScenario
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  /** False while the current generation's server defaults are still loading. */
  defaultsReady?: boolean
  onNavigate: (view: ViewName) => void
}

function ScenarioLaunchForm({
  scenario,
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  labels,
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
    () => dynamicScenarioParameters(scenario),
    [scenario],
  )
  const isBaselineForbidden = scenario.baseline_policy === 'forbidden'

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
    targetName !== '' && !targets.some((target) => target.target_registry_name === targetName)
  ) || selectedTechniques.some((name) => !techniqueOptions.some((technique) => technique.name === name))
  const [baselineChecked, setBaselineChecked] = useState(
    () => !isBaselineForbidden && scenario.include_baseline_by_default,
  )
  const [datasetOverride, setDatasetOverride] = useState('')
  const configuredDefaultMaxDatasetSize = useMemo(
    () => defaultMaxDatasetSize(scenario),
    [scenario],
  )
  const [maxDatasetSize, setMaxDatasetSize] = useState(configuredDefaultMaxDatasetSize)
  const [harmCategoriesFilter, setHarmCategoriesFilter] = useState('')
  const [dataTypesFilter, setDataTypesFilter] = useState('')
  const [maxConcurrency, setMaxConcurrency] = useState(DEFAULT_MAX_CONCURRENCY)
  const [maxRetries, setMaxRetries] = useState(DEFAULT_MAX_RETRIES)
  const [scenarioParamValues, setScenarioParamValues] = useState<Record<string, ParameterFormValue>>(() =>
    getInitialFormValues(dynamicParameters),
  )
  const [validationError, setValidationError] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
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

  const techniques = selectedTechniques
  const maxDatasetSizeOverride = maxDatasetSize.trim()
    && maxDatasetSize !== configuredDefaultMaxDatasetSize
    ? maxDatasetSize
    : ''
  const datasetSizeLabel = datasetSizeNotApplicable(scenario)
    ? 'Not applicable'
    : maxDatasetSize.trim() || configuredDefaultMaxDatasetSize || 'Scenario default'
  const estimateResult = useMemo(
    () => buildEstimateRequest({
      scenario,
      targetName,
      adversarialTargetName,
      techniques,
      dynamicParameters,
      scenarioParamValues,
      datasetOverride,
      maxDatasetSize: maxDatasetSizeOverride,
      harmCategoriesFilter,
      dataTypesFilter,
      includeBaseline: isBaselineForbidden ? false : baselineChecked,
    }),
    [
      adversarialTargetName,
      baselineChecked,
      datasetOverride,
      dataTypesFilter,
      dynamicParameters,
      harmCategoriesFilter,
      isBaselineForbidden,
      maxDatasetSizeOverride,
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
      datasetOverride,
      maxDatasetSize: maxDatasetSizeOverride,
      harmCategoriesFilter,
      dataTypesFilter,
      maxConcurrency,
      maxRetries,
      includeBaseline: isBaselineForbidden ? false : baselineChecked,
      labels,
    }),
    [
      adversarialTargetName,
      baselineChecked,
      datasetOverride,
      dataTypesFilter,
      dynamicParameters,
      harmCategoriesFilter,
      isBaselineForbidden,
      labels,
      maxConcurrency,
      maxDatasetSizeOverride,
      maxRetries,
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
          setEstimateRequestState({
            status: 'error',
            requestKey: estimateRequestKey,
            error: toApiError(err).detail,
          })
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

  const updateScenarioParam = (name: string, value: ParameterFormValue): void => {
    setScenarioParamValues((current) => ({ ...current, [name]: value }))
  }

  const handleLaunchConfirmed = async (): Promise<void> => {
    if (isSubmittingRef.current || !runtime.ready || !defaultsReady || staleSelection || unavailableSelection) {
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
    setApiError(null)
    if (!requestResult.ok) {
      setValidationError(requestResult.error)
      return
    }
    setValidationError(null)
    setPreviewOpen(true)
  }

  const techniqueSelectionInvalid = selectedTechniques.length === 0
  const displayedEstimateNotes = estimateNotes(estimateState)
  const displayedEstimate = estimateFromState(estimateState)
  const selectedTechniqueCount = selectedTechniques.length + (baselineChecked ? 1 : 0)
  const previewDatasets = parseDatasetNames(datasetOverride)
  const effectiveDatasets = previewDatasets.length > 0 ? previewDatasets : scenario.default_datasets
  const previewHarmCategories = parseDatasetNames(harmCategoriesFilter)
  const previewDataTypes = parseDatasetNames(dataTypesFilter)

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
            {validationError && (
              <MessageBar intent="warning">
                <MessageBarBody role="alert">{validationError}</MessageBarBody>
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

            <ScenarioTechniqueSelector
              techniqueOptions={techniqueOptions}
              selectedTechniques={selectedTechniques}
              includeBaseline={baselineChecked}
              isBaselineForbidden={isBaselineForbidden}
              disabled={submitting}
              onTechniquesChange={(next) => {
                setSelectedTechniques(next)
                setValidationError(null)
              }}
              onIncludeBaselineChange={(next) => {
                setBaselineChecked(next)
                setValidationError(null)
              }}
            />

            <section className={styles.section} aria-labelledby="parameters-section-title">
              <Text id="parameters-section-title" as="h2" size={400} weight="semibold">
                Parameters
              </Text>
              <div className={styles.dynamicParameters}>
                {dynamicParameters.map((parameter) => (
                  <ParameterField
                    key={parameter.name}
                    parameter={parameter}
                    value={scenarioParamValues[parameter.name]}
                    disabled={submitting}
                    onChange={updateScenarioParam}
                    testIdPrefix="scenario-param"
                  />
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
                <ScenarioDatasetFields
                  scenario={scenario}
                  datasetOverride={datasetOverride}
                  maxDatasetSize={maxDatasetSize}
                  harmCategoriesFilter={harmCategoriesFilter}
                  dataTypesFilter={dataTypesFilter}
                  configuredDefaultMaxDatasetSize={configuredDefaultMaxDatasetSize}
                  disabled={submitting}
                  onDatasetOverrideChange={setDatasetOverride}
                  onMaxDatasetSizeChange={setMaxDatasetSize}
                  onHarmCategoriesFilterChange={setHarmCategoriesFilter}
                  onDataTypesFilterChange={setDataTypesFilter}
                />
                <Field label="Max concurrency">
                  <SingleStepSpinButton
                    className={styles.numberInput}
                    value={maxConcurrency}
                    min={MIN_MAX_CONCURRENCY}
                    max={MAX_MAX_CONCURRENCY}
                    disabled={submitting}
                    onChange={(_, data) => setMaxConcurrency(resolveSpinButtonValue(data, maxConcurrency))}
                    data-testid="max-concurrency-input"
                  />
                </Field>
                <Field label="Max retries">
                  <SingleStepSpinButton
                    className={styles.numberInput}
                    value={maxRetries}
                    min={MIN_MAX_RETRIES}
                    max={MAX_MAX_RETRIES}
                    disabled={submitting}
                    onChange={(_, data) => setMaxRetries(resolveSpinButtonValue(data, maxRetries))}
                    data-testid="max-retries-input"
                  />
                </Field>
              </div>
            </section>

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
                <div className={styles.costEstimateRow}>
                  <dt>Dataset size</dt>
                  <dd>
                    {datasetSizeLabel}
                  </dd>
                </div>
                <div className={styles.costEstimateRow}>
                  <dt>Number techniques</dt>
                  <dd>{selectedTechniqueCount}</dd>
                </div>
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
                disabled={!runtime.ready || !defaultsReady || staleSelection || unavailableSelection || submitting || techniqueSelectionInvalid}
                data-testid="launch-scenario-btn"
              >
                Launch scan
              </Button>
            </section>
          </form>

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
                      <dt>Objective Target</dt>
                      <dd>{targetName}</dd>
                      {scenario.uses_default_adversarial_target && (
                        <>
                          <dt>Adversarial Target</dt>
                          <dd>{adversarialTargetName || 'Use server default'}</dd>
                        </>
                      )}
                    </div>
                    <div className={styles.previewGroup}>
                      <dt>Techniques</dt>
                      <dd>
                        <div className={styles.previewBadges}>
                          {baselineChecked && <Badge appearance="outline">baseline</Badge>}
                          {selectedTechniques.map((name) => (
                            <Badge key={name} appearance="outline">{name}</Badge>
                          ))}
                        </div>
                      </dd>
                    </div>
                    <div className={styles.previewGroup}>
                      <dt>Datasets</dt>
                      <dd>
                        <div className={styles.previewStack}>
                          <Text>
                            {effectiveDatasets.length > 0
                              ? effectiveDatasets.join(', ')
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
                    disabled={submitting || !runtime.ready || !defaultsReady}
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
