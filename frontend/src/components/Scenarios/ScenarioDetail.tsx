import { type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  MessageBar,
  MessageBarBody,
  Select,
  Spinner,
  Switch,
  Text,
  Tooltip,
} from '@fluentui/react-components'
import {
  ArrowLeftRegular,
  ArrowSyncRegular,
  SettingsRegular,
} from '@fluentui/react-icons'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import ContextualHelpLabel from '@/components/ContextualHelpLabel'
import HelpField from '@/components/HelpField'
import { HELP_DEFINITIONS } from '@/components/helpDefinitions'
import ConversationEditor, { type ConversationEditorHandle } from '@/components/Chat/ConversationEditor'
import ChatScorerPicker from '@/components/Chat/ChatScorerPicker'
import TargetSelect from '@/components/Config/TargetSelect'
import TechniqueEditor from '@/components/Techniques/TechniqueEditor'
import { useConversationDraft } from '@/hooks/useConversationDraft'
import { useRuntime } from '@/hooks/useRuntime'
import { useUserPreferences } from '@/hooks/useUserPreferences'
import { useRunTechniqueEditor } from '@/hooks/useRunTechniqueEditor'
import { useScenarioDatasets } from '@/hooks/useScenarioDatasets'
import { useScenarioDatasetPreparation } from '@/hooks/useScenarioDatasetPreparation'
import { useScenarioDetail } from '@/hooks/useScenarioDetail'
import { useScenarioPreset } from '@/hooks/useScenarioPreset'
import { useScenarioLaunch } from '@/hooks/useScenarioLaunch'
import { useScenarioRunEstimate } from '@/hooks/useScenarioRunEstimate'
import ParameterField from '@/components/Parameters/ParameterField'
import SingleStepSpinButton from '@/components/Parameters/SingleStepSpinButton'
import type { ParameterFormValue } from '@/components/Parameters/parameterForm'
import { PRESET_QUERY_PARAM, presetScannerRoutePath } from '@/components/ScenarioPresets/presetRoutes'
import type { ViewName } from '@/components/Sidebar/Navigation'
import { attacksApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  AddMessageResponse,
  Parameter,
  RegisteredComponentReference,
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioDatasetOptions,
  ScenarioDatasetRequest,
  ScenarioDatasetSettings,
  ScenarioPresetResponse,
  ScenarioRunSummary,
  ScorerInstance,
  TargetInstance,
} from '@/types'
import { findRegisteredScorer } from '@/utils/scorerPresentation'
import { newDraftMessage, toConversationDraft } from '@/utils/conversationDraft'
import {
  DATASET_QUERY_PARAM, datasetNamesFromSearchParams, isBlankDatasetName, routerPathParamValue, scenarioRunRoutePath,
} from '@/utils/routeParams'
import { targetModelName } from '@/utils/targetIdentity'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import ScenarioEstimateSection from './ScenarioEstimateSection'
import ScenarioRunPreviewDialog from './ScenarioRunPreviewDialog'
import ScenarioTechniqueSection from './ScenarioTechniqueSection'
import { datasetTotalLimit, scenarioDatasetDefaults, scenarioDatasetOptions } from './datasetPresentation'
import { formatCarriedParameterPreview } from './scenarioLaunchPresentation'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { carriedPresetScenarioParams, seedPresetTechniques, untouchedPresetScenarioParams } from './scenarioPresetSeed'
import {
  BASELINE_TECHNIQUE,
  exceedsTechniqueLimit,
  initialScenarioLaunchForm,
  reduceScenarioLaunchForm,
  type ScenarioLaunchFormAction,
  type ScenarioLaunchFormRules,
  type ScenarioLaunchFormState,
  type SelectableTechnique,
  uniqueTechniqueOptions,
} from './scenarioLaunchForm'
import {
  buildEstimateRequest,
  buildRunRequest,
  CONVERSATION_PARAMETER_NAMES,
  MAX_MAX_CONCURRENCY,
  MAX_MAX_RETRIES,
  MIN_MAX_CONCURRENCY,
  MIN_MAX_RETRIES,
  OBJECTIVE_PARAMETER_NAMES,
  isScorerOwnedParameter,
  type ScenarioLaunchInput,
  scenarioDynamicParameters,
  linkedDatasetProblem,
  supportsParameter,
  usesDefaultAssertScoring,
  usesScenarioDatasets,
} from './scenarioLaunchRequest'
import ScenarioDatasetTable from './ScenarioDatasetTable'
import DatasetActions from './DatasetActions'

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

interface ScorerPickerData {
  scorers?: ScorerInstance[]
  scorersLoading?: boolean
  scorersError?: string | null
}

interface ScenarioDetailProps extends ScorerPickerData {
  /** A saved run to start from, or undefined for a fresh form. */
  initialRun?: RunScenarioRequest
  /** The scan the saved run came from, for the back link. */
  sourceRunId?: string
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
  const initialConversationId = conversationIds.length === 1 ? (conversationIds[0]?.trim() ?? '') : ''
  const presetName = searchParams.get(PRESET_QUERY_PARAM)?.trim() ?? ''
  const datasetNames = datasetNamesFromSearchParams(searchParams)
  if (presetName && props.sourceRunId) return <ScenarioDetailStateFrame>
    <MessageBar intent="error"><MessageBarBody>Choose a saved run or a preset, not both.</MessageBarBody></MessageBar>
  </ScenarioDetailStateFrame>
  // A new scenario, conversation, preset, dataset, or saved-run link starts a fresh configuration form.
  return <ScenarioDetailContent
    key={JSON.stringify([encodedScenarioName, initialConversationId, presetName, props.sourceRunId, datasetNames])}
    encodedScenarioName={encodedScenarioName} initialConversationId={initialConversationId}
    presetName={presetName} datasetNames={datasetNames} {...props} />
}

