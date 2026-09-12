import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'

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
  Input,
  MessageBar,
  MessageBarBody,
  mergeClasses,
  Select,
  Spinner,
  SpinButton,
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
import { Link, useNavigate, useParams } from 'react-router'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import ParameterField from '@/components/Parameters/ParameterField'
import {
  buildParametersFromForm,
  getInitialFormValues,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import type { ViewName } from '@/components/Sidebar/Navigation'
import { scenariosApi, targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  Parameter,
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioRunEstimate,
  ScenarioRunEstimateResult,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunEstimateState,
  ScenarioTechniqueSummary,
  TargetInstance,
} from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'
import { routerPathParamValue, scenarioRunRoutePath } from '@/utils/routeParams'
import { targetModelName } from '@/utils/targetIdentity'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import { ScenarioRunEstimateDetails } from './ScenarioRunEstimate'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'
import { techniqueSetName } from './scenarioTechniqueSets'

/** Items requested per target page while paging through the full list. */
const TARGET_PAGE_SIZE = 200

/**
 * Common/opaque parameters every scenario declares via
 * `Scenario._common_scenario_parameters` — the launch form already exposes a
 * purpose-built control for each of these (target, techniques, datasets,
 * labels, concurrency, retries, baseline), and `technique_converters` has no
 * UI at all. They're hidden from the dynamic scenario-specific parameter list.
 */
const COMMON_SCENARIO_PARAMETER_NAMES = new Set([
  'objective_target',
  'scenario_techniques',
  'technique_converters',
  'dataset_config',
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

function targetOptionLabel(target: TargetInstance): string {
  const modelName = targetModelName(target)
  return modelName
    ? `${target.target_registry_name} (${modelName})`
    : target.target_registry_name
}

function defaultMaxDatasetSize(scenario: RegisteredScenario): string {
  const datasets = scenario.default_run_size.datasets
  if (datasets.length === 0) {
    return ''
  }

  for (const dataset of datasets) {
    if (dataset.configured_caps.length === 0) {
      return ''
    }
  }

  const selectedGroupCount = datasets.reduce(
    (total, dataset) => total + dataset.selected_seed_group_count,
    0,
  )
  return selectedGroupCount > 0 ? String(selectedGroupCount) : ''
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

function parseDatasetNames(datasetOverride: string): string[] {
  return datasetOverride
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
}

function formatParameterPreview(value: ParameterFormValue | undefined): string {
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(', ') : 'Not set'
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
    return state.status === 'loading' ? 'Calculating...' : 'Unavailable'
  }
  if (estimate.total !== null) {
    return estimate.total.toLocaleString()
  }
  if (estimate.minimum != null && estimate.maximum != null) {
    return estimate.minimum === estimate.maximum
      ? estimate.minimum.toLocaleString()
      : `${estimate.minimum.toLocaleString()}-${estimate.maximum.toLocaleString()}`
  }
  if (estimate.minimum != null) {
    return `At least ${estimate.minimum.toLocaleString()}`
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

function buildEstimateRequest({
  targetName,
  techniques,
  dynamicParameters,
  scenarioParamValues,
  datasetOverride,
  maxDatasetSize,
  harmCategoriesFilter,
  dataTypesFilter,
  includeBaseline,
}: BuildEstimateRequestInput): BuildEstimateRequestResult {
  if (techniques.length === 0) {
    return { ok: false, error: 'Select at least one technique.' }
  }

  let scenarioParams: Record<string, unknown> | null = null
  if (dynamicParameters.length > 0) {
    const result = buildParametersFromForm(dynamicParameters, scenarioParamValues)
    if (!result.ok) {
      return result
    }
    scenarioParams = result.parameters
  }

  let maxDatasetSizeValue: number | undefined
  const trimmedMaxDatasetSize = maxDatasetSize.trim()
  if (trimmedMaxDatasetSize.length > 0) {
    const parsed = Number(trimmedMaxDatasetSize)
    if (!Number.isInteger(parsed) || parsed < 1) {
      return { ok: false, error: 'Max dataset size must be a positive integer.' }
    }
    maxDatasetSizeValue = parsed
  }
  const datasetNames = parseDatasetNames(datasetOverride)
  const request: ScenarioRunSizeEstimateRequest = {
    techniques,
    include_baseline: includeBaseline,
  }
  if (targetName) {
    request.target_name = targetName
  }
  if (datasetNames.length > 0) {
    request.dataset_names = datasetNames
  }
  if (maxDatasetSizeValue !== undefined) {
    request.max_dataset_size = maxDatasetSizeValue
  }
  const harmCategories = parseDatasetNames(harmCategoriesFilter)
  const dataTypes = parseDatasetNames(dataTypesFilter)
  if (harmCategories.length > 0 || dataTypes.length > 0) {
    request.dataset_filters = {
      ...(harmCategories.length > 0 ? { harm_categories: harmCategories } : {}),
      ...(dataTypes.length > 0 ? { data_types: dataTypes } : {}),
    }
  }
  if (scenarioParams) {
    request.scenario_params = scenarioParams
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
  activeTarget: TargetInstance | null
  labels: Record<string, string>
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
  activeTarget,
  labels,
  onNavigate,
}: ScenarioDetailContentProps) {
  const styles = useScenarioDetailStyles()
  const decodedScenarioName = routerPathParamValue(encodedScenarioName)

  const [scenario, setScenario] = useState<RegisteredScenario | null>(null)
  const [scenarioStatus, setScenarioStatus] = useState<LoadStatus>('loading')
  const [scenarioError, setScenarioError] = useState<string | null>(null)
  const [targets, setTargets] = useState<TargetInstance[] | null>(null)
  const [targetsError, setTargetsError] = useState<string | null>(null)
  const [refetchCount, setRefetchCount] = useState(0)

  useEffect(() => {
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
  }, [decodedScenarioName, refetchCount])

  useEffect(() => {
    let cancelled = false
    fetchAllPages(
      (cursor) => targetsApi.listTargets(TARGET_PAGE_SIZE, cursor),
      undefined,
      (target) => target.target_registry_name,
    )
      .then((items) => {
        if (cancelled) return
        setTargets(items)
        setTargetsError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setTargets([])
        setTargetsError(toApiError(err).detail)
      })
    return () => {
      cancelled = true
    }
  }, [refetchCount])

  const handleRetry = (): void => {
    setScenarioStatus('loading')
    setScenarioError(null)
    setTargets(null)
    setTargetsError(null)
    setRefetchCount((count) => count + 1)
  }

  if (scenarioStatus === 'loading' || targets === null) {
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

  if (scenarioStatus === 'error' || targetsError) {
    return (
      <section className={styles.root} data-testid="scenario-detail" aria-label="Scenario detail">
        <div className={styles.content}>
          <Link to="/scanner" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scanners
          </Link>
          <div className={styles.centeredState} data-testid="scenario-error">
            <MessageBar intent="error">
              <MessageBarBody>{scenarioError ?? targetsError}</MessageBarBody>
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
      activeTarget={activeTarget}
      labels={labels}
      onNavigate={onNavigate}
    />
  )
}

interface ScenarioLaunchFormProps {
  scenario: RegisteredScenario
  targets: TargetInstance[]
  activeTarget: TargetInstance | null
  labels: Record<string, string>
  onNavigate: (view: ViewName) => void
}

function ScenarioLaunchForm({
  scenario,
  targets,
  activeTarget,
  labels,
  onNavigate,
}: ScenarioLaunchFormProps) {
  const styles = useScenarioDetailStyles()
  const navigate = useNavigate()
  const formId = `scenario-launch-${encodeURIComponent(scenario.scenario_name).replace(/%/g, '-')}`

  const { techniques: techniqueOptions, defaultTechniques } = useMemo(
    () => uniqueTechniqueOptions(scenario),
    [scenario],
  )
  const dynamicParameters = useMemo(
    () => scenario.supported_parameters.filter(
      (parameter) => !COMMON_SCENARIO_PARAMETER_NAMES.has(parameter.name),
    ),
    [scenario.supported_parameters],
  )
  const isBaselineForbidden = scenario.baseline_policy === 'forbidden'

  const [targetName, setTargetName] = useState(() => {
    if (activeTarget && targets.some((target) =>
      target.target_registry_name === activeTarget.target_registry_name)) {
      return activeTarget.target_registry_name
    }
    return targets[0]?.target_registry_name ?? ''
  })
  const [selectedTechniques, setSelectedTechniques] = useState<string[]>(() => defaultTechniques)
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

  const selectableTechniques = useMemo<SelectableTechnique[]>(
    () => [
      {
        ...BASELINE_TECHNIQUE,
        isBaseline: true,
        disabled: isBaselineForbidden,
      },
      ...techniqueOptions.map((technique) => ({
        ...technique,
        isBaseline: false,
        disabled: false,
      })),
    ],
    [isBaselineForbidden, techniqueOptions],
  )
  const techniques = selectedTechniques
  const maxDatasetSizeOverride = maxDatasetSize.trim()
    && maxDatasetSize !== configuredDefaultMaxDatasetSize
    ? maxDatasetSize
    : ''
  const estimateResult = useMemo(
    () => buildEstimateRequest({
      scenario,
      targetName,
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
      : JSON.stringify({ scenarioName: scenario.scenario_name, request: estimateRequest }),
    [estimateRequest, scenario.scenario_name],
  )

  useEffect(() => {
    if (estimateRequest === null || estimateRequestKey === null) {
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
  }, [estimateRequest, estimateRequestKey, scenario.scenario_name])

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
    if (isSubmittingRef.current) {
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
              <Text id="target-section-title" as="h2" size={400} weight="semibold">Target</Text>
              <Field hint="The registered target this scenario will run against.">
                <Select
                  className={styles.control}
                  value={targetName}
                  disabled={submitting}
                  onChange={(_, data) => setTargetName(data.value)}
                  data-testid="scenario-target-select"
                  aria-label="Target"
                >
                  {targets.length === 0 && <option value="">No targets configured</option>}
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
                  onClick={() => onNavigate('targets')}
                >
                  Configure target to launch
                </Button>
              )}
            </section>

            <section className={styles.section} aria-labelledby="techniques-section-title">
              <Text id="techniques-section-title" as="h2" size={400} weight="semibold">
                Techniques
              </Text>
              <Text size={200} className={styles.hint}>
                Select individual techniques, or use a tag to select or clear all techniques with that tag.
              </Text>
              {techniqueSelectionInvalid && (
                <Text className={styles.errorText} role="alert">
                  Select at least one attack technique.
                </Text>
              )}
              <div className={styles.techniqueList} role="group" aria-label="Techniques">
                {selectableTechniques.map((technique) => {
                  const selected = isTechniqueSelected(technique)
                  return (
                    <div className={styles.techniqueOption} key={technique.name}>
                    <Checkbox
                      className={styles.selectionControl}
                      label={technique.name}
                      checked={selected}
                      disabled={submitting || technique.disabled}
                      onChange={(_, data) => handleTechniqueChange(technique, data.checked === true)}
                      data-testid={technique.isBaseline ? 'baseline-checkbox' : `technique-${technique.name}`}
                    />
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
                            return (
                              <ToggleButton
                                className={styles.techniqueTag}
                                key={tag}
                                size="small"
                                appearance="outline"
                                checked={tagSelected}
                                disabled={submitting || tagMembers.length === 0}
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
                <Field
                  label="Dataset override"
                  hint="Comma-separated dataset names. Leave blank to use the scenario's default datasets."
                >
                  <Input
                    className={styles.control}
                    value={datasetOverride}
                    disabled={submitting}
                    onChange={(_, data) => setDatasetOverride(data.value)}
                    placeholder={scenario.default_datasets.join(', ') || undefined}
                    data-testid="dataset-override-input"
                  />
                </Field>
                <Field
                  label="Max dataset size"
                  hint={configuredDefaultMaxDatasetSize
                    ? `The scenario default is ${configuredDefaultMaxDatasetSize}. Edit it to override the default.`
                    : 'Enter a positive integer to limit the selected dataset size.'}
                >
                  <Input
                    className={styles.numberInput}
                    type="number"
                    min={1}
                    value={maxDatasetSize}
                    disabled={submitting}
                    onChange={(_, data) => setMaxDatasetSize(data.value)}
                    data-testid="max-dataset-size-input"
                  />
                </Field>
                <Field
                  label="Harm categories"
                  hint="Comma-separated values. A seed must match every listed category."
                >
                  <Input
                    className={styles.control}
                    value={harmCategoriesFilter}
                    disabled={submitting}
                    placeholder="cyber, violence"
                    onChange={(_, data) => setHarmCategoriesFilter(data.value)}
                    data-testid="harm-categories-filter-input"
                  />
                </Field>
                <Field
                  label="Data types"
                  hint="Comma-separated values. A seed can match any listed data type."
                >
                  <Input
                    className={styles.control}
                    value={dataTypesFilter}
                    disabled={submitting}
                    placeholder="text, image_path"
                    onChange={(_, data) => setDataTypesFilter(data.value)}
                    data-testid="data-types-filter-input"
                  />
                </Field>
                <Field label="Max concurrency">
                  <SpinButton
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
                  <SpinButton
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
                    {maxDatasetSizeOverride.trim()
                      || configuredDefaultMaxDatasetSize
                      || 'Not configured'}
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
                className={styles.launchButton}
                appearance="primary"
                type="submit"
                disabled={submitting || techniqueSelectionInvalid}
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
              }
            }}
          >
            <DialogSurface>
              <DialogBody>
                <DialogTitle>Run preview</DialogTitle>
                <DialogContent className={styles.dialogContent}>
                  <dl className={styles.previewList}>
                    <div className={styles.previewGroup}>
                      <dt>Target</dt>
                      <dd>{targetName}</dd>
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
                            {maxDatasetSize.trim() ? ` - capped at ${maxDatasetSize.trim()} each` : ''}
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
                    onClick={() => setPreviewOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    appearance="primary"
                    disabled={submitting}
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
