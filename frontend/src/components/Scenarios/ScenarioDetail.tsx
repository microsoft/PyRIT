import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'

import {
  Accordion,
  AccordionHeader,
  AccordionItem,
  AccordionPanel,
  Badge,
  Button,
  Checkbox,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Radio,
  RadioGroup,
  Select,
  Spinner,
  SpinButton,
  Text,
} from '@fluentui/react-components'
import { ArrowLeftRegular, ArrowSyncRegular, SettingsRegular } from '@fluentui/react-icons'
import { Link, useNavigate, useParams } from 'react-router'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import ParameterField, {
  type RejectedNumberInputReason,
} from '@/components/Parameters/ParameterField'
import {
  buildParametersFromForm,
  getInitialFormValues,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import type { ViewName } from '@/components/Sidebar/Navigation'
import { datasetsApi, scenariosApi, targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  Parameter,
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioDatasetSizeLimit,
  ScenarioRunEstimateResult,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunEstimateState,
  TargetInstance,
} from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'
import { routerPathParamValue, scenarioRunRoutePath } from '@/utils/routeParams'
import { targetModelName } from '@/utils/targetIdentity'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import { ScenarioRunEstimateDetails } from './ScenarioRunEstimate'
import { formatAdaptiveCapFeedback } from './scenarioAdaptiveCap'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'
import {
  techniqueSetDisplayName,
  techniqueSetMembers,
  techniqueSetOptionLabel,
} from './scenarioTechniqueSets'

/** Items requested per target page while paging through the full list. */
const TARGET_PAGE_SIZE = 200

function targetOptionLabel(target: TargetInstance): string {
  const modelName = targetModelName(target)
  return modelName
    ? `${target.target_registry_name} (${modelName})`
    : target.target_registry_name
}

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
const TEXT_ADAPTIVE_SCENARIO_NAME = 'adaptive.text_adaptive'
const CUSTOM_TECHNIQUE_SET_VALUE = '__custom__'
const MAX_ATTEMPTS_PARAMETER_NAME = 'max_attempts_per_objective'
const MAX_ATTEMPTS_DISPLAY_LABEL = 'Maximum techniques per objective'
const MAX_ATTEMPTS_DEFAULT_HINT = 'Leave blank to use the default of 3.'
const MAX_ATTEMPTS_BEHAVIOR_HINT = [
  'This is a per-objective limit, not a total-run budget.',
  'Adaptive stops after the first success, and incompatible techniques are skipped.',
  'This is separate from retries.',
].join(' ')
const MAX_ATTEMPTS_VALIDATION_MESSAGE = 'Enter a whole number of 1 or more.'
const MAX_DATASET_SIZE_VALIDATION_MESSAGE = 'Enter a whole number of 1 or more.'
const CORRECT_HIGHLIGHTED_SETTING_MESSAGE = 'Correct the highlighted setting to calculate this run.'
const MAX_DATASET_SIZE_PARAMETER: Parameter = {
  name: 'max_dataset_size',
  type_name: 'int',
  required: false,
  default: null,
  choices: null,
  is_list: false,
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

type TechniqueSelection =
  | {
      mode: 'preset'
      preset: string
    }
  | {
      mode: 'custom'
    }

interface TechniqueOptions {
  presets: string[]
  concrete: string[]
  defaultSelection: TechniqueSelection
  initialCustomTechniques: string[]
}

/** Options rendered for technique selection: exclusive presets first, then concrete techniques. */
function uniqueTechniqueOptions(scenario: RegisteredScenario): TechniqueOptions {
  const aggregateNames = new Set(scenario.aggregate_techniques)
  const defaultIsPreset = aggregateNames.has(scenario.default_technique)
  const seenPresets = new Set<string>()
  const presets: string[] = []
  for (const name of scenario.aggregate_techniques) {
    if (!seenPresets.has(name)) {
      seenPresets.add(name)
      presets.push(name)
    }
  }
  const seenConcrete = new Set<string>()
  const concrete: string[] = []
  const concreteCandidates = defaultIsPreset
    ? scenario.all_techniques
    : [scenario.default_technique, ...scenario.all_techniques]
  for (const name of concreteCandidates) {
    if (!aggregateNames.has(name) && !seenConcrete.has(name)) {
      seenConcrete.add(name)
      concrete.push(name)
    }
  }
  const defaultSelection: TechniqueSelection = defaultIsPreset
    ? { mode: 'preset', preset: scenario.default_technique }
    : { mode: 'custom' }
  const initialCustomTechniques = defaultIsPreset ? [] : [scenario.default_technique]
  return { presets, concrete, defaultSelection, initialCustomTechniques }
}

function sameStringSet(left: string[], right: string[]): boolean {
  const leftSet = new Set(left)
  const rightSet = new Set(right)
  return leftSet.size === rightSet.size && [...leftSet].every((value) => rightSet.has(value))
}

function parameterDisplayLabel(parameter: Parameter, usesAdaptiveTechniqueSelection: boolean): string {
  return usesAdaptiveTechniqueSelection && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
    ? MAX_ATTEMPTS_DISPLAY_LABEL
    : parameter.name
}

function formatParameterPreview(value: ParameterFormValue | undefined): string {
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(', ') : 'Not set'
  }
  return value?.trim() || 'Not set'
}

function maxAttemptsValidationError(value: ParameterFormValue | undefined): string | undefined {
  const raw = typeof value === 'string' ? value.trim() : ''
  if (raw.length === 0) {
    return undefined
  }
  const parsed = Number(raw)
  return Number.isSafeInteger(parsed) && parsed >= 1
    ? undefined
    : MAX_ATTEMPTS_VALIDATION_MESSAGE
}

function datasetSizeFieldLabel(limit: ScenarioDatasetSizeLimit): string {
  if (limit.override_scope === 'unsupported') {
    return 'Maximum objectives'
  }
  return limit.override_scope === 'per_dataset'
    ? 'Maximum objectives per dataset'
    : 'Maximum objectives across selected datasets'
}

function datasetSizeHint(limit: ScenarioDatasetSizeLimit): string {
  if (limit.override_scope === 'unsupported') {
    return 'This scenario manages its objective population directly and does not support a dataset-size override.'
  }
  if (limit.default_scope === 'per_dataset' && limit.default_count !== null) {
    return `Scenario default: up to ${limit.default_count.toLocaleString()} objectives from each selected dataset. Enter another whole number to override it, or leave blank to use the scenario default.`
  }
  if (limit.default_scope === 'combined' && limit.default_count !== null) {
    return `Scenario default: up to ${limit.default_count.toLocaleString()} objectives across the selected datasets. Enter another whole number to override it, or leave blank to use the scenario default.`
  }
  if (limit.default_scope === 'heterogeneous') {
    const replacement = limit.override_scope === 'per_dataset'
      ? 'a uniform per-dataset maximum'
      : 'a combined maximum'
    return `Scenario defaults vary by dataset. Enter a whole number to replace them with ${replacement}, or leave blank to keep the scenario defaults.`
  }
  const scope = limit.override_scope === 'per_dataset'
    ? 'objectives from each selected dataset'
    : 'objectives across the selected datasets'
  return `No scenario default cap. Enter a whole number to limit ${scope}, or leave blank for no additional cap.`
}

function formatDatasetSizePreview(
  limit: ScenarioDatasetSizeLimit,
  maxDatasetSize: string,
  hasOverride: boolean,
): string {
  const parsed = Number(maxDatasetSize.trim())
  if (hasOverride && Number.isSafeInteger(parsed) && parsed >= 1) {
    return limit.override_scope === 'per_dataset'
      ? `${parsed.toLocaleString()} per dataset (override)`
      : `${parsed.toLocaleString()} total (override)`
  }
  if (limit.default_scope === 'per_dataset' && limit.default_count !== null) {
    return `${limit.default_count.toLocaleString()} per dataset (scenario default)`
  }
  if (limit.default_scope === 'combined' && limit.default_count !== null) {
    return `${limit.default_count.toLocaleString()} total (scenario default)`
  }
  if (limit.default_scope === 'heterogeneous') {
    return 'Varies by dataset (scenario default)'
  }
  return 'No additional objective cap'
}

function maxDatasetSizeValidationError(value: string): string | undefined {
  const raw = value.trim()
  if (raw.length === 0) {
    return undefined
  }
  const parsed = Number(raw)
  return Number.isSafeInteger(parsed) && parsed >= 1
    ? undefined
    : MAX_DATASET_SIZE_VALIDATION_MESSAGE
}

interface BuildRunRequestInput {
  scenario: RegisteredScenario
  targetName: string
  techniques: string[]
  dynamicParameters: Parameter[]
  scenarioParamValues: Record<string, ParameterFormValue>
  selectedDatasets: string[]
  maxDatasetSize: string
  hasMaxDatasetSizeOverride: boolean
  maxConcurrency: number
  maxRetries: number
  includeBaseline: boolean
  labels: Record<string, string>
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

type EstimateRequestState =
  | {
      status: 'resolved'
      requestKey: string
      result: ScenarioRunEstimateResult
    }
  | {
      status: 'error'
      requestKey: string
      summary: string
      note?: string
      maxAttemptsError?: string
    }

interface MappedEstimateError {
  summary: string
  note?: string
  maxAttemptsError?: string
}

interface AdaptiveCandidateMetadata {
  scopeKey: string
  maximum: number
}

interface AdaptiveLimitNotice {
  scopeKey: string
  message: string
  validationState: 'none' | 'warning'
}

function mapEstimateError(error: unknown): MappedEstimateError {
  const detail = toApiError(error).detail
  if (detail.includes(MAX_ATTEMPTS_PARAMETER_NAME)) {
    return {
      summary: CORRECT_HIGHLIGHTED_SETTING_MESSAGE,
      maxAttemptsError: MAX_ATTEMPTS_VALIDATION_MESSAGE,
    }
  }
  return {
    summary: 'Run size couldn’t be updated.',
    note: detail,
  }
}

function buildRunRequest({
  scenario,
  targetName,
  techniques,
  dynamicParameters,
  scenarioParamValues,
  selectedDatasets,
  maxDatasetSize,
  hasMaxDatasetSizeOverride,
  maxConcurrency,
  maxRetries,
  includeBaseline,
  labels,
}: BuildRunRequestInput): BuildRunRequestResult {
  if (!targetName) {
    return { ok: false, error: 'Select a target.' }
  }
  if (techniques.length === 0) {
    return { ok: false, error: 'Select at least one technique.' }
  }
  if (scenario.default_datasets.length > 0 && selectedDatasets.length === 0) {
    return { ok: false, error: 'Select at least one dataset.' }
  }
  if (dynamicParameters.some((parameter) => parameter.name === MAX_ATTEMPTS_PARAMETER_NAME)) {
    const maxAttemptsError = maxAttemptsValidationError(
      scenarioParamValues[MAX_ATTEMPTS_PARAMETER_NAME],
    )
    if (maxAttemptsError) {
      return { ok: false, error: maxAttemptsError }
    }
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
    if (hasMaxDatasetSizeOverride) {
      if (scenario.dataset_size_limit.override_scope === 'unsupported') {
        return { ok: false, error: 'This scenario does not support a dataset-size override.' }
      }
      maxDatasetSizeValue = parsed
    }
  }
  if (
    !Number.isInteger(maxConcurrency)
    || maxConcurrency < MIN_MAX_CONCURRENCY
    || maxConcurrency > MAX_MAX_CONCURRENCY
  ) {
    return {
      ok: false,
      error: `Max concurrency must be an integer from ${MIN_MAX_CONCURRENCY} to ${MAX_MAX_CONCURRENCY}.`,
    }
  }
  if (
    !Number.isInteger(maxRetries)
    || maxRetries < MIN_MAX_RETRIES
    || maxRetries > MAX_MAX_RETRIES
  ) {
    return {
      ok: false,
      error: `Max retries must be an integer from ${MIN_MAX_RETRIES} to ${MAX_MAX_RETRIES}.`,
    }
  }

  const request: RunScenarioRequest = {
    scenario_name: scenario.scenario_name,
    target_name: targetName,
    techniques,
    max_concurrency: maxConcurrency,
    max_retries: maxRetries,
    include_baseline: includeBaseline,
    labels,
  }
  if (!sameStringSet(selectedDatasets, scenario.default_datasets)) {
    request.dataset_names = selectedDatasets
  }
  if (maxDatasetSizeValue !== undefined) {
    request.max_dataset_size = maxDatasetSizeValue
  }
  if (scenarioParams) {
    request.scenario_params = scenarioParams
  }
  return { ok: true, request }
}

function buildEstimateRequest(request: RunScenarioRequest): ScenarioRunSizeEstimateRequest {
  const estimateRequest: ScenarioRunSizeEstimateRequest = {
    target_name: request.target_name,
    techniques: request.techniques,
    include_baseline: request.include_baseline,
  }
  if (request.dataset_names !== undefined) {
    estimateRequest.dataset_names = request.dataset_names
  }
  if (request.max_dataset_size !== undefined) {
    estimateRequest.max_dataset_size = request.max_dataset_size
  }
  if (request.dataset_filters !== undefined) {
    estimateRequest.dataset_filters = request.dataset_filters
  }
  if (request.scenario_params !== undefined) {
    estimateRequest.scenario_params = request.scenario_params
  }
  return estimateRequest
}

type DatasetCatalogStatus = 'loading' | 'success' | 'error'

interface DatasetPickerProps {
  availableDatasets: string[]
  defaultDatasets: string[]
  selectedDatasets: string[]
  status: DatasetCatalogStatus
  error: string | null
  disabled: boolean
  invalid: boolean
  onChange: (name: string, checked: boolean) => void
  onRestoreDefaults: () => void
}

function DatasetPicker({
  availableDatasets,
  defaultDatasets,
  selectedDatasets,
  status,
  error,
  disabled,
  invalid,
  onChange,
  onRestoreDefaults,
}: DatasetPickerProps) {
  const styles = useScenarioDetailStyles()
  const [query, setQuery] = useState('')
  const selectedSet = useMemo(() => new Set(selectedDatasets), [selectedDatasets])
  const defaultSet = useMemo(() => new Set(defaultDatasets), [defaultDatasets])
  const orderedDatasets = useMemo(() => {
    const names = [...new Set([...availableDatasets, ...defaultDatasets])]
    return names.sort((left, right) => {
      const leftPriority = selectedSet.has(left) ? 0 : defaultSet.has(left) ? 1 : 2
      const rightPriority = selectedSet.has(right) ? 0 : defaultSet.has(right) ? 1 : 2
      return leftPriority - rightPriority || left.localeCompare(right)
    })
  }, [availableDatasets, defaultDatasets, defaultSet, selectedSet])
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const visibleDatasets = normalizedQuery.length === 0
    ? orderedDatasets
    : orderedDatasets.filter((name) => name.toLocaleLowerCase().includes(normalizedQuery))
  const selectedCount = selectedDatasets.length

  return (
    <>
      <div className={styles.datasetPickerHeader}>
        <Text aria-live="polite">
          {selectedCount.toLocaleString()} dataset{selectedCount === 1 ? '' : 's'} selected
        </Text>
        <Button
          className={styles.touchTarget}
          appearance="subtle"
          icon={<ArrowSyncRegular />}
          disabled={disabled || sameStringSet(selectedDatasets, defaultDatasets)}
          onClick={onRestoreDefaults}
          data-testid="restore-default-datasets"
        >
          Restore defaults
        </Button>
      </div>
      <Field
        label="Search datasets"
        hint="Search registered datasets, then select the datasets this run should use."
        validationState={invalid ? 'error' : 'none'}
        validationMessage={invalid ? 'Select at least one dataset.' : undefined}
      >
        <Input
          className={styles.control}
          value={query}
          disabled={disabled}
          onChange={(_, data) => setQuery(data.value)}
          placeholder="Search datasets"
          aria-label="Search datasets"
          data-testid="dataset-search-input"
        />
      </Field>
      {status === 'loading' && (
        <Text size={200} className={styles.hint} role="status" data-testid="dataset-catalog-loading">
          Loading registered datasets…
        </Text>
      )}
      {status === 'error' && (
        <MessageBar intent="error" data-testid="dataset-catalog-error">
          <MessageBarBody role="alert">
            Registered datasets couldn’t be loaded. Scenario defaults remain available.
            {error ? ` ${error}` : ''}
          </MessageBarBody>
        </MessageBar>
      )}
      <div
        className={styles.datasetList}
        role="group"
        aria-label="Datasets"
        data-testid="dataset-picker-list"
      >
        {visibleDatasets.length > 0 ? (
          visibleDatasets.map((name) => (
            <Checkbox
              className={styles.selectionControl}
              key={name}
              label={name}
              checked={selectedSet.has(name)}
              disabled={disabled}
              onChange={(_, data) => onChange(name, data.checked === true)}
              data-testid={`dataset-${name}`}
            />
          ))
        ) : (
          <Text className={styles.datasetEmptyState}>No datasets match this search.</Text>
        )}
      </div>
    </>
  )
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
          <Link to="/scenarios" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scenarios
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
          <Link to="/scenarios" className={styles.backLink}>
            <ArrowLeftRegular /> Back to scenarios
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
    />
  )
}

interface ScenarioLaunchFormProps {
  scenario: RegisteredScenario
  targets: TargetInstance[]
  activeTarget: TargetInstance | null
  labels: Record<string, string>
}

function ScenarioLaunchForm({ scenario, targets, activeTarget, labels }: ScenarioLaunchFormProps) {
  const styles = useScenarioDetailStyles()
  const navigate = useNavigate()
  const formId = `scenario-launch-${encodeURIComponent(scenario.scenario_name).replace(/%/g, '-')}`

  const { presets, concrete, defaultSelection, initialCustomTechniques } = useMemo(
    () => uniqueTechniqueOptions(scenario),
    [scenario],
  )
  const techniqueSummaries = useMemo(
    () => new Map(scenario.technique_summaries.map((summary) => [summary.name, summary])),
    [scenario.technique_summaries],
  )
  const dynamicParameters = useMemo(
    () => scenario.supported_parameters.filter(
      (parameter) => !COMMON_SCENARIO_PARAMETER_NAMES.has(parameter.name),
    ),
    [scenario.supported_parameters],
  )
  const isBaselineForbidden = scenario.baseline_policy === 'forbidden'
  const usesAdaptiveTechniqueSelection = scenario.scenario_name === TEXT_ADAPTIVE_SCENARIO_NAME

  const [targetName, setTargetName] = useState(() => {
    if (activeTarget && targets.some((target) =>
      target.target_registry_name === activeTarget.target_registry_name)) {
      return activeTarget.target_registry_name
    }
    return targets[0].target_registry_name
  })
  const [techniqueSelection, setTechniqueSelection] = useState<TechniqueSelection>(() => defaultSelection)
  const [customTechniques, setCustomTechniques] = useState<string[]>(() => initialCustomTechniques)
  const [baselineChecked, setBaselineChecked] = useState(
    () => !isBaselineForbidden && scenario.include_baseline_by_default,
  )
  const [availableDatasets, setAvailableDatasets] = useState<string[]>(() => [...scenario.default_datasets])
  const [selectedDatasets, setSelectedDatasets] = useState<string[]>(() => [...scenario.default_datasets])
  const [datasetCatalogStatus, setDatasetCatalogStatus] = useState<DatasetCatalogStatus>('loading')
  const [datasetCatalogError, setDatasetCatalogError] = useState<string | null>(null)
  const [maxDatasetSize, setMaxDatasetSize] = useState(() => {
    const limit = scenario.dataset_size_limit
    return limit.default_count !== null && limit.default_scope === limit.override_scope
      ? String(limit.default_count)
      : ''
  })
  const [hasMaxDatasetSizeOverride, setHasMaxDatasetSizeOverride] = useState(false)
  const [maxDatasetSizeInputRejected, setMaxDatasetSizeInputRejected] = useState(false)
  const [maxConcurrency, setMaxConcurrency] = useState(DEFAULT_MAX_CONCURRENCY)
  const [maxRetries, setMaxRetries] = useState(DEFAULT_MAX_RETRIES)
  const [scenarioParamValues, setScenarioParamValues] = useState<Record<string, ParameterFormValue>>(() => {
    const initialValues = getInitialFormValues(dynamicParameters)
    if (usesAdaptiveTechniqueSelection && MAX_ATTEMPTS_PARAMETER_NAME in initialValues) {
      initialValues[MAX_ATTEMPTS_PARAMETER_NAME] = ''
    }
    return initialValues
  })
  const [validationError, setValidationError] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [estimateRequestState, setEstimateRequestState] = useState<EstimateRequestState | null>(null)
  const [launchMaxAttemptsError, setLaunchMaxAttemptsError] = useState<string | null>(null)
  const [maxAttemptsInputRejected, setMaxAttemptsInputRejected] = useState(false)
  const [adaptiveCandidateMetadata, setAdaptiveCandidateMetadata] =
    useState<AdaptiveCandidateMetadata | null>(null)
  const [adaptiveLimitNotice, setAdaptiveLimitNotice] = useState<AdaptiveLimitNotice | null>(null)
  // Synchronous guard against a double-submit racing ahead of the state update.
  const isSubmittingRef = useRef(false)
  const estimateSequenceRef = useRef(0)
  const customTechniquesInitializedRef = useRef(defaultSelection.mode === 'custom')
  const hasResolvedAdaptiveMetadataRef = useRef(false)

  const techniques = useMemo(
    () => techniqueSelection.mode === 'preset'
      ? [techniqueSelection.preset]
      : customTechniques,
    [customTechniques, techniqueSelection],
  )
  const adaptiveCandidateScopeKey = useMemo(
    () => JSON.stringify({ targetName, techniques }),
    [targetName, techniques],
  )
  const knownAdaptiveCandidateMaximum =
    adaptiveCandidateMetadata?.scopeKey === adaptiveCandidateScopeKey
      ? adaptiveCandidateMetadata.maximum
      : null
  const adaptiveSelectionDisplayName = techniqueSelection.mode === 'preset'
    ? techniqueSetDisplayName(scenario, techniqueSelection.preset)
    : 'Custom selection'
  const requestResult = useMemo(
    () => buildRunRequest({
      scenario,
      targetName,
      techniques,
      dynamicParameters,
      scenarioParamValues,
      selectedDatasets,
      maxDatasetSize,
      hasMaxDatasetSizeOverride,
      maxConcurrency,
      maxRetries,
      includeBaseline: isBaselineForbidden ? false : baselineChecked,
      labels,
    }),
    [
      baselineChecked,
      dynamicParameters,
      isBaselineForbidden,
      labels,
      maxConcurrency,
      maxDatasetSize,
      hasMaxDatasetSizeOverride,
      maxRetries,
      scenario,
      scenarioParamValues,
      selectedDatasets,
      targetName,
      techniques,
    ],
  )
  const estimateRequest = useMemo(
    () => {
      if (!requestResult.ok || maxAttemptsInputRejected || maxDatasetSizeInputRejected) {
        return null
      }
      const request = buildEstimateRequest(requestResult.request)
      if (!usesAdaptiveTechniqueSelection || !request.scenario_params) {
        return request
      }
      const scenarioParams = { ...request.scenario_params }
      const configuredMaximum = scenarioParams[MAX_ATTEMPTS_PARAMETER_NAME]
      if (knownAdaptiveCandidateMaximum === null || knownAdaptiveCandidateMaximum === 0) {
        delete scenarioParams[MAX_ATTEMPTS_PARAMETER_NAME]
      } else if (
        typeof configuredMaximum === 'number'
        && configuredMaximum > knownAdaptiveCandidateMaximum
      ) {
        scenarioParams[MAX_ATTEMPTS_PARAMETER_NAME] = knownAdaptiveCandidateMaximum
      }
      return {
        ...request,
        scenario_params: Object.keys(scenarioParams).length > 0 ? scenarioParams : undefined,
      }
    },
    [
      knownAdaptiveCandidateMaximum,
      maxAttemptsInputRejected,
      maxDatasetSizeInputRejected,
      requestResult,
      usesAdaptiveTechniqueSelection,
    ],
  )
  const estimateRequestKey = useMemo(
    () => estimateRequest === null
      ? null
      : JSON.stringify({ scenarioName: scenario.scenario_name, request: estimateRequest }),
    [estimateRequest, scenario.scenario_name],
  )

  useEffect(() => {
    let cancelled = false
    datasetsApi
      .listDatasets()
      .then((response) => {
        if (cancelled) return
        setAvailableDatasets([...new Set([
          ...scenario.default_datasets,
          ...response.items.map((item) => item.name),
        ])])
        setDatasetCatalogStatus('success')
        setDatasetCatalogError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setAvailableDatasets([...scenario.default_datasets])
        setDatasetCatalogStatus('error')
        setDatasetCatalogError(toApiError(err).detail)
      })
    return () => {
      cancelled = true
    }
  }, [scenario.default_datasets])

  useEffect(() => {
    const requestSequence = estimateSequenceRef.current + 1
    estimateSequenceRef.current = requestSequence
    if (estimateRequest === null || estimateRequestKey === null) {
      return
    }

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
          const adaptiveDetails =
            result.status === 'available' || result.status === 'conditional'
              ? result.estimate.adaptiveDetails
              : null
          if (usesAdaptiveTechniqueSelection && adaptiveDetails) {
            const maximum = adaptiveDetails.candidateTechniqueCount
            const hadResolvedAdaptiveMetadata = hasResolvedAdaptiveMetadataRef.current
            hasResolvedAdaptiveMetadataRef.current = true
            setAdaptiveCandidateMetadata({ scopeKey: adaptiveCandidateScopeKey, maximum })
            const rawValue = scenarioParamValues[MAX_ATTEMPTS_PARAMETER_NAME]
            const parsedValue = typeof rawValue === 'string' && rawValue.trim() !== ''
              ? Number(rawValue)
              : null
            const configuredValue = parsedValue ?? adaptiveDetails.maxAttemptsPerObjective
            const isDefaultReduction = parsedValue === null
            if (
              maximum > 0
              && Number.isSafeInteger(configuredValue)
              && configuredValue > maximum
            ) {
              setScenarioParamValues((current) => ({
                ...current,
                [MAX_ATTEMPTS_PARAMETER_NAME]: String(maximum),
              }))
              setAdaptiveLimitNotice(
                isDefaultReduction
                  ? {
                      scopeKey: adaptiveCandidateScopeKey,
                      message: `The scenario default of ${configuredValue.toLocaleString()} is reduced to ${
                        maximum.toLocaleString()
                      } because ${adaptiveSelectionDisplayName} provides ${maximum.toLocaleString()} compatible ${
                        maximum === 1 ? 'technique' : 'techniques'
                      } for this target.`,
                      validationState: 'none',
                    }
                  : hadResolvedAdaptiveMetadata
                  ? {
                      scopeKey: adaptiveCandidateScopeKey,
                      message: `Reduced to ${maximum.toLocaleString()} because ${
                        adaptiveSelectionDisplayName
                      } provides ${maximum.toLocaleString()} compatible ${
                        maximum === 1 ? 'technique' : 'techniques'
                      } for this target.`,
                      validationState: 'warning',
                    }
                  : null,
              )
            }
          }
          setEstimateRequestState({
            status: 'resolved',
            requestKey: estimateRequestKey,
            result,
          })
        })
        .catch((err: unknown) => {
          if (
            controller.signal.aborted
            || requestSequence !== estimateSequenceRef.current
          ) {
            return
          }
          const mappedError = mapEstimateError(err)
          setEstimateRequestState({
            status: 'error',
            requestKey: estimateRequestKey,
            ...mappedError,
          })
        })
    }, ESTIMATE_DEBOUNCE_MS)

    return () => {
      window.clearTimeout(debounceTimer)
      controller.abort()
    }
  }, [
    adaptiveCandidateScopeKey,
    adaptiveSelectionDisplayName,
    estimateRequest,
    estimateRequestKey,
    scenario.scenario_name,
    scenarioParamValues,
    usesAdaptiveTechniqueSelection,
  ])

  const currentResolvedEstimate = estimateRequestState?.requestKey === estimateRequestKey
    && estimateRequestState.status === 'resolved'
    ? estimateRequestState.result
    : null
  const currentResolvedRunEstimate = currentResolvedEstimate
    && (currentResolvedEstimate.status === 'available' || currentResolvedEstimate.status === 'conditional')
    ? currentResolvedEstimate.estimate
    : null
  const currentResolvedAdaptiveDetails = currentResolvedRunEstimate
    ? currentResolvedRunEstimate.adaptiveDetails
    : null
  const adaptiveCandidateMaximum = currentResolvedAdaptiveDetails?.candidateTechniqueCount
    ?? knownAdaptiveCandidateMaximum
  const adaptiveCandidateAvailability = adaptiveCandidateMaximum === null
    ? undefined
    : `${adaptiveSelectionDisplayName} provides ${adaptiveCandidateMaximum.toLocaleString()} compatible ${
      adaptiveCandidateMaximum === 1 ? 'technique' : 'techniques'
    } for this target.`
  const maxAttemptsParameter = dynamicParameters.find(
    (parameter) => parameter.name === MAX_ATTEMPTS_PARAMETER_NAME,
  )
  const maxAttemptsDefault = Number(maxAttemptsParameter?.default ?? 3)
  const adaptiveDefaultIsReduced = usesAdaptiveTechniqueSelection
    && adaptiveCandidateMaximum !== null
    && adaptiveCandidateMaximum > 0
    && Number.isSafeInteger(maxAttemptsDefault)
    && maxAttemptsDefault > adaptiveCandidateMaximum
  const adaptiveDefaultHint = adaptiveDefaultIsReduced
    ? `Blank restores the bounded default of ${adaptiveCandidateMaximum.toLocaleString()} techniques per objective for this target.`
    : MAX_ATTEMPTS_DEFAULT_HINT
  const maxAttemptsRawValue = scenarioParamValues[MAX_ATTEMPTS_PARAMETER_NAME]
  const maxAttemptsNumericValue = typeof maxAttemptsRawValue === 'string'
    && maxAttemptsRawValue.trim() !== ''
    ? Number(maxAttemptsRawValue)
    : null
  const maxAttemptsExceedsCandidateMaximum = adaptiveCandidateMaximum !== null
    && maxAttemptsNumericValue !== null
    && maxAttemptsNumericValue > adaptiveCandidateMaximum
  const adaptiveMetadataUnavailable = usesAdaptiveTechniqueSelection
    && adaptiveCandidateMaximum === null
  const noAdaptiveCandidatesError = usesAdaptiveTechniqueSelection && adaptiveCandidateMaximum === 0
    ? 'No compatible techniques are available for this target. Choose a different technique set or target.'
    : undefined
  const maxAttemptsClientError = usesAdaptiveTechniqueSelection
    ? maxAttemptsInputRejected
      ? MAX_ATTEMPTS_VALIDATION_MESSAGE
      : maxAttemptsValidationError(scenarioParamValues[MAX_ATTEMPTS_PARAMETER_NAME])
    : undefined
  const currentEstimateError = estimateRequestState?.requestKey === estimateRequestKey
    && estimateRequestState.status === 'error'
    ? estimateRequestState
    : null
  const maxAttemptsFieldError = maxAttemptsClientError
    ?? noAdaptiveCandidatesError
    ?? currentEstimateError?.maxAttemptsError
    ?? launchMaxAttemptsError
    ?? undefined
  const maxDatasetSizeFieldError = maxDatasetSizeInputRejected
    ? MAX_DATASET_SIZE_VALIDATION_MESSAGE
    : maxDatasetSizeValidationError(maxDatasetSize)

  let estimateState: ScenarioRunEstimateState
  if (maxAttemptsFieldError || maxDatasetSizeFieldError) {
    estimateState = {
      status: 'unavailable',
      scope: 'request',
      label: CORRECT_HIGHLIGHTED_SETTING_MESSAGE,
    }
  } else if (!requestResult.ok) {
    estimateState = {
      status: 'unavailable',
      scope: 'request',
      label: 'Complete the required configuration to request an estimate.',
      note: requestResult.error,
    }
  } else if (currentResolvedEstimate) {
    estimateState = currentResolvedEstimate
  } else if (currentEstimateError) {
    estimateState = {
      status: 'unavailable',
      scope: 'request',
      label: currentEstimateError.summary,
      note: currentEstimateError.note,
    }
  } else {
    estimateState = { status: 'loading', scope: 'request' }
  }
  const estimateRequestBlocked = estimateRequestState?.requestKey === estimateRequestKey
    && estimateRequestState.status === 'error'

  const handleTechniqueModeChange = (value: string): void => {
    if (value === CUSTOM_TECHNIQUE_SET_VALUE) {
      if (!customTechniquesInitializedRef.current) {
        const members = techniqueSelection.mode === 'preset'
          ? techniqueSetMembers(scenario, techniqueSelection.preset)
          : []
        const concreteSet = new Set(concrete)
        setCustomTechniques(members.filter((member) => concreteSet.has(member)))
        customTechniquesInitializedRef.current = true
      }
      setTechniqueSelection({ mode: 'custom' })
    } else {
      setTechniqueSelection({ mode: 'preset', preset: value })
    }
    setValidationError(null)
  }

  const handleConcreteChange = (name: string, checked: boolean): void => {
    setCustomTechniques((current) => {
      if (checked) {
        return current.includes(name)
          ? current
          : [...current, name]
      }
      return current.filter((technique) => technique !== name)
    })
    setValidationError(null)
  }

  const handleDatasetChange = (name: string, checked: boolean): void => {
    setSelectedDatasets((current) => {
      if (checked) {
        return current.includes(name) ? current : [...current, name]
      }
      return current.filter((dataset) => dataset !== name)
    })
    setValidationError(null)
  }

  const updateScenarioParam = (name: string, value: ParameterFormValue): void => {
    setScenarioParamValues((current) => ({ ...current, [name]: value }))
    if (name === MAX_ATTEMPTS_PARAMETER_NAME) {
      setMaxAttemptsInputRejected(false)
      setAdaptiveLimitNotice(null)
      setLaunchMaxAttemptsError(null)
      setApiError(null)
    }
    setValidationError(null)
  }

  const rejectScenarioParamInput = (
    name: string,
    reason: RejectedNumberInputReason,
    retainedValue: string,
  ): void => {
    if (name !== MAX_ATTEMPTS_PARAMETER_NAME) {
      return
    }

    if (reason === 'above-max' && adaptiveCandidateMaximum !== null) {
      setScenarioParamValues((current) => ({
        ...current,
        [name]: String(adaptiveCandidateMaximum),
      }))
      setMaxAttemptsInputRejected(false)
      setAdaptiveLimitNotice({
        scopeKey: adaptiveCandidateScopeKey,
        message: `Maximum reached: ${adaptiveCandidateAvailability}`,
        validationState: 'warning',
      })
      setLaunchMaxAttemptsError(null)
      setApiError(null)
      setValidationError(null)
      return
    }
    setScenarioParamValues((current) => ({ ...current, [name]: retainedValue }))
    setMaxAttemptsInputRejected(true)
    setAdaptiveLimitNotice(null)
    setLaunchMaxAttemptsError(null)
    setApiError(null)
    setValidationError(null)
  }

  const updateMaxDatasetSize = (_name: string, value: ParameterFormValue): void => {
    const nextValue = typeof value === 'string' ? value : ''
    const parsed = Number(nextValue.trim())
    const limit = scenario.dataset_size_limit
    const matchesRepresentableDefault = nextValue.trim() !== ''
      && limit.default_count !== null
      && limit.default_scope === limit.override_scope
      && parsed === limit.default_count
    setMaxDatasetSize(nextValue)
    setHasMaxDatasetSizeOverride(nextValue.trim() !== '' && !matchesRepresentableDefault)
    setMaxDatasetSizeInputRejected(false)
    setValidationError(null)
    setApiError(null)
  }

  const rejectMaxDatasetSizeInput = (
    _name: string,
    _reason: RejectedNumberInputReason,
    retainedValue: string,
  ): void => {
    setMaxDatasetSize(retainedValue)
    setMaxDatasetSizeInputRejected(true)
    setValidationError(null)
    setApiError(null)
  }

  const restoreDefaultDatasetSize = (): void => {
    const limit = scenario.dataset_size_limit
    setMaxDatasetSize(
      limit.default_count !== null && limit.default_scope === limit.override_scope
        ? String(limit.default_count)
        : '',
    )
    setHasMaxDatasetSizeOverride(false)
    setMaxDatasetSizeInputRejected(false)
    setValidationError(null)
    setApiError(null)
  }

  const handleSubmit = async (): Promise<void> => {
    if (isSubmittingRef.current) {
      return
    }

    setApiError(null)
    if (
      adaptiveMetadataUnavailable
      || adaptiveCandidateMaximum === 0
      || maxAttemptsExceedsCandidateMaximum
    ) {
      setValidationError('Wait for the compatible technique limit to update.')
      return
    }
    if (!requestResult.ok) {
      setValidationError(requestResult.error)
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
    } catch (err: unknown) {
      const mappedError = mapEstimateError(err)
      if (mappedError.maxAttemptsError) {
        setLaunchMaxAttemptsError(mappedError.maxAttemptsError)
        setApiError(null)
      } else {
        setApiError(mappedError.note ?? mappedError.summary)
      }
    } finally {
      isSubmittingRef.current = false
      setSubmitting(false)
    }
  }

  const handleFormSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    void handleSubmit()
  }

  const techniqueSelectionInvalid =
    techniqueSelection.mode === 'custom' && customTechniques.length === 0
  const datasetSelectionInvalid = scenario.default_datasets.length > 0 && selectedDatasets.length === 0
  const datasetsAreDefaults = sameStringSet(selectedDatasets, scenario.default_datasets)
  const datasetSizePreview = formatDatasetSizePreview(
    scenario.dataset_size_limit,
    maxDatasetSize,
    hasMaxDatasetSizeOverride,
  )
  const presetMembers = techniqueSelection.mode === 'preset'
    ? techniqueSetMembers(scenario, techniqueSelection.preset)
    : []
  const atAdaptiveCandidateMaximum = adaptiveCandidateMaximum !== null
    && adaptiveCandidateMaximum > 0
    && maxAttemptsNumericValue === adaptiveCandidateMaximum
  const scopedAdaptiveLimitNotice = adaptiveLimitNotice?.scopeKey === adaptiveCandidateScopeKey
    ? adaptiveLimitNotice
    : null
  const currentAdaptiveLimitNotice = scopedAdaptiveLimitNotice?.message
    ?? (atAdaptiveCandidateMaximum && adaptiveCandidateAvailability
      ? `Maximum reached: ${adaptiveCandidateAvailability}`
      : undefined)
  const currentAdaptiveLimitValidationState = scopedAdaptiveLimitNotice?.validationState
    ?? (atAdaptiveCandidateMaximum && adaptiveCandidateAvailability ? 'warning' : 'none')
  const currentBaselineCount = currentResolvedRunEstimate?.components
    .filter((component) => component.isBaseline)
    .reduce((sum, component) => sum + component.count, 0)
  const baselineHint = isBaselineForbidden
    ? 'This scenario does not support sending objectives directly without an attack technique, so a direct comparison cannot be included.'
    : baselineChecked && currentBaselineCount
      ? `Adds ${currentBaselineCount.toLocaleString()} direct ${
        currentBaselineCount === 1 ? 'baseline attack' : 'baseline attacks'
      } for the current objectives.`
      : 'Also send each selected objective directly, without an attack technique. This provides a comparison point for measuring whether the selected techniques improve results and adds one planned attack per objective.'
  const adaptiveCapFeedback = currentResolvedAdaptiveDetails
    ? formatAdaptiveCapFeedback({
        selectedCandidateCount: currentResolvedAdaptiveDetails.selectedCandidateTechniqueCount,
        compatibleCandidateCount: currentResolvedAdaptiveDetails.candidateTechniqueCount,
        limit: currentResolvedAdaptiveDetails.maxAttemptsPerObjective,
        effectiveMaximum: currentResolvedAdaptiveDetails.techniquesPerObjectiveUpperBound,
      })
    : undefined

  return (
    <section
      className={styles.root}
      data-testid="scenario-detail"
      aria-labelledby="scenario-detail-title"
    >
      <div className={styles.content}>
        <Link to="/scenarios" className={styles.backLink}>
          <ArrowLeftRegular /> Back to scenarios
        </Link>

        <div className={styles.headerText}>
          <Text id="scenario-detail-title" as="h1" size={600} weight="semibold">
            {scenario.scenario_name}
          </Text>
          <Text size={200} className={styles.scenarioMetadata}>
            {scenario.scenario_type} · v{scenario.scenario_version}
          </Text>
          <MarkdownContent
            content={normalizeScenarioMarkdown(
              scenario.description_markdown || scenario.description,
            )}
            className={styles.description}
            testId="scenario-detail-description"
          />
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
                Choose a predefined set, or choose Custom to select techniques individually.
              </Text>
              {usesAdaptiveTechniqueSelection && (
                <>
                  <Text size={200} className={styles.hint}>
                    Core, Extra, Light, Multi-turn, and Single-turn reflect tags on PyRIT&apos;s registered
                    techniques. All is generated from the catalog; Recommended is curated for this scenario.
                  </Text>
                  <MessageBar intent="info">
                    <MessageBarBody>
                      Adaptive uses these as a candidate pool. It tracks one progress step per compatible objective.
                      Adaptive tries no more than the configured maximum or the compatible candidate count, whichever
                      is smaller, and stops after the first success. Adding techniques changes the candidate pool, not
                      the number of progress steps; compatibility can still change how many objectives can run.
                    </MessageBarBody>
                  </MessageBar>
                </>
              )}
              <div className={styles.techniqueGroups}>
                <Field label="Technique set">
                  <RadioGroup
                    value={techniqueSelection.mode === 'preset'
                      ? techniqueSelection.preset
                      : CUSTOM_TECHNIQUE_SET_VALUE}
                    onChange={(_, data) => handleTechniqueModeChange(data.value)}
                    aria-label="Technique set"
                  >
                    {presets.map((name) => (
                      <Radio
                        className={styles.selectionControl}
                        key={name}
                        label={techniqueSetOptionLabel(scenario, name)}
                        value={name}
                        disabled={submitting}
                        data-testid={`technique-${name}`}
                      />
                    ))}
                    <Radio
                      className={styles.selectionControl}
                      label="Custom"
                      value={CUSTOM_TECHNIQUE_SET_VALUE}
                      disabled={submitting}
                      data-testid="technique-mode-custom"
                    />
                  </RadioGroup>
                </Field>
                {techniqueSelection.mode === 'preset' && (
                  <div
                    className={styles.resolvedMembers}
                    data-testid="selected-technique-set-members"
                    aria-live="polite"
                  >
                    <Text size={200} weight="semibold">Included techniques</Text>
                    {presetMembers.length > 0 ? (
                      <div className={styles.previewBadges}>
                        {presetMembers.map((name) => (
                          <Badge key={name} appearance="outline">{name}</Badge>
                        ))}
                      </div>
                    ) : (
                      <Text size={200} className={styles.hint}>
                        No concrete members were supplied for this technique set.
                      </Text>
                    )}
                  </div>
                )}
                {techniqueSelection.mode === 'custom' && (
                  <Field
                    label="Individual techniques"
                    validationState={techniqueSelectionInvalid ? 'error' : 'none'}
                    validationMessage={techniqueSelectionInvalid
                      ? 'Select at least one technique.'
                      : undefined}
                  >
                    {concrete.length > 0 ? (
                      <div className={styles.checkboxGroup} role="group" aria-label="Individual techniques">
                        {concrete.map((name) => {
                          const summary = techniqueSummaries.get(name)
                          return (
                            <div key={name}>
                              <Checkbox
                                className={styles.selectionControl}
                                label={name}
                                checked={customTechniques.includes(name)}
                                disabled={submitting}
                                onChange={(_, data) => handleConcreteChange(name, data.checked === true)}
                                data-testid={`technique-${name}`}
                              />
                              {summary?.description && (
                                <Text size={200} className={styles.hint}>{summary.description}</Text>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    ) : (
                      <Text size={200} className={styles.hint}>
                        No concrete techniques are registered for custom selection.
                      </Text>
                    )}
                  </Field>
                )}
              </div>
            </section>

            <section className={styles.section} aria-labelledby="baseline-section-title">
              <Text id="baseline-section-title" as="h2" size={400} weight="semibold">
                Baseline
              </Text>
              <Field hint={baselineHint}>
                <Checkbox
                  className={styles.selectionControl}
                  checked={baselineChecked}
                  disabled={submitting || isBaselineForbidden}
                  label="Include direct baseline comparison"
                  onChange={(_, data) => setBaselineChecked(data.checked === true)}
                  data-testid="baseline-checkbox"
                />
              </Field>
            </section>

            {dynamicParameters.length > 0 && (
              <section className={styles.section} aria-labelledby="parameters-section-title">
                <Text id="parameters-section-title" as="h2" size={400} weight="semibold">
                  Scenario parameters
                </Text>
                <div className={styles.dynamicParameters}>
                  {dynamicParameters.map((parameter) => (
                    <ParameterField
                      key={parameter.name}
                      parameter={parameter}
                      value={scenarioParamValues[parameter.name]}
                      disabled={
                        submitting
                        || (
                          usesAdaptiveTechniqueSelection
                          && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
                          && (adaptiveMetadataUnavailable || adaptiveCandidateMaximum === 0)
                        )
                      }
                      onChange={updateScenarioParam}
                      displayLabel={usesAdaptiveTechniqueSelection
                        && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
                        ? MAX_ATTEMPTS_DISPLAY_LABEL
                        : undefined}
                      displayHint={usesAdaptiveTechniqueSelection
                        && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
                        ? `${adaptiveDefaultHint} ${MAX_ATTEMPTS_BEHAVIOR_HINT}${adaptiveCapFeedback
                          ? ` ${adaptiveCapFeedback}`
                          : ''}`
                        : undefined}
                      validationState={usesAdaptiveTechniqueSelection
                        && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
                        ? maxAttemptsFieldError
                          ? 'error'
                          : currentAdaptiveLimitNotice
                            ? currentAdaptiveLimitValidationState
                            : 'none'
                        : 'none'}
                      validationMessage={usesAdaptiveTechniqueSelection
                        && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
                        ? maxAttemptsFieldError ?? currentAdaptiveLimitNotice
                        : undefined}
                      numberMin={usesAdaptiveTechniqueSelection
                        && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
                        ? 1
                        : undefined}
                      numberMax={usesAdaptiveTechniqueSelection
                        && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
                        && adaptiveCandidateMaximum !== null
                        && adaptiveCandidateMaximum > 0
                        ? adaptiveCandidateMaximum
                        : undefined}
                      numberStep={usesAdaptiveTechniqueSelection
                        && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
                        ? 1
                        : undefined}
                      numberWholeOnly={usesAdaptiveTechniqueSelection
                        && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME}
                      onRejectedNumberInput={usesAdaptiveTechniqueSelection
                        && parameter.name === MAX_ATTEMPTS_PARAMETER_NAME
                        ? rejectScenarioParamInput
                        : undefined}
                      testIdPrefix="scenario-param"
                    />
                  ))}
                </div>
              </section>
            )}

            <section className={styles.section} aria-labelledby="datasets-section-title">
              <Text id="datasets-section-title" as="h2" size={400} weight="semibold">
                Datasets
              </Text>
              <Text size={200} className={styles.hint}>
                Choose the registered datasets that provide objectives for this run.
              </Text>
              <DatasetPicker
                availableDatasets={availableDatasets}
                defaultDatasets={scenario.default_datasets}
                selectedDatasets={selectedDatasets}
                status={datasetCatalogStatus}
                error={datasetCatalogError}
                disabled={submitting}
                invalid={datasetSelectionInvalid}
                onChange={handleDatasetChange}
                onRestoreDefaults={() => {
                  setSelectedDatasets([...scenario.default_datasets])
                  setValidationError(null)
                }}
              />
            </section>

            <Accordion collapsible className={styles.advancedSection}>
              <AccordionItem value="advanced">
                <AccordionHeader>Advanced options</AccordionHeader>
                <AccordionPanel>
                  <div className={styles.advancedFields}>
                    <ParameterField
                      parameter={MAX_DATASET_SIZE_PARAMETER}
                      value={maxDatasetSize}
                      disabled={submitting || scenario.dataset_size_limit.override_scope === 'unsupported'}
                      onChange={updateMaxDatasetSize}
                      displayLabel={datasetSizeFieldLabel(scenario.dataset_size_limit)}
                      displayHint={datasetSizeHint(scenario.dataset_size_limit)}
                      validationState={maxDatasetSizeFieldError ? 'error' : 'none'}
                      validationMessage={maxDatasetSizeFieldError}
                      numberMin={1}
                      numberStep={1}
                      numberWholeOnly
                      onRejectedNumberInput={rejectMaxDatasetSizeInput}
                      testIdPrefix="advanced"
                    />
                    {scenario.dataset_size_limit.override_scope !== 'unsupported'
                      && (hasMaxDatasetSizeOverride || maxDatasetSize.trim() === '') && (
                      <Button
                        className={styles.touchTarget}
                        appearance="subtle"
                        icon={<ArrowSyncRegular />}
                        type="button"
                        disabled={submitting}
                        onClick={restoreDefaultDatasetSize}
                        data-testid="restore-default-dataset-size"
                      >
                        Restore scenario default
                      </Button>
                    )}
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
                    <Field
                      label="Max retries"
                      hint={usesAdaptiveTechniqueSelection
                        ? 'Maximum times to resume the scenario after an exception. This is separate from Adaptive trying another technique.'
                        : 'Maximum times to resume the scenario after an exception.'}
                    >
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
                </AccordionPanel>
              </AccordionItem>
            </Accordion>
          </form>

          <aside className={styles.previewRail} aria-labelledby="run-preview-title">
            <div className={styles.previewHeader}>
              <Text id="run-preview-title" as="h2" size={500} weight="semibold">Run preview</Text>
              <Text size={200} className={styles.hint}>
                Review the exact configuration used for this run.
              </Text>
            </div>
            <dl className={styles.previewList}>
              <div className={styles.previewGroup}>
                <dt>Target</dt>
                <dd>{targetName}</dd>
              </div>
              <div className={styles.previewGroup}>
                <dt>Techniques</dt>
                <dd>
                  {techniqueSelection.mode === 'preset' ? (
                    <div className={styles.previewStack}>
                      <Text weight="semibold">
                        Technique set: {techniqueSetDisplayName(scenario, techniqueSelection.preset)}
                      </Text>
                      {presetMembers.length > 0 && (
                        <Text size={200} className={styles.hint}>
                          Resolves to {presetMembers.join(', ')}
                        </Text>
                      )}
                    </div>
                  ) : customTechniques.length > 0 ? (
                    <div className={styles.previewBadges}>
                      {customTechniques.map((name) => (
                        <Badge key={name} appearance="outline">{name}</Badge>
                      ))}
                    </div>
                  ) : (
                    <Text className={styles.errorText}>No custom techniques selected</Text>
                  )}
                </dd>
              </div>
              <div className={styles.previewGroup}>
                <dt>Datasets</dt>
                <dd>
                  <div className={styles.previewStack}>
                    <Text>
                      {selectedDatasets.length > 0 ? selectedDatasets.join(', ') : 'No datasets selected'}
                    </Text>
                    <Text size={200} className={styles.hint}>
                      {datasetsAreDefaults ? 'Scenario defaults' : 'Selected datasets'}
                      {' · '}
                      {datasetSizePreview}
                    </Text>
                  </div>
                </dd>
              </div>
              <div className={styles.previewGroup}>
                <dt>Scenario parameters</dt>
                <dd>
                  {dynamicParameters.length > 0 ? (
                    <dl className={styles.parameterPreview}>
                      {dynamicParameters.map((parameter) => (
                        <div className={styles.parameterPreviewRow} key={parameter.name}>
                          <dt>{parameterDisplayLabel(parameter, usesAdaptiveTechniqueSelection)}</dt>
                          <dd>{formatParameterPreview(scenarioParamValues[parameter.name])}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    'No scenario-specific parameters'
                  )}
                </dd>
              </div>
              <div className={styles.previewGroup}>
                <dt>Baseline</dt>
                <dd>
                  {isBaselineForbidden
                    ? 'Not included — this scenario does not support direct comparison'
                    : baselineChecked
                      ? 'Included — direct objective without an attack technique'
                      : 'Not included'}
                </dd>
              </div>
            </dl>
            <div className={styles.estimateGroup}>
              <Text as="h3" size={400} weight="semibold">Planned run size</Text>
              <ScenarioRunEstimateDetails
                state={estimateState}
                idPrefix={`${formId}-estimate`}
              />
            </div>
            <div className={styles.previewActions}>
              <Button
                className={styles.launchButton}
                appearance="primary"
                type="submit"
                form={formId}
                disabled={
                  submitting
                  || techniqueSelectionInvalid
                  || datasetSelectionInvalid
                  || !requestResult.ok
                  || Boolean(maxAttemptsFieldError)
                  || Boolean(maxDatasetSizeFieldError)
                  || estimateRequestBlocked
                  || adaptiveMetadataUnavailable
                  || maxAttemptsExceedsCandidateMaximum
                }
                data-testid="launch-scenario-btn"
              >
                {submitting ? 'Launching...' : 'Launch scenario'}
              </Button>
            </div>
          </aside>
        </div>
      </div>
    </section>
  )
}