interface ScenarioDetailContentProps extends ScenarioDetailProps {
  encodedScenarioName: string | undefined
  initialConversationId: string
  /** The saved preset that pre-fills the form, or empty for none. */
  presetName: string
  /** Explicit dataset names from a link; the server validates them when estimating and launching. */
  datasetNames: string[]
}

interface ScenarioDetailStateFrameProps {
  readonly children: ReactNode
}

function ScenarioDetailStateFrame({ children }: ScenarioDetailStateFrameProps) {
  const styles = useScenarioDetailStyles()
  return (
    <section className={styles.root} data-testid="scenario-detail" aria-label="Scenario detail">
      <div className={styles.content}>
        <Link to="/scanner/launch" className={styles.backLink}>
          <ArrowLeftRegular /> Back to scanners
        </Link>
        {children}
      </div>
    </section>
  )
}

function ScenarioDetailContent({
  initialRun,
  sourceRunId,
  encodedScenarioName,
  initialConversationId,
  presetName,
  datasetNames,
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  defaultsReady = false,
  labels,
  operationId = null,
  onNavigate,
  scorers = [],
  scorersLoading = false,
  scorersError = null,
}: ScenarioDetailContentProps) {
  const styles = useScenarioDetailStyles()
  const decodedScenarioName = routerPathParamValue(encodedScenarioName)
  const { status: scenarioStatus, scenario, error: scenarioError, retry: retryScenario } = useScenarioDetail(decodedScenarioName)
  const { status: presetStatus, preset, error: presetError, retry: retryPreset } = useScenarioPreset(presetName)

  const handleRetry = (): void => {
    retryScenario()
    retryPreset()
  }

  if (scenarioStatus === 'loading' || presetStatus === 'loading') {
    return (
      <section className={styles.root} data-testid="scenario-detail" aria-label="Scenario detail">
        <div className={styles.centeredState}>
          <Spinner label="Loading scenario..." />
        </div>
      </section>
    )
  }

  const retryButton = (
    <Button
      className={styles.touchTarget}
      appearance="primary"
      icon={<ArrowSyncRegular />}
      onClick={handleRetry}
      data-testid="retry-btn"
    >
      Retry
    </Button>
  )

  if (scenarioStatus === 'not-found') {
    return (
      <ScenarioDetailStateFrame>
        <div className={styles.centeredState} data-testid="scenario-not-found">
          <Text size={400}>Scenario &quot;{decodedScenarioName}&quot; was not found</Text>
          <Text size={200}>It may have been renamed or is no longer registered.</Text>
        </div>
      </ScenarioDetailStateFrame>
    )
  }

  if (scenarioStatus === 'error') {
    return (
      <ScenarioDetailStateFrame>
        <div className={styles.centeredState} data-testid="scenario-error">
          <MessageBar intent="error">
            <MessageBarBody>{scenarioError}</MessageBarBody>
          </MessageBar>
          {retryButton}
        </div>
      </ScenarioDetailStateFrame>
    )
  }

  if (presetStatus === 'not-found') {
    return (
      <ScenarioDetailStateFrame>
        <div className={styles.centeredState} data-testid="preset-not-found">
          <Text size={400}>Preset &quot;{presetName}&quot; was not found</Text>
          <Text size={200}>It may have been renamed or deleted.</Text>
          <Link to={`/scanner/launch/${encodeURIComponent(decodedScenarioName)}`}>
            Open {decodedScenarioName} without a preset
          </Link>
        </div>
      </ScenarioDetailStateFrame>
    )
  }

  if (presetStatus === 'error') {
    return (
      <ScenarioDetailStateFrame>
        <div className={styles.centeredState} data-testid="preset-error">
          <MessageBar intent="error">
            <MessageBarBody>Preset &quot;{presetName}&quot; could not be loaded: {presetError}</MessageBarBody>
          </MessageBar>
          {retryButton}
        </div>
      </ScenarioDetailStateFrame>
    )
  }

  // Both loads succeeded from here on; each value is set together with its status.
  if (!scenario) {
    return null
  }

  if (preset && preset.preset.scenario_name !== scenario.scenario_name) {
    const { name, scenario_name: presetScenarioName } = preset.preset
    return (
      <ScenarioDetailStateFrame>
        <div className={styles.centeredState} data-testid="preset-scenario-mismatch">
          <MessageBar intent="error">
            <MessageBarBody>
              Preset &quot;{name}&quot; configures {presetScenarioName}, not {scenario.scenario_name}.
            </MessageBarBody>
          </MessageBar>
          <Link to={presetScannerRoutePath(presetScenarioName, name)}>
            Open {presetScenarioName} with this preset
          </Link>
        </div>
      </ScenarioDetailStateFrame>
    )
  }

  if (initialRun && scenario.scenario_name !== initialRun.scenario_name) return <ScenarioDetailStateFrame>
    <MessageBar intent="error"><MessageBarBody>The saved configuration belongs to a different scenario.</MessageBarBody></MessageBar>
  </ScenarioDetailStateFrame>
  return (
    <ScenarioLaunchForm
      key={scenario.scenario_name}
      scenario={scenario}
      preset={preset}
      initialRun={initialRun}
      sourceRunId={sourceRunId}
      initialConversationId={initialConversationId}
      linkedDatasetNames={datasetNames}
      targets={targets}
      defaultObjectiveTarget={defaultObjectiveTarget}
      defaultAdversarialTarget={defaultAdversarialTarget}
      labels={labels}
      operationId={operationId}
      defaultsReady={defaultsReady}
      onNavigate={onNavigate}
      scorers={scorers}
      scorersLoading={scorersLoading}
      scorersError={scorersError}
    />
  )
}

