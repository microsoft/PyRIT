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
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import ContextualHelpLabel from '@/components/ContextualHelpLabel'
import HelpField from '@/components/HelpField'
import { HELP_DEFINITIONS } from '@/components/helpDefinitions'
import ConversationEditor, { type ConversationEditorHandle } from '@/components/Chat/ConversationEditor'
import CreateTargetDialog from '@/components/Config/CreateTargetDialog'
import CreateScorerDialog from '@/components/Scorers/CreateScorerDialog'
import ChatScorerPicker from '@/components/Chat/ChatScorerPicker'
import TargetSelect from '@/components/Config/TargetSelect'
import TechniqueEditor from '@/components/Techniques/TechniqueEditor'
import { useConversationDraft } from '@/hooks/useConversationDraft'
import { useRuntime } from '@/hooks/useRuntime'
import { useUserPreferences } from '@/hooks/useUserPreferences'
import { useRunTechniqueEditor } from '@/hooks/useRunTechniqueEditor'
import { useScenarioDatasets } from '@/hooks/useScenarioDatasets'
import { useScenarioDetail } from '@/hooks/useScenarioDetail'
import { useScenarioPreset } from '@/hooks/useScenarioPreset'
import { useScenarioLaunch } from '@/hooks/useScenarioLaunch'
import { useScenarioRunEstimate } from '@/hooks/useScenarioRunEstimate'
import ParameterField from '@/components/Parameters/ParameterField'
import SingleStepSpinButton from '@/components/Parameters/SingleStepSpinButton'
import type { ParameterFormValue } from '@/components/Parameters/parameterForm'
import { PRESET_QUERY_PARAM, presetScannerRoutePath } from '@/components/ScenarioPresets/presetRoutes'
import type { ViewName } from '@/components/Sidebar/Navigation'
import { attacksApi, scenariosApi } from '@/services/api'
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
  ScenarioReadiness,
  TargetInstance,
} from '@/types'
import { findRegisteredScorer } from '@/utils/scorerPresentation'
import { newDraftMessage, toConversationDraft } from '@/utils/conversationDraft'
import { routerPathParamValue, scenarioRunRoutePath } from '@/utils/routeParams'
import { resolvedRoleLabel, targetModelName } from '@/utils/targetIdentity'

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
  type ScenarioLaunchInput,
  scenarioDynamicParameters,
  supportsParameter,
} from './scenarioLaunchRequest'
import ScenarioDatasetTable from './ScenarioDatasetTable'

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
  if (presetName && props.sourceRunId) return <ScenarioDetailStateFrame>
    <MessageBar intent="error"><MessageBarBody>Choose a saved run or a preset, not both.</MessageBarBody></MessageBar>
  </ScenarioDetailStateFrame>
  // A new scenario, conversation, preset, or saved-run link starts a fresh configuration form.
  return <ScenarioDetailContent key={JSON.stringify([encodedScenarioName, initialConversationId, presetName, props.sourceRunId])}
    encodedScenarioName={encodedScenarioName} initialConversationId={initialConversationId}
    presetName={presetName} {...props} />
}

