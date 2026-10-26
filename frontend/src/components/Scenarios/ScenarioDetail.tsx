import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from 'react'

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
import ConversationEditor, { type ConversationEditorHandle } from '@/components/Chat/ConversationEditor'
import TargetSelect from '@/components/Config/TargetSelect'
import TechniqueEditor from '@/components/Techniques/TechniqueEditor'
import { useConversationDraft } from '@/hooks/useConversationDraft'
import { useRuntime } from '@/hooks/useRuntime'
import { useRunTechniqueEditor } from '@/hooks/useRunTechniqueEditor'
import { useScenarioDatasets } from '@/hooks/useScenarioDatasets'
import { useScenarioDetail } from '@/hooks/useScenarioDetail'
import { useScenarioLaunch } from '@/hooks/useScenarioLaunch'
import { useScenarioRunEstimate } from '@/hooks/useScenarioRunEstimate'
import ParameterField from '@/components/Parameters/ParameterField'
import SingleStepSpinButton from '@/components/Parameters/SingleStepSpinButton'
import type { ParameterFormValue } from '@/components/Parameters/parameterForm'
import type { ViewName } from '@/components/Sidebar/Navigation'
import { attacksApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  AddMessageResponse,
  Parameter,
  RegisteredScenario,
  ScenarioDatasetOptions,
  ScenarioDatasetRequest,
  ScenarioDatasetSettings,
  ScenarioRunSummary,
  TargetInstance,
} from '@/types'
import { newDraftMessage, toConversationDraft } from '@/utils/conversationDraft'
import { routerPathParamValue, scenarioRunRoutePath } from '@/utils/routeParams'
import { targetModelName } from '@/utils/targetIdentity'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import ScenarioEstimateSection from './ScenarioEstimateSection'
import ScenarioRunPreviewDialog from './ScenarioRunPreviewDialog'
import ScenarioTechniqueSection from './ScenarioTechniqueSection'
import { scenarioDatasetDefaults, scenarioDatasetOptions } from './datasetPresentation'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
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
  const { status: scenarioStatus, scenario, error: scenarioError, retry } = useScenarioDetail(decodedScenarioName)

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
              onClick={retry}
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
  const dynamicParameters = useMemo(() => scenarioDynamicParameters(scenario), [scenario])
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
  const adversarialTargets = targets.filter(
    (target: TargetInstance) => target.capabilities?.supports_multi_turn === true,
  )

  const formRules = useMemo<ScenarioLaunchFormRules>(() => ({
    techniqueNames: techniqueOptions.map((technique) => technique.name),
    maxConcreteTechniques: scenario.max_concrete_techniques ?? null,
    singleTechnique,
  }), [techniqueOptions, scenario.max_concrete_techniques, singleTechnique])
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

  const techniqueEditor = useRunTechniqueEditor(
    singleTechnique && supportsTechniques && selectedTechniques.length === 1 ? selectedTechniques[0] : undefined,
    runtime.generation, runtime.ready,
  )
  const [pendingTechnique, setPendingTechnique] = useState<string | null>(null)
  const techniqueEditError = techniqueEditor.unresolved
    ? techniqueEditor.error ?? 'Loading selected technique settings.'
    : techniqueEditor.pending ? 'Wait for valid technique settings before launch.' : undefined
  const supportsPrependedConversation = !singleTechnique
    || techniqueEditor.configuration?.supports_prepended_conversation === true
  const objectiveParameters = dynamicParameters.filter((parameter) => OBJECTIVE_PARAMETER_NAMES.has(parameter.name))
  const historyParameters = dynamicParameters.filter((parameter) => CONVERSATION_PARAMETER_NAMES.has(parameter.name)
    && parameter.name !== conversationConvertersParameter?.name)
  const generalParameters = dynamicParameters.filter((parameter) =>
    !OBJECTIVE_PARAMETER_NAMES.has(parameter.name) && !CONVERSATION_PARAMETER_NAMES.has(parameter.name))
  const unavailableSelection = (
    supportsObjectiveTarget && targetName !== ''
    && !targets.some((target) => target.target_registry_name === targetName)
  ) || (supportsTechniques
    && selectedTechniques.some((name) => !techniqueOptions.some((technique) => technique.name === name)))

  // The one effective configuration both the estimate and the launch read.
  const launchInput = useMemo<ScenarioLaunchInput>(() => ({
    scenario,
    targetName,
    adversarialTargetName,
    techniques: selectedTechniques,
    techniqueOverride: techniqueEditor.override,
    techniqueEditError,
    supportsPrependedConversation,
    dynamicParameters,
    scenarioParamValues,
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
    operationId,
    scenario,
    scenarioParamValues,
    selectedTechniques,
    supportsPrependedConversation,
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
      || staleSelection || unavailableSelection || checkingRefreshPolicies,
    onLaunched: (summary: ScenarioRunSummary) => {
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
  const datasetSizeLabel = !datasetOptions.total_limit ? 'Not applicable'
    : effectiveDatasets.max_total === 'all' ? 'No total limit' : String(effectiveDatasets.max_total)

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
  )

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
            onSubmit={submitForm}
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
                isSelected={isTechniqueSelected}
                onTechniqueChange={handleTechniqueChange}
                onTagChange={handleTagChange}
              />
            )}

            {(supportsObjectiveTarget || objectiveParameters.length > 0) && (
              <section className={styles.section} aria-labelledby="objective-section-title">
                <Text id="objective-section-title" as="h2" size={400} weight="semibold">Objective</Text>
                {supportsObjectiveTarget && <>
                  <Field label="Objective Target" required hint="The registered target this scenario will run against.">
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
                  </Field>
                  {targets.length === 0 && (
                    <Button className={styles.touchTarget} appearance="secondary" icon={<SettingsRegular />}
                      type="button" onClick={() => onNavigate('registry')}>
                      Configure target to launch
                    </Button>
                  )}
                </>}
                {objectiveParameters.map(renderScenarioParameter)}
              </section>
            )}

            {((singleTechnique && supportsTechniques) || historyParameters.length > 0) && (
              <section className={styles.section} aria-labelledby="technique-parameters-section-title">
                <Text id="technique-parameters-section-title" as="h2" size={400} weight="semibold">
                  Technique Parameters
                </Text>
                {singleTechnique && supportsTechniques && <>
                  {techniqueEditor.unresolved && !techniqueEditor.error && <Spinner size="tiny" label="Loading technique settings..." />}
                  {techniqueEditor.error && <MessageBar intent="error"><MessageBarBody>
                    {techniqueEditor.error}
                    <Button className={styles.touchTarget} type="button" onClick={techniqueEditor.retry}>Retry technique settings</Button>
                  </MessageBarBody></MessageBar>}
                  {techniqueEditor.hasChanges && <div className={styles.previewBadges}>
                    {techniqueEditor.edited && <Badge appearance="outline">Edited for this run</Badge>}
                    <Button className={styles.touchTarget} type="button" disabled={submitting || previewOpen}
                      onClick={techniqueEditor.reset}>Reset to original</Button>
                  </div>}
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
              <Text id="datasets-section-title" as="h2" size={400} weight="semibold">Datasets</Text>
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
                    onChange={(_, data) => dispatch({ type: 'setMaxConcurrency', value: resolveSpinButtonValue(data, maxConcurrency) })}
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
                    onChange={(_, data) => dispatch({ type: 'setMaxRetries', value: resolveSpinButtonValue(data, maxRetries) })}
                    data-testid="max-retries-input"
                  />
                </Field>}
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
                disabled={!runtime.ready || !defaultsReady || staleSelection || unavailableSelection || submitting || conversationBusy || Boolean(techniqueEditError) || techniqueSelectionError !== null || datasetMetadata.preparing || (supportsDatasets && datasetError !== null)}
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
            estimateState={estimateState}
          />
        </div>
      </div>
    </section>
  )
}