interface ScenarioLaunchFormProps extends ScorerPickerData {
  initialRun?: RunScenarioRequest
  sourceRunId?: string
  scenario: RegisteredScenario
  /** Seeds the initial form state only; edits are never written back to the preset. */
  preset?: ScenarioPresetResponse | null
  initialConversationId: string
  /** Explicit dataset names from a link; when the scenario takes datasets they replace the preset's sources. */
  linkedDatasetNames?: readonly string[]
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
  initialRun,
  sourceRunId,
  scenario,
  preset = null,
  initialConversationId,
  linkedDatasetNames = [],
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  labels,
  operationId,
  defaultsReady = false,
  onNavigate,
  scorers = [],
  scorersLoading = false,
  scorersError = null,
}: ScenarioLaunchFormProps) {
  const runtime = useRuntime()
  const { rememberScanTargets } = useUserPreferences()
  const [selectionGeneration, setSelectionGeneration] = useState(runtime.generation)
  const staleSelection = selectionGeneration !== runtime.generation
  const [objectiveScorer, setObjectiveScorer] = useState<RegisteredComponentReference | null>(initialRun?.objective_scorer ?? null)
  const scoringTargetName = initialRun?.scoring_target_name ?? ''
  const scorerError = objectiveScorer && (
    scorersLoading || scorersError || !findRegisteredScorer(objectiveScorer, scorers)
  ) ? 'The selected objective scorer is unavailable. Select a scorer or use the scenario default.' : undefined
  const styles = useScenarioDetailStyles()
  const navigate = useNavigate()
  const formId = `scenario-launch-${encodeURIComponent(scenario.scenario_name).replace(/%/g, '-')}`

  const { techniques: techniqueOptions, defaultTechniques } = useMemo(
    () => uniqueTechniqueOptions(scenario),
    [scenario],
  )
  const catalogParameters = useMemo(() => scenarioDynamicParameters(scenario), [scenario])
  const savedScenarioParams = initialRun?.scenario_params ?? preset?.preset.scenario_params
  const [carriedScenarioParams] = useState(
    () => carriedPresetScenarioParams(savedScenarioParams, catalogParameters),
  )
  // Initially carried values stay read-only until remount, even if refreshed metadata adds a field.
  const dynamicParameters = useMemo(
    () => catalogParameters.filter(
      (parameter) => !Object.prototype.hasOwnProperty.call(carriedScenarioParams, parameter.name),
    ),
    [catalogParameters, carriedScenarioParams],
  )
  const [techniqueSeed] = useState(() => seedPresetTechniques(
    scenario,
    initialRun?.techniques ?? preset?.preset.techniques,
    new Set(techniqueOptions.map((technique) => technique.name)),
    scenario.scenario_name === 'technique.execute' ? [] : defaultTechniques,
  ))
  // Preset tokens with no checkbox are sent verbatim for the server to resolve, so they
  // must not read as a selection a runtime refresh made unavailable.
  const presetTokens = useMemo(() => new Set(techniqueSeed.tokensWithoutCheckbox), [techniqueSeed])
  const conversationConvertersParameter = dynamicParameters.some((parameter) => parameter.name === 'prepended_conversation_id')
    ? dynamicParameters.find((parameter) => parameter.name === 'apply_converters_to_prepended_history')
    : undefined
  const supportsObjectiveTarget = supportsParameter(scenario, 'objective_target')
  const supportsTechniques = supportsParameter(scenario, 'scenario_techniques')
  const supportsBaseline = supportsParameter(scenario, 'include_baseline')
  const supportsDatasets = supportsParameter(scenario, 'dataset_config')
  const defaultAssertScoring = usesDefaultAssertScoring(scenario)
  const supportsConcurrency = supportsParameter(scenario, 'max_concurrency')
  const supportsRetries = supportsParameter(scenario, 'max_retries')
  const isBaselineForbidden = scenario.baseline_policy === 'forbidden'
  const singleTechnique = scenario.max_concrete_techniques === 1
  const adversarialTargets = targets.filter(
    (target: TargetInstance) => target.capabilities?.supports_multi_turn === true,
  )
  const [linkedNames] = useState(() => [...linkedDatasetNames])
  const linkedDatasetError = useMemo(
    () => linkedDatasetProblem(linkedNames, supportsDatasets),
    [linkedNames, supportsDatasets],
  )
  const location = useLocation()
  // Without dataset input every linked name goes; otherwise only the blank ones do.
  const searchWithoutBadLinks = new URLSearchParams()
  for (const [key, value] of new URLSearchParams(location.search)) {
    if (key !== DATASET_QUERY_PARAM || (supportsDatasets && !isBlankDatasetName(value))) {
      searchWithoutBadLinks.append(key, value)
    }
  }
  const remainingSearch = searchWithoutBadLinks.toString()
  const pathWithoutBadLinks = `${location.pathname}${remainingSearch ? `?${remainingSearch}` : ''}`

  const formRules = useMemo<ScenarioLaunchFormRules>(() => ({
    techniqueNames: techniqueOptions.map((technique) => technique.name),
    maxConcreteTechniques: scenario.max_concrete_techniques ?? null,
    singleTechnique,
    presetTokens,
  }), [techniqueOptions, scenario.max_concrete_techniques, singleTechnique, presetTokens])
  const [form, dispatch] = useReducer(
    (state: ScenarioLaunchFormState, action: ScenarioLaunchFormAction) =>
      reduceScenarioLaunchForm(state, action, formRules),
    undefined,
    () => initialScenarioLaunchForm({
      scenario,
      targets,
      defaultObjectiveTarget,
      adversarialTargets,
      defaultAdversarialTarget,
      defaultTechniques,
      dynamicParameters,
      initialConversationId,
      preset,
      seededTechniques: techniqueSeed.techniques,
      initialRun,
      datasetNames: supportsDatasets && linkedDatasetNames.length > 0
        ? linkedDatasetNames.filter((name) => !isBlankDatasetName(name)) : undefined,
    }),
  )
  const {
    targetName,
    adversarialTargetName,
    selectedTechniques,
    baselineChecked,
    datasetEdits,
    datasetError,
    maxConcurrency,
    maxRetries,
    scenarioParamValues,
  } = form
  const setDatasetEdits = useCallback(
    (edits: ScenarioDatasetRequest) => dispatch({ type: 'setDatasetEdits', edits }),
    [],
  )
  const setDatasetError = useCallback(
    (error: string | null) => dispatch({ type: 'setDatasetError', error }),
    [],
  )
  const updateScenarioParam = (name: string, value: ParameterFormValue): void => {
    dispatch({ type: 'setParam', name, value })
  }

  const singleSelectedTechnique = singleTechnique && supportsTechniques && selectedTechniques.length === 1
    ? selectedTechniques[0]
    : undefined
  // A verbatim preset token names no registered technique to load settings from, so the
  // server alone decides what it supports.
  const opaqueSelectedTechnique = singleSelectedTechnique !== undefined && presetTokens.has(singleSelectedTechnique)
    && !techniqueOptions.some((technique) => technique.name === singleSelectedTechnique)
  const techniqueEditor = useRunTechniqueEditor(
    opaqueSelectedTechnique ? undefined : singleSelectedTechnique,
    runtime.generation, runtime.ready,
    initialRun?.technique_override && initialRun.technique_override.source_name === singleSelectedTechnique
      ? initialRun.technique_override : undefined,
  )
  const [pendingTechnique, setPendingTechnique] = useState<string | null>(null)
  const techniqueEditError = techniqueEditor.unresolved
    ? techniqueEditor.error ?? 'Loading selected technique settings.'
    : techniqueEditor.pending ? 'Wait for valid technique settings before launch.' : undefined
  const supportsPrependedConversation = !singleTechnique || opaqueSelectedTechnique
    || techniqueEditor.configuration?.supports_prepended_conversation === true
  const supportsPrompt = !singleTechnique || opaqueSelectedTechnique
    || techniqueEditor.configuration?.supports_prompt === true
  const objectiveParameters = dynamicParameters.filter((parameter) => OBJECTIVE_PARAMETER_NAMES.has(parameter.name))
  const historyParameters = dynamicParameters.filter((parameter) => CONVERSATION_PARAMETER_NAMES.has(parameter.name)
    && parameter.name !== conversationConvertersParameter?.name)
  const generalParameters = dynamicParameters.filter((parameter) =>
    !OBJECTIVE_PARAMETER_NAMES.has(parameter.name) && !CONVERSATION_PARAMETER_NAMES.has(parameter.name)
    && (parameter.name !== 'prompt' || supportsPrompt))
  const unavailableSelection = (
    supportsObjectiveTarget && targetName !== ''
    && !targets.some((target) => target.target_registry_name === targetName)
  ) || (supportsTechniques
    && selectedTechniques.some((name) => !presetTokens.has(name)
      && !techniqueOptions.some((technique) => technique.name === name)))
  const carriedScenarioParamNames = Object.keys(carriedScenarioParams).filter((name) => !isScorerOwnedParameter(scenario, name))
  const presetScenarioParams = useMemo(
    () => ({
      ...carriedScenarioParams,
      ...untouchedPresetScenarioParams(savedScenarioParams, dynamicParameters, scenarioParamValues),
    }),
    [carriedScenarioParams, dynamicParameters, savedScenarioParams, scenarioParamValues],
  )
  const techniquesWithoutCheckbox = selectedTechniques.filter((name) => presetTokens.has(name)
    && !techniqueOptions.some((technique) => technique.name === name))

  // The one effective configuration both the estimate and the launch read.
  const launchInput = useMemo<ScenarioLaunchInput>(() => ({
    scenario,
    targetName,
    adversarialTargetName,
    scoringTargetName,
    techniques: selectedTechniques,
    techniqueOverride: techniqueEditor.override,
    techniqueEditError,
    supportsPrependedConversation,
    supportsPrompt,
    objectiveScorer,
    scorerError,
    dynamicParameters,
    scenarioParamValues,
    presetScenarioParams,
    datasetEdits,
    datasetError,
    linkedDatasetError,
    maxConcurrency,
    maxRetries,
    includeBaseline: isBaselineForbidden ? false : baselineChecked,
    labels,
    operationId,
  }), [
    adversarialTargetName,
    baselineChecked,
    datasetEdits,
    datasetError,
    dynamicParameters,
    isBaselineForbidden,
    labels,
    maxConcurrency,
    maxRetries,
    objectiveScorer,
    operationId,
    presetScenarioParams,
    scenario,
    scenarioParamValues,
    scorerError,
    scoringTargetName,
    selectedTechniques,
    supportsPrependedConversation,
    supportsPrompt,
    targetName,
    techniqueEditError,
    techniqueEditor.override,
    linkedDatasetError,
  ])
  const estimateResult = useMemo(
    () => buildEstimateRequest({ ...launchInput, normalizeDatasetLoading: false }), [launchInput],
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
  const datasetMetadata = useScenarioDatasets(scenario.scenario_name,
    supportsDatasets || scenarioDatasetDefaults(scenario).sources.length > 0)
  const datasetContextKey = useMemo(() => estimateRequest === null ? null : JSON.stringify([
    scenario.scenario_name, runtime.generation,
    Object.fromEntries(Object.entries(estimateRequest).filter(([key]) =>
      !['datasets', 'dataset_names', 'max_dataset_size', 'dataset_filters'].includes(key))),
  ]), [estimateRequest, scenario.scenario_name, runtime.generation])
  const runEstimate = useScenarioRunEstimate({
    scenarioName: scenario.scenario_name,
    request: estimateRequest,
    requestKey: estimateRequestKey,
    datasetContextKey,
    invalidReason: estimateResult.ok ? null : estimateResult.error,
    ready: runtime.ready,
    paused: staleSelection,
  })
  const estimateState = runEstimate.state

  const datasetProjection = runEstimate.datasetProjection
  const datasetDefaults: ScenarioDatasetSettings = datasetProjection?.dataset_defaults
    ?? scenarioDatasetDefaults(scenario)
  const effectiveDatasets = datasetProjection?.effective_datasets ?? datasetDefaults
  const datasetOptions: ScenarioDatasetOptions = datasetProjection?.dataset_options
    ?? scenarioDatasetOptions(scenario)
  const usesDatasets = usesScenarioDatasets(scenario, datasetDefaults)
  const datasetConfigurationReady = !usesDatasets || (datasetContextKey !== null
    && runEstimate.datasetProjectionContextKey === datasetContextKey)
  const resolvedLaunchInput = useMemo(() => ({
    ...launchInput, datasetDefaults, datasetOptions,
  }), [launchInput, datasetDefaults, datasetOptions])
  const preparationRequestResult = useMemo(
    () => buildEstimateRequest(resolvedLaunchInput), [resolvedLaunchInput],
  )
  const requestResult = useMemo(() => buildRunRequest(datasetConfigurationReady
    ? resolvedLaunchInput : { ...launchInput, normalizeDatasetLoading: false }),
  [datasetConfigurationReady, resolvedLaunchInput, launchInput])
  const datasetConfigurationError = !datasetConfigurationReady && runEstimate.current?.status === 'error'
    ? runEstimate.current.error : null
  const selectedDatasetNames = usesDatasets
    ? (datasetEdits.sources ?? datasetDefaults.sources).map((source) => source.name) : []
  const datasetSelectionKey = JSON.stringify(selectedDatasetNames)
  const reloadDatasetMetadata = datasetMetadata.reload
  const previousDatasetSelection = useRef(datasetSelectionKey)
  useEffect(() => {
    if (!defaultAssertScoring && previousDatasetSelection.current !== datasetSelectionKey) {
      previousDatasetSelection.current = datasetSelectionKey
      reloadDatasetMetadata()
    }
  }, [datasetSelectionKey, reloadDatasetMetadata, defaultAssertScoring])
  const preparation = useScenarioDatasetPreparation({
    scenarioName: scenario.scenario_name,
    datasetNames: selectedDatasetNames,
    request: usesDatasets && datasetConfigurationReady && preparationRequestResult.ok ? preparationRequestResult.request : null,
    metadata: datasetMetadata.items,
    metadataLoading: datasetMetadata.loading,
    metadataError: datasetMetadata.error,
    metadataRevision: datasetMetadata.revision,
    enabled: usesDatasets && !staleSelection && datasetConfigurationReady,
    onPrepared: datasetMetadata.reload,
  })

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

  const {
    previewOpen,
    submitting,
    apiError,
    validationError,
    clearValidationError,
    isSubmitting,
    launchButtonRef,
    submitForm,
    dismissPreview,
    changePreviewOpen,
    confirmLaunch,
  } = useScenarioLaunch({
    requestResult,
    submitBlocked: conversationBusy || Boolean(techniqueEditError),
    launchBlocked: conversationBusy || Boolean(techniqueEditError) || !runtime.ready || !defaultsReady
      || staleSelection || unavailableSelection || Boolean(preparation.error) || !datasetConfigurationReady,
    onLaunched: (summary: ScenarioRunSummary) => {
      // The launched request is the one this render built, so it is still current here.
      if (requestResult.ok) rememberScanTargets(targets, requestResult.request)
      if (!defaultAssertScoring) datasetMetadata.reload()
      navigate(scenarioRunRoutePath(summary.scenario_result_id), {
        state: { scenarioName: scenario.scenario_name },
      })
    },
  })

  const beginConversation = async (parameterName: string, edit: boolean): Promise<void> => {
    if (conversationBusy || previewOpen || isSubmitting()) return
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
  const datasetSelectionLimit = datasetTotalLimit(datasetEdits, datasetDefaults)
  const datasetSizeLabel = !datasetOptions.total_limit ? 'Not applicable'
    : datasetSelectionLimit === 'all' ? 'No total limit' : String(datasetSelectionLimit)

  const handleTechniqueChange = (technique: SelectableTechnique, checked: boolean): void => {
    if (technique.isBaseline) {
      dispatch({ type: 'setBaseline', checked })
    } else {
      if (singleTechnique && checked && technique.name !== selectedTechniques[0] && techniqueEditor.hasChanges) {
        setPendingTechnique(technique.name)
        return
      }
      if (singleTechnique && technique.name !== selectedTechniques[0]) techniqueEditor.discard()
      dispatch({ type: 'setTechnique', name: technique.name, checked })
    }
    clearValidationError()
  }

  const isTechniqueSelected = (technique: SelectableTechnique): boolean => (
    technique.isBaseline ? baselineChecked : selectedTechniques.includes(technique.name)
  )

  const handleTagChange = (tag: string): void => {
    const members = selectableTechniques.filter(
      (technique) => !technique.disabled && technique.tags.includes(tag),
    )
    const select = members.some((technique) => !isTechniqueSelected(technique))
    const memberNames = members.filter((technique) => !technique.isBaseline).map((technique) => technique.name)
    if (select && exceedsTechniqueLimit(selectedTechniques, memberNames, scenario.max_concrete_techniques ?? null)) {
      return
    }
    dispatch({
      type: 'toggleTag',
      memberNames,
      includesBaseline: members.some((technique) => technique.isBaseline),
      select,
    })
    clearValidationError()
  }

  const techniqueSelectionOverLimit = supportsTechniques && scenario.max_concrete_techniques != null
    && selectedTechniques.length > scenario.max_concrete_techniques
  const techniqueSelectionError = supportsTechniques && selectedTechniques.length === 0
    ? 'Select at least one attack technique.'
    : techniqueSelectionOverLimit
    ? `Select at most ${scenario.max_concrete_techniques} concrete technique(s).`
    : null
  const selectedTechniqueCount = (supportsTechniques ? selectedTechniques.length : 0)
    + (supportsBaseline && baselineChecked && !isBaselineForbidden ? 1 : 0)
  const previewDatasets = (datasetEdits.sources ?? datasetDefaults.sources).map((source) => source.name)
  const previewFilters = { ...datasetDefaults.filters, ...datasetEdits.filters }

  const renderScenarioParameter = (parameter: Parameter): React.ReactElement => (
    <div key={parameter.name} className={styles.dynamicParameters}>
      <ParameterField
        parameter={parameter}
        value={scenarioParamValues[parameter.name] ?? ''}
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
  )

  return (
    <section
      className={styles.root}
      data-testid="scenario-detail"
      aria-labelledby="scenario-detail-title"
    >
      <div className={styles.content}>
        <Link to={sourceRunId ? scenarioRunRoutePath(sourceRunId) : '/scanner/launch'} className={styles.backLink}>
          <ArrowLeftRegular /> {sourceRunId ? 'Back to scan' : 'Back to scanners'}
        </Link>

        <div className={styles.headerText}>
          <Text id="scenario-detail-title" as="h2" size={600} weight="semibold">
            {scenario.scenario_name}
          </Text>
        </div>

        <div className={styles.layout}>
          <form
            id={formId}
            className={styles.formColumn}
            aria-label="Scenario run configuration"
            onSubmit={submitForm}
            noValidate
          >
            {preset && (
              <MessageBar intent="info" data-testid="preset-seed-banner">
                <MessageBarBody>
                  Pre-filled from preset <Text weight="semibold">{preset.preset.name}</Text>.
                  Changes apply to this run only and are not saved to the preset.
                  {preset.preset.description && <Text block size={200}>{preset.preset.description}</Text>}
                  {carriedScenarioParamNames.length > 0 && <Text block size={200}>
                    Also sends saved parameters this form has no field for: {carriedScenarioParamNames.join(', ')}.
                  </Text>}
                </MessageBarBody>
              </MessageBar>
            )}
            {preset && preset.issues.length > 0 && (
              <MessageBar intent="warning" data-testid="preset-issues">
                <MessageBarBody>
                  This preset may not run as saved here:
                  <ul className={styles.presetIssueList}>
                    {preset.issues.map((issue) => (
                      <li key={`${issue.field}:${issue.message}`}><Text size={200}>{issue.message}</Text></li>
                    ))}
                  </ul>
                </MessageBarBody>
              </MessageBar>
            )}
            {validationError && !requestResult.ok && (
              <MessageBar intent="warning">
                <MessageBarBody role="alert">{requestResult.error}</MessageBarBody>
              </MessageBar>
            )}
            {linkedDatasetError && (
              <MessageBar intent="error" data-testid="linked-datasets-error">
                <MessageBarBody>
                  {linkedDatasetError}{' '}
                  <Link to={pathWithoutBadLinks}>
                    {supportsDatasets ? 'Open without the blank dataset names' : 'Open without the linked datasets'}
                  </Link>
                </MessageBarBody>
              </MessageBar>
            )}
            {(staleSelection || (targetName && unavailableSelection)) && (
              <MessageBar intent="warning">
                <MessageBarBody>
                  PyRIT was reinitialized. Review the refreshed target and technique selections before starting.
                  Your parameter drafts have been preserved.
                  <Button onClick={() => {
                    dispatch({ type: 'keepAvailableTechniques' })
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

            {(supportsTechniques || supportsBaseline) && (
              <ScenarioTechniqueSection
                formId={formId}
                techniques={selectableTechniques}
                selectedTechniques={selectedTechniques}
                supportsTechniques={supportsTechniques}
                singleTechnique={singleTechnique}
                maxConcreteTechniques={scenario.max_concrete_techniques ?? null}
                selectionOverLimit={techniqueSelectionOverLimit}
                selectionError={techniqueSelectionError}
                disabled={submitting}
                tokensWithoutCheckbox={techniquesWithoutCheckbox}
                isSelected={isTechniqueSelected}
                onTechniqueChange={handleTechniqueChange}
                onTagChange={handleTagChange}
                onRemoveToken={(token) => {
                  dispatch({ type: 'replaceTechniques', names: selectedTechniques.filter((name) => name !== token) })
                  clearValidationError()
                }}
              />
            )}

            {(supportsObjectiveTarget || objectiveParameters.length > 0 || scenario.supports_objective_scorer) && (
              <section className={styles.section} aria-labelledby="objective-section-title">
                <Text id="objective-section-title" as="h2" aria-label="Objective" size={400} weight="semibold">
                  <ContextualHelpLabel term="Objective" labelProps={{ weight: 'semibold' }}>
                    The behavior you want to test for. A clear goal lets the attack and scorer work toward the same result.
                  </ContextualHelpLabel>
                </Text>
                {supportsObjectiveTarget && <>
                  <HelpField label="Objective Target" required help={HELP_DEFINITIONS.objectiveTarget}>
                    <Select
                      className={styles.control}
                      value={targetName}
                      disabled={submitting}
                      onChange={(_, data) => { dispatch({ type: 'setTarget', name: data.value }); setSelectionGeneration(runtime.generation) }}
                      data-testid="scenario-target-select"
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
                  </HelpField>
                  {targets.length === 0 && (
                    <Button className={styles.touchTarget} appearance="secondary" icon={<SettingsRegular />}
                      type="button" onClick={() => onNavigate('registry')}>
                      Configure target to launch
                    </Button>
                  )}
                </>}
                {objectiveParameters.map(renderScenarioParameter)}
                {scenario.supports_objective_scorer && !defaultAssertScoring && <ChatScorerPicker
                  label="Objective scorer" usage="scenario" value={objectiveScorer}
                  scorers={scorers} loading={scorersLoading} error={scorersError}
                  disabled={submitting || previewOpen || !runtime.ready}
                  onChange={(value) => { setObjectiveScorer(value); setSelectionGeneration(runtime.generation) }}
                />}
              </section>
            )}

            {((singleTechnique && supportsTechniques) || historyParameters.length > 0) && (
              <section className={styles.section} aria-labelledby="technique-parameters-section-title">
                <Text id="technique-parameters-section-title" as="h2" size={400} weight="semibold">
                  Technique Parameters
                </Text>
                {singleTechnique && supportsTechniques && <>
                  {opaqueSelectedTechnique && <Text size={200} className={styles.hint}>
                    {singleSelectedTechnique} is sent as the preset saved it, so its settings cannot be edited for this run.
                  </Text>}
                  {techniqueEditor.unresolved && !techniqueEditor.error && <Spinner size="tiny" label="Loading technique settings..." />}
                  {techniqueEditor.error && <MessageBar intent="error"><MessageBarBody>
                    {techniqueEditor.error}
                    <Button className={styles.touchTarget} type="button" onClick={techniqueEditor.retry}>Retry technique settings</Button>
                  </MessageBarBody></MessageBar>}
                  {techniqueEditor.hasChanges && <Button
                    className={styles.resetTechnique} appearance="outline" size="small" type="button"
                    disabled={submitting || previewOpen} onClick={techniqueEditor.reset}
                  >Edited - click to reset</Button>}
                  {techniqueEditor.original && <TechniqueEditor key={techniqueEditor.formKey}
                    original={techniqueEditor.original} initial={techniqueEditor.configuration}
                    autoApply onPending={techniqueEditor.markPending} onApplied={techniqueEditor.apply}
                    disabled={!runtime.ready || staleSelection || submitting || previewOpen || conversationBusy} />}
                </>}
                {supportsPrependedConversation ? historyParameters.map(renderScenarioParameter)
                  : historyParameters.length > 0 && <>
                    <Text>This technique does not accept a prepended conversation.</Text>
                    {canEditConversation && <MessageBar intent="warning"><MessageBarBody>
                      Clear the saved conversation before launch. It is not compatible with this technique.
                      <Button className={styles.touchTarget} type="button" disabled={submitting}
                        onClick={() => dispatch({ type: 'clearPrependedConversation' })}>Clear saved conversation</Button>
                    </MessageBarBody></MessageBar>}
                  </>}
              </section>
            )}

            {(supportsDatasets || datasetDefaults.sources.length > 0) && (
            <section className={styles.section} aria-labelledby="datasets-section-title">
              <div className={styles.sectionHeader}>
                <Text id="datasets-section-title" as="h2" aria-label="Datasets" size={400} weight="semibold">
                  <ContextualHelpLabel term="Datasets" labelProps={{ weight: 'semibold', required: defaultAssertScoring }}>
                    Seed examples used by this scan. Limits and filters control which examples are selected.
                  </ContextualHelpLabel>
                </Text>
                <DatasetActions scenarioName={scenario.scenario_name} />
              </div>
              {!datasetConfigurationReady && <Text role="status" size={200}>
                {datasetConfigurationError ? 'Dataset configuration could not be resolved.' : 'Checking dataset configuration...'}
              </Text>}
              {datasetConfigurationError && <MessageBar intent="error"><MessageBarBody>
                {datasetConfigurationError}
                <Button className={styles.touchTarget} onClick={runEstimate.retry}>Retry dataset configuration</Button>
              </MessageBarBody></MessageBar>}
              <ScenarioDatasetTable
                request={datasetEdits} defaults={datasetDefaults} effective={effectiveDatasets}
                options={supportsDatasets ? datasetOptions : { ...datasetOptions, mode: 'fixed', supports_overrides: false }}
                datasets={datasetMetadata.items} disabled={submitting}
                loading={datasetMetadata.loading} metadataError={datasetMetadata.error}
                compatibleOnly={defaultAssertScoring}
                required={defaultAssertScoring} showCatalogLink={false}
                automaticLoading preparing={preparation.preparing} loadError={preparation.error}
                onRetryPreparation={preparation.retry}
                onReload={datasetMetadata.reload} onChange={setDatasetEdits} onValidationChange={setDatasetError}
              />
            </section>
            )}

            {(generalParameters.length > 0 || scenario.uses_default_adversarial_target
              || supportsConcurrency || supportsRetries) && (
            <section className={styles.section} aria-labelledby="parameters-section-title">
              <Text id="parameters-section-title" as="h2" size={400} weight="semibold">
                Parameters
              </Text>
              <div className={styles.dynamicParameters}>
                {generalParameters.map(renderScenarioParameter)}
                {scenario.uses_default_adversarial_target && (
                  <TargetSelect
                    targets={adversarialTargets}
                    value={adversarialTargetName}
                    onChange={(target: TargetInstance | null) => {
                      dispatch({ type: 'setAdversarialTarget', name: target?.target_registry_name ?? '' })
                    }}
                    label="Adversarial Target"
                    help={HELP_DEFINITIONS.adversarialTarget}
                    placeholder="Use server default"
                    disabled={submitting}
                  />
                )}
                {supportsConcurrency && <HelpField label="Max concurrency" help={HELP_DEFINITIONS.maxConcurrency}>
                  <SingleStepSpinButton
                    className={styles.numberInput}
                    value={maxConcurrency}
                    min={MIN_MAX_CONCURRENCY}
                    max={MAX_MAX_CONCURRENCY}
                    disabled={submitting}
                    onChange={(_, data) => dispatch({ type: 'setMaxConcurrency', value: resolveSpinButtonValue(data, maxConcurrency) })}
                    data-testid="max-concurrency-input"
                  />
                </HelpField>}
                {supportsRetries && <HelpField label="Max retries" help={HELP_DEFINITIONS.maxRetries}>
                  <SingleStepSpinButton
                    className={styles.numberInput}
                    value={maxRetries}
                    min={MIN_MAX_RETRIES}
                    max={MAX_MAX_RETRIES}
                    disabled={submitting}
                    onChange={(_, data) => dispatch({ type: 'setMaxRetries', value: resolveSpinButtonValue(data, maxRetries) })}
                    data-testid="max-retries-input"
                  />
                </HelpField>}
              </div>
            </section>
            )}

            <ScenarioEstimateSection
              state={estimateState}
              showDatasetSize={supportsDatasets}
              datasetSizeLabel={datasetSizeLabel}
              showTechniqueCount={supportsTechniques || supportsBaseline}
              techniqueCount={selectedTechniqueCount}
              dynamicParameters={dynamicParameters}
              scenarioParamValues={scenarioParamValues}
            />

            <section className={styles.launchSection} aria-label="Launch scan">
              <Button
                ref={launchButtonRef}
                className={styles.launchButton}
                appearance="primary"
                type="submit"
                disabled={!runtime.ready || !defaultsReady || staleSelection || unavailableSelection || submitting || conversationBusy || Boolean(techniqueEditError) || techniqueSelectionError !== null || Boolean(preparation.error) || (supportsDatasets && datasetError !== null) || linkedDatasetError !== null}
                data-testid="launch-scenario-btn"
              >
                Launch scan
              </Button>
            </section>
          </form>

          <Dialog open={pendingTechnique !== null} onOpenChange={(_, data) => {
            if (!data.open) setPendingTechnique(null)
          }}>
            <DialogSurface><DialogBody>
              <DialogTitle>Discard technique edits?</DialogTitle>
              <DialogContent>Switching technique removes the edits for this run. The registered technique stays unchanged.</DialogContent>
              <DialogActions>
                <Button className={styles.touchTarget} onClick={() => setPendingTechnique(null)}>Cancel</Button>
                <Button className={styles.touchTarget} appearance="primary" onClick={() => {
                  if (pendingTechnique) {
                    techniqueEditor.discard()
                    dispatch({ type: 'replaceTechniques', names: [pendingTechnique] })
                  }
                  setPendingTechnique(null)
                }}>Discard edits and switch</Button>
              </DialogActions>
            </DialogBody></DialogSurface>
          </Dialog>

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

          <ScenarioRunPreviewDialog
            open={previewOpen}
            submitting={submitting}
            confirmDisabled={submitting || conversationBusy || !runtime.ready || !defaultsReady || !requestResult.ok
              || Boolean(preparation.error) || !datasetConfigurationReady}
            datasetConfigurationPending={!datasetConfigurationReady && !datasetConfigurationError}
            datasetConfigurationError={datasetConfigurationError
              ?? (datasetConfigurationReady && !preparationRequestResult.ok ? preparationRequestResult.error : null)}
            onRetryDatasetConfiguration={datasetConfigurationError ? runEstimate.retry : undefined}
            onOpenChange={changePreviewOpen}
            onCancel={dismissPreview}
            onConfirm={() => void confirmLaunch()}
            objectiveTarget={supportsObjectiveTarget ? targetName : null}
            objectiveScorer={scenario.supports_objective_scorer && !defaultAssertScoring
              ? objectiveScorer?.registry_name ?? 'Use scenario default' : null}
            adversarialTarget={scenario.uses_default_adversarial_target ? adversarialTargetName || 'Use server default' : null}
            techniques={supportsTechniques || supportsBaseline ? {
              baseline: supportsBaseline && baselineChecked && !isBaselineForbidden,
              names: supportsTechniques ? selectedTechniques : [],
            } : null}
            techniqueOverride={techniqueEditor.override}
            datasets={supportsDatasets ? {
              names: previewDatasets.length > 0 ? previewDatasets : scenario.default_datasets,
              custom: previewDatasets.length > 0,
              sizeLabel: datasetSizeLabel,
              harmCategories: previewFilters.harm_categories ?? [],
              dataTypes: previewFilters.data_types ?? [],
            } : null}
            dynamicParameters={dynamicParameters}
            scenarioParamValues={scenarioParamValues}
            carriedParameters={carriedScenarioParamNames.map((name) => ({
              name, value: formatCarriedParameterPreview(carriedScenarioParams[name]),
            }))}
            estimateState={estimateState}
          />
        </div>
      </div>
    </section>
  )
}