interface ScenarioDetailContentProps extends ScenarioDetailProps {
  encodedScenarioName: string | undefined
  initialConversationId: string
  /** The saved preset that pre-fills the form, or empty for none. */
  presetName: string
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
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  labels,
  operationId,
  defaultsReady = false,
  scorers = [],
  scorersLoading = false,
  scorersError = null,
}: ScenarioLaunchFormProps) {
  const runtime = useRuntime()
  const { rememberScanTargets } = useUserPreferences()
  const [selectionGeneration, setSelectionGeneration] = useState(runtime.generation)
  const staleSelection = selectionGeneration !== runtime.generation
  const [objectiveScorer, setObjectiveScorer] = useState<RegisteredComponentReference | null>(initialRun?.objective_scorer ?? null)
  const [createdTargets, setCreatedTargets] = useState<Array<{ generation: string; target: TargetInstance }>>([])
  const [createdScorers, setCreatedScorers] = useState<Array<{ generation: string; scorer: ScorerInstance }>>([])
  const availableTargets = [...targets, ...createdTargets.filter((entry) => entry.generation === runtime.generation)
    .map((entry) => entry.target).filter((entry) => !targets.some(
    (target) => target.target_registry_name === entry.target_registry_name))]
  const availableScorers = [...scorers, ...createdScorers.filter((entry) => entry.generation === runtime.generation)
    .map((entry) => entry.scorer).filter((entry) => !scorers.some(
    (scorer) => scorer.scorer_registry_name === entry.scorer_registry_name))]
  const [createRole, setCreateRole] = useState<'objective_target' | 'adversarial_target' | 'scoring_target' | null>(null)
  const [createScorer, setCreateScorer] = useState(false)
  const [creationGeneration, setCreationGeneration] = useState(runtime.generation)
  const [scoringTargetName, setScoringTargetName] = useState(initialRun?.scoring_target_name ?? '')
  const scorerError = objectiveScorer && (
    scorersLoading || scorersError || !findRegisteredScorer(objectiveScorer, availableScorers)
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
  const supportsConcurrency = supportsParameter(scenario, 'max_concurrency')
  const supportsRetries = supportsParameter(scenario, 'max_retries')
  const isBaselineForbidden = scenario.baseline_policy === 'forbidden'
  const singleTechnique = scenario.max_concrete_techniques === 1
  const adversarialTargets = availableTargets.filter(
    (target: TargetInstance) => target.capabilities?.supports_multi_turn === true,
  )

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
    && !availableTargets.some((target) => target.target_registry_name === targetName)
  ) || (supportsTechniques
    && selectedTechniques.some((name) => !presetTokens.has(name)
      && !techniqueOptions.some((technique) => technique.name === name)))
  const carriedScenarioParamNames = Object.keys(carriedScenarioParams)
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
  ])
  const estimateResult = useMemo(() => buildEstimateRequest(launchInput), [launchInput])
  const requestResult = useMemo(() => buildRunRequest(launchInput), [launchInput])
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
  const datasetMetadata = useScenarioDatasets()
  const runEstimate = useScenarioRunEstimate({
    scenarioName: scenario.scenario_name,
    request: estimateRequest,
    requestKey: estimateRequestKey,
    invalidReason: estimateResult.ok ? null : estimateResult.error,
    ready: runtime.ready,
    paused: staleSelection,
  })
  const estimateState = runEstimate.state
  const currentDatasetEstimate = runEstimate.current

  const datasetProjection = runEstimate.datasetProjection
  const datasetDefaults: ScenarioDatasetSettings = datasetProjection?.dataset_defaults
    ?? scenarioDatasetDefaults(scenario)
  const effectiveDatasets = datasetProjection?.effective_datasets ?? datasetDefaults
  const datasetOptions: ScenarioDatasetOptions = datasetProjection?.dataset_options
    ?? scenarioDatasetOptions(scenario)
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
  const confirmedDatasetSettings = currentDatasetEstimate?.status === 'resolved'
    ? currentDatasetEstimate.datasetSettings : undefined
  const checkingRefreshPolicies = supportsDatasets && selectedDatasetNames.length > 0 && currentDatasetEstimate === null
  const unconfirmedRefreshPolicies = supportsDatasets && selectedDatasetNames.length > 0
    && !checkingRefreshPolicies && confirmedDatasetSettings === undefined
  const refreshDatasets = [...new Set((confirmedDatasetSettings?.sources ?? [])
    .filter((source) => source.fetch === 'always').map((source) => source.name))]
  const editedRefreshDatasets = refreshDatasets.filter((name) => {
    const metadata = datasetMetadata.items.find((item) => item.name === name)
    return metadata?.loaded && (!metadata.origins?.length
      || metadata.origins.some((origin) => origin === 'user' || origin === 'unknown'))
  })
  const uncheckedRefreshDatasets = refreshDatasets.filter((name) => datasetMetadata.loading || datasetMetadata.error
    || !datasetMetadata.items.some((item) => item.name === name))


  const [readinessState, setReadinessState] = useState<{
    key: string; result?: ScenarioReadiness; error?: string
  } | null>(null)
  useEffect(() => {
    if (!runtime.ready || staleSelection || !estimateRequest || !estimateRequestKey) return
    const controller = new AbortController()
    void scenariosApi.readiness(scenario.scenario_name, estimateRequest, controller.signal).then((result) => {
        if (!controller.signal.aborted) setReadinessState({ key: estimateRequestKey, result })
      }).catch((failure: unknown) => {
        if (!controller.signal.aborted) setReadinessState({ key: estimateRequestKey, error: toApiError(failure).detail })
      })
    return () => { controller.abort() }
  }, [estimateRequest, estimateRequestKey, scenario.scenario_name, runtime.ready, staleSelection])
  const readiness = readinessState?.key === estimateRequestKey ? readinessState.result : undefined
  const readinessError = readinessState?.key === estimateRequestKey ? readinessState.error : undefined
  const rolesReady = Boolean(readiness && readiness.issues.length === 0)
  const needsAdversarial = readiness
    ? [...readiness.bindings, ...readiness.issues].some((entry) => entry.role === 'adversarial_target')
    : scenario.uses_default_adversarial_target
  const defaultScorer = readiness?.bindings.find((entry) => entry.role === 'objective_scorer')
  const needsScoringTarget = readiness
    ? [...readiness.bindings, ...readiness.issues].some((entry) => entry.role === 'scoring_target')
    : !objectiveScorer
  const roleIssues = (role: string) => readiness?.issues.filter((issue) => issue.role === role)
    .map((issue) => <Text key={`${issue.component}:${issue.reason}`} role="alert" size={200}>
      {issue.reason} {issue.remediation}
    </Text>)

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
    submitBlocked: conversationBusy || Boolean(techniqueEditError) || !rolesReady,
    launchBlocked: conversationBusy || Boolean(techniqueEditError) || !runtime.ready || !defaultsReady
      || staleSelection || unavailableSelection || checkingRefreshPolicies || !rolesReady,
    onLaunched: (summary: ScenarioRunSummary) => {
      // The launched request is the one this render built, so it is still current here.
      if (requestResult.ok) rememberScanTargets(availableTargets, requestResult.request)
      datasetMetadata.reload()
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
            {!requestResult.ok && !techniqueEditError && !techniqueSelectionError && (
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
            {validationError && (
              <MessageBar intent="warning">
                <MessageBarBody role="alert">{validationError}</MessageBarBody>
              </MessageBar>
            )}
            {readinessError && <MessageBar intent="error"><MessageBarBody role="alert">
              {readinessError}
            </MessageBarBody></MessageBar>}
            {readiness && readiness.issues.length > 0 && <MessageBar intent="warning">
              <MessageBarBody role="alert" aria-label="Scan configuration issues">
                Configure the required scan roles before launching.
                <ul>{readiness.issues.map((issue) => <li key={`${issue.role}:${issue.component}:${issue.reason}`}>
                  {issue.role}: {issue.reason} {issue.remediation}
                </li>)}</ul>
              </MessageBarBody>
            </MessageBar>}

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
                      {!availableTargets.some((target) => target.target_registry_name === targetName) && (
                        <option value={targetName} disabled>
                          {availableTargets.length === 0 ? 'No targets configured' : 'Select an available target'}
                        </option>
                      )}
                      {availableTargets.map((target) => (
                        <option key={target.target_registry_name} value={target.target_registry_name}>
                          {targetOptionLabel(target)}
                        </option>
                      ))}
                    </Select>
                  </HelpField>
                  <Button className={styles.touchTarget} appearance="secondary" icon={<SettingsRegular />}
                    type="button" onClick={() => { setCreationGeneration(runtime.generation); setCreateRole('objective_target') }}>
                    Create objective target
                  </Button>
                  {roleIssues('objective_target')}
                </>}
                {objectiveParameters.map(renderScenarioParameter)}
                {scenario.supports_objective_scorer && <ChatScorerPicker
                  label="Objective scorer" usage="scenario" value={objectiveScorer}
                  scorers={availableScorers} loading={scorersLoading} error={scorersError}
                  defaultLabel={defaultScorer ? `Scenario rubric: ${defaultScorer.identifier.class_name}` : 'No configured default scorer'}
                  disabled={submitting || previewOpen || !runtime.ready}
                  onChange={(value) => { setObjectiveScorer(value); setSelectionGeneration(runtime.generation) }}
                />}
                {scenario.supports_objective_scorer && <Button className={styles.touchTarget} type="button"
                  disabled={submitting} onClick={() => { setCreationGeneration(runtime.generation); setCreateScorer(true) }}>
                  Create objective scorer
                </Button>}
                {roleIssues('objective_scorer')}
                {(scoringTargetName !== '' || (!objectiveScorer && needsScoringTarget)) && <>
                  <TargetSelect label="Scoring Target" targets={availableTargets} value={scoringTargetName}
                    onChange={(target) => setScoringTargetName(target?.target_registry_name ?? '')}
                    placeholder={resolvedRoleLabel(readiness, 'scoring_target', availableTargets, 'No configured scoring target')}
                    hint="Only needed by the scenario's model-based scoring rubric. Deterministic scorers need no target."
                    disabled={submitting} />
                  {roleIssues('scoring_target')}
                  <Button className={styles.touchTarget} type="button" disabled={submitting}
                    onClick={() => { setCreationGeneration(runtime.generation); setCreateRole('scoring_target') }}>
                    Create scoring target
                  </Button>
                </>}
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
              <Text id="datasets-section-title" as="h2" aria-label="Datasets" size={400} weight="semibold">
                <ContextualHelpLabel term="Datasets" labelProps={{ weight: 'semibold' }}>
                  Seed examples used by this scan. Limits and filters control which examples are selected.
                </ContextualHelpLabel>
              </Text>
              <ScenarioDatasetTable
                request={datasetEdits} defaults={datasetDefaults} effective={effectiveDatasets}
                options={supportsDatasets ? datasetOptions : { ...datasetOptions, mode: 'fixed', supports_overrides: false }}
                datasets={datasetMetadata.items} disabled={submitting}
                loading={datasetMetadata.loading} metadataError={datasetMetadata.error}
                preparing={datasetMetadata.preparing} loadError={datasetMetadata.loadError}
                loadEnabled={runtime.ready && !staleSelection && estimateRequest !== null
                  && confirmedDatasetSettings !== undefined
                  && !confirmedDatasetSettings.sources.some((source) => source.fetch === 'always'
                    && !datasetMetadata.items.some((item) => item.name === source.name && item.loaded))}
                onLoad={() => { if (estimateRequest) void datasetMetadata.loadSelected(scenario.scenario_name, estimateRequest) }}
                onReload={datasetMetadata.reload} onChange={setDatasetEdits} onValidationChange={setDatasetError}
              />
            </section>
            )}

            {(generalParameters.length > 0 || needsAdversarial
              || supportsConcurrency || supportsRetries) && (
            <section className={styles.section} aria-labelledby="parameters-section-title">
              <Text id="parameters-section-title" as="h2" size={400} weight="semibold">
                Parameters
              </Text>
              <div className={styles.dynamicParameters}>
                {generalParameters.map(renderScenarioParameter)}
                {needsAdversarial && (
                  <TargetSelect
                    targets={adversarialTargets}
                    value={adversarialTargetName}
                    onChange={(target: TargetInstance | null) => {
                      dispatch({ type: 'setAdversarialTarget', name: target?.target_registry_name ?? '' })
                    }}
                    label="Adversarial Target"
                    help={HELP_DEFINITIONS.adversarialTarget}
                    placeholder={resolvedRoleLabel(readiness, 'adversarial_target', availableTargets, 'No configured generation target')}
                    disabled={submitting}
                  />
                )}
                {needsAdversarial && <Button className={styles.touchTarget} type="button" disabled={submitting}
                  onClick={() => { setCreationGeneration(runtime.generation); setCreateRole('adversarial_target') }}>
                  Create adversarial target
                </Button>}
                {roleIssues('adversarial_target')}
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
                disabled={!requestResult.ok || !rolesReady || !runtime.ready || !defaultsReady || staleSelection || unavailableSelection || submitting || conversationBusy || Boolean(techniqueEditError) || techniqueSelectionError !== null || datasetMetadata.preparing || (supportsDatasets && datasetError !== null)}
                data-testid="launch-scenario-btn"
              >
                Launch scan
              </Button>
            </section>
          </form>

          {createRole && <CreateTargetDialog open existingTargets={availableTargets}
            onClose={() => setCreateRole(null)} onCreated={(target) => {
              if (creationGeneration === runtime.generation) {
                setCreatedTargets((current) => [...current, { generation: runtime.generation, target }])
                if (createRole === 'objective_target') dispatch({ type: 'setTarget', name: target.target_registry_name })
                if (createRole === 'scoring_target') setScoringTargetName(target.target_registry_name)
                if (createRole === 'adversarial_target') dispatch({ type: 'setAdversarialTarget', name: target.target_registry_name })
              }
              setCreateRole(null)
            }} />}
          {createScorer && <CreateScorerDialog targets={availableTargets} scorers={availableScorers}
            onClose={() => setCreateScorer(false)} onCreated={(scorer) => {
              if (creationGeneration === runtime.generation) {
                setCreatedScorers((current) => [...current, { generation: runtime.generation, scorer }])
                setObjectiveScorer({ registry_name: scorer.scorer_registry_name, identifier_hash: scorer.identifier.hash })
              }
              setCreateScorer(false)
            }} />}
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
            confirmDisabled={!rolesReady || submitting || conversationBusy || !runtime.ready || !defaultsReady || !requestResult.ok
              || checkingRefreshPolicies}
            onOpenChange={changePreviewOpen}
            onCancel={dismissPreview}
            onConfirm={() => void confirmLaunch()}
            refresh={{
              checking: checkingRefreshPolicies,
              unconfirmed: unconfirmedRefreshPolicies,
              refreshed: refreshDatasets,
              edited: editedRefreshDatasets,
              unchecked: uncheckedRefreshDatasets,
            }}
            objectiveTarget={supportsObjectiveTarget ? targetName : null}
            objectiveScorer={scenario.supports_objective_scorer
              ? objectiveScorer?.registry_name ?? (defaultScorer
                ? `${defaultScorer.identifier.class_name} (${defaultScorer.identifier.hash.slice(0, 8)})`
                : 'Not used') : null}
            scoringTarget={readiness?.bindings.some((binding) => binding.role === 'scoring_target')
              ? resolvedRoleLabel(readiness, 'scoring_target', availableTargets, 'Not used') : null}
            adversarialTarget={readiness?.bindings.some((binding) => binding.role === 'adversarial_target')
              ? resolvedRoleLabel(readiness, 'adversarial_target', availableTargets, 'Not used') : null}
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
