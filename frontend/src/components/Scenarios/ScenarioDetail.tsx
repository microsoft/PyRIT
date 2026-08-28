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
  ScenarioRunEstimateResult,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunEstimateState,
  TargetInstance,
} from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'
import { routerPathParamValue } from '@/utils/routeParams'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import { ScenarioRunEstimateDetails } from './ScenarioRunEstimate'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'

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
      techniques: string[]
    }

interface TechniqueOptions {
  presets: string[]
  concrete: string[]
  defaultSelection: TechniqueSelection
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
    : { mode: 'custom', techniques: [scenario.default_technique] }
  return { presets, concrete, defaultSelection }
}

function selectedTechniqueNames(selection: TechniqueSelection): string[] {
  return selection.mode === 'preset' ? [selection.preset] : selection.techniques
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

interface BuildEstimateRequestInput {
  scenario: RegisteredScenario
  targetName: string
  techniques: string[]
  dynamicParameters: Parameter[]
  scenarioParamValues: Record<string, ParameterFormValue>
  datasetOverride: string
  maxDatasetSize: string
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

function ScenarioLaunchForm({ scenario, targets, activeTarget, labels, onNavigate }: ScenarioLaunchFormProps) {
  const styles = useScenarioDetailStyles()
  const navigate = useNavigate()
  const formId = `scenario-launch-${encodeURIComponent(scenario.scenario_name).replace(/%/g, '-')}`

  const { presets, concrete, defaultSelection } = useMemo(
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
  const [techniqueSelection, setTechniqueSelection] = useState<TechniqueSelection>(() => defaultSelection)
  const [baselineChecked, setBaselineChecked] = useState(
    () => !isBaselineForbidden && scenario.include_baseline_by_default,
  )
  const [datasetOverride, setDatasetOverride] = useState('')
  const [maxDatasetSize, setMaxDatasetSize] = useState('')
  const [maxConcurrency, setMaxConcurrency] = useState(DEFAULT_MAX_CONCURRENCY)
  const [maxRetries, setMaxRetries] = useState(DEFAULT_MAX_RETRIES)
  const [scenarioParamValues, setScenarioParamValues] = useState<Record<string, ParameterFormValue>>(() =>
    getInitialFormValues(dynamicParameters),
  )
  const [validationError, setValidationError] = useState<string | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [estimateRequestState, setEstimateRequestState] = useState<EstimateRequestState | null>(null)
  const [lastGoodEstimate, setLastGoodEstimate] = useState<SuccessfulEstimateResult | null>(null)
  // Synchronous guard against a double-submit racing ahead of the state update.
  const isSubmittingRef = useRef(false)
  const estimateSequenceRef = useRef(0)

  const techniques = useMemo(
    () => selectedTechniqueNames(techniqueSelection),
    [techniqueSelection],
  )
  const estimateResult = useMemo(
    () => buildEstimateRequest({
      scenario,
      targetName,
      techniques,
      dynamicParameters,
      scenarioParamValues,
      datasetOverride,
      maxDatasetSize,
      includeBaseline: isBaselineForbidden ? false : baselineChecked,
    }),
    [
      baselineChecked,
      datasetOverride,
      dynamicParameters,
      isBaselineForbidden,
      maxDatasetSize,
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
      maxDatasetSize,
      maxConcurrency,
      maxRetries,
      includeBaseline: isBaselineForbidden ? false : baselineChecked,
      labels,
    }),
    [
      baselineChecked,
      datasetOverride,
      dynamicParameters,
      isBaselineForbidden,
      labels,
      maxConcurrency,
      maxDatasetSize,
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

  const handlePresetChange = (preset: string): void => {
    setTechniqueSelection({ mode: 'preset', preset })
    setValidationError(null)
  }

  const handleConcreteChange = (name: string, checked: boolean): void => {
    setTechniqueSelection((current) => {
      if (checked) {
        if (current.mode === 'preset') {
          return { mode: 'custom', techniques: [name] }
        }
        return current.techniques.includes(name)
          ? current
          : { mode: 'custom', techniques: [...current.techniques, name] }
      }
      if (current.mode === 'preset') {
        return current
      }
      return {
        mode: 'custom',
        techniques: current.techniques.filter((technique) => technique !== name),
      }
    })
    setValidationError(null)
  }

  const updateScenarioParam = (name: string, value: ParameterFormValue): void => {
    setScenarioParamValues((current) => ({ ...current, [name]: value }))
  }

  const handleSubmit = async (): Promise<void> => {
    if (isSubmittingRef.current) {
      return
    }

    setApiError(null)
    if (!requestResult.ok) {
      setValidationError(requestResult.error)
      return
    }

    isSubmittingRef.current = true
    setSubmitting(true)
    setValidationError(null)

    try {
      const summary = await scenariosApi.startRun(requestResult.request)
      navigate(`/scenario-history/${encodeURIComponent(summary.scenario_result_id)}`, {
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
    void handleSubmit()
  }

  const techniqueSelectionInvalid =
    techniqueSelection.mode === 'custom' && techniqueSelection.techniques.length === 0
  const previewDatasets = parseDatasetNames(datasetOverride)
  const effectiveDatasets = previewDatasets.length > 0 ? previewDatasets : scenario.default_datasets
  const presetMembers = techniqueSelection.mode === 'preset'
    ? (
        scenario.aggregate_technique_expansions[techniqueSelection.preset]
        ?? (techniqueSelection.preset === scenario.default_technique
          ? scenario.default_techniques
          : [])
      )
    : []

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
                      {target.target_registry_name}
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
                  onClick={() => onNavigate('config')}
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
                Selecting a preset replaces any custom list. Selecting the first individual technique
                switches to a custom list and clears the preset.
              </Text>
              <div className={styles.techniqueGroups}>
                {presets.length > 0 ? (
                  <Field label="Aggregate preset">
                    <RadioGroup
                      value={techniqueSelection.mode === 'preset' ? techniqueSelection.preset : ''}
                      onChange={(_, data) => handlePresetChange(data.value)}
                      aria-label="Aggregate preset"
                    >
                      {presets.map((name) => (
                        <Radio
                          className={styles.selectionControl}
                          key={name}
                          label={name === scenario.default_technique ? `${name} (default)` : name}
                          value={name}
                          disabled={submitting}
                          data-testid={`technique-${name}`}
                        />
                      ))}
                    </RadioGroup>
                  </Field>
                ) : (
                  <Text size={200} className={styles.hint}>
                    No aggregate presets are registered for this scenario.
                  </Text>
                )}
                {techniqueSelection.mode === 'preset' && (
                  <div className={styles.resolvedMembers} aria-live="polite">
                    <Text size={200} weight="semibold">Backend-resolved preset members</Text>
                    {presetMembers.length > 0 ? (
                      <div className={styles.previewBadges}>
                        {presetMembers.map((name) => (
                          <Badge key={name} appearance="outline">{name}</Badge>
                        ))}
                      </div>
                    ) : (
                      <Text size={200} className={styles.hint}>
                        No concrete members were supplied for this preset.
                      </Text>
                    )}
                  </div>
                )}
                <Field
                  label="Individual techniques"
                  validationState={techniqueSelectionInvalid ? 'error' : 'none'}
                  validationMessage={techniqueSelectionInvalid
                    ? 'Select at least one technique.'
                    : undefined}
                >
                  {concrete.length > 0 ? (
                    <div className={styles.checkboxGroup} role="group" aria-label="Individual techniques">
                      {concrete.map((name) => (
                        <Checkbox
                          className={styles.selectionControl}
                          key={name}
                          label={name}
                          checked={
                            techniqueSelection.mode === 'custom'
                            && techniqueSelection.techniques.includes(name)
                          }
                          disabled={submitting}
                          onChange={(_, data) => handleConcreteChange(name, data.checked === true)}
                          data-testid={`technique-${name}`}
                        />
                      ))}
                    </div>
                  ) : (
                    <Text size={200} className={styles.hint}>
                      No concrete techniques are registered for custom selection.
                    </Text>
                  )}
                </Field>
              </div>
            </section>

            <section className={styles.section} aria-labelledby="baseline-section-title">
              <Text id="baseline-section-title" as="h2" size={400} weight="semibold">
                Baseline
              </Text>
              <Field>
                <Checkbox
                  className={styles.selectionControl}
                  checked={baselineChecked}
                  disabled={submitting || isBaselineForbidden}
                  label={isBaselineForbidden ? 'Not available for this scenario' : 'Include baseline attack'}
                  onChange={(_, data) => setBaselineChecked(data.checked === true)}
                  data-testid="baseline-checkbox"
                />
              </Field>
              {isBaselineForbidden && (
                <Text size={200} className={styles.hint}>
                  This scenario forbids a baseline comparison run.
                </Text>
              )}
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
                      disabled={submitting}
                      onChange={updateScenarioParam}
                      testIdPrefix="scenario-param"
                    />
                  ))}
                </div>
              </section>
            )}

            <Accordion collapsible className={styles.advancedSection}>
              <AccordionItem value="advanced">
                <AccordionHeader>Advanced options</AccordionHeader>
                <AccordionPanel>
                  <div className={styles.advancedFields}>
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
                    <Field label="Max dataset size" hint="Optional. Leave blank for no cap.">
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
                </AccordionPanel>
              </AccordionItem>
            </Accordion>
          </form>

          <aside className={styles.previewRail} aria-labelledby="run-preview-title">
            <div className={styles.previewHeader}>
              <Text id="run-preview-title" as="h2" size={500} weight="semibold">Run preview</Text>
              <Text size={200} className={styles.hint}>
                Review the exact configuration sent to the backend.
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
                      <Text weight="semibold">Preset: {techniqueSelection.preset}</Text>
                      {presetMembers.length > 0 && (
                        <Text size={200} className={styles.hint}>
                          Resolves to {presetMembers.join(', ')}
                        </Text>
                      )}
                    </div>
                  ) : techniqueSelection.techniques.length > 0 ? (
                    <div className={styles.previewBadges}>
                      {techniqueSelection.techniques.map((name) => (
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
                      {effectiveDatasets.length > 0 ? effectiveDatasets.join(', ') : 'No datasets declared'}
                    </Text>
                    <Text size={200} className={styles.hint}>
                      {previewDatasets.length > 0 ? 'Custom override' : 'Scenario defaults'}
                      {maxDatasetSize.trim() ? ` · capped at ${maxDatasetSize.trim()} each` : ''}
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
                          <dt>{parameter.name}</dt>
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
                    ? 'Excluded by scenario policy'
                    : baselineChecked
                      ? 'Included'
                      : 'Not included'}
                </dd>
              </div>
            </dl>
            <div className={styles.estimateGroup}>
              <Text as="h3" size={400} weight="semibold">Backend-owned size</Text>
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
                disabled={submitting || techniqueSelectionInvalid}
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
