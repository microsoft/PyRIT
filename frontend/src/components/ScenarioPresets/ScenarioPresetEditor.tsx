import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'

import {
  Button,
  Field,
  MessageBar,
  MessageBarBody,
  Spinner,
  Text,
  Textarea,
} from '@fluentui/react-components'
import { useNavigate, useParams } from 'react-router'

import ParameterField from '@/components/Parameters/ParameterField'
import type { ParameterFormValue } from '@/components/Parameters/parameterForm'
import DatasetActions from '@/components/Scenarios/DatasetActions'
import ScenarioDatasetTable from '@/components/Scenarios/ScenarioDatasetTable'
import ScenarioTechniqueSelector from '@/components/Scenarios/ScenarioTechniqueSelector'
import { scenarioDatasetDefaults, scenarioDatasetOptions } from '@/components/Scenarios/datasetPresentation'
import {
  dynamicScenarioParameters,
  uniqueTechniqueOptions,
  type ScenarioConfigFormState,
} from '@/components/Scenarios/scenarioConfigForm'
import { acceptsAnyNamedDataset, usesCompatibleDatasetPicker } from '@/components/Scenarios/scenarioLaunchRequest'
import { useScenarioDatasets } from '@/hooks/useScenarioDatasets'
import { scenarioPresetsApi, scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  RegisteredScenario,
  ScenarioPresetResponse,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunSizeEstimateResponse,
} from '@/types'
import { routerPathParamValue } from '@/utils/routeParams'

import { useScenarioPresetEditorStyles } from './ScenarioPresetEditor.styles'
import { PRESETS_ROUTE } from './presetRoutes'
import {
  buildPresetScenarioConfig,
  configToPreset,
  presetToConfigState,
  uneditableScenarioParams,
} from './scenarioPresetForm'

const DATASET_PROJECTION_DEBOUNCE_MS = 250

type LoadStatus = 'loading' | 'ready' | 'not-found' | 'error'

interface DatasetProjectionResult {
  request: ScenarioRunSizeEstimateRequest
  scenarioName: string
  error: string | null
}

/**
 * Edits one scenario of a preset: a stored scenario preset's techniques, datasets,
 * parameters, and description. Its name and scenario are fixed; presets create
 * scenarios from the Registry's Scenario presets page.
 */
export default function ScenarioPresetEditor() {
  const { presetName } = useParams<{ presetName: string }>()
  // Remount on navigation between two scenarios so every field resets to the
  // newly loaded document rather than retaining the previous one's edits.
  return <ScenarioPresetEditorContent key={presetName} presetName={presetName ?? ''} />
}

interface ScenarioPresetEditorContentProps {
  presetName: string
}

function ScenarioPresetEditorContent({ presetName }: ScenarioPresetEditorContentProps) {
  const styles = useScenarioPresetEditorStyles()
  const navigate = useNavigate()
  const decodedName = routerPathParamValue(presetName)

  const [status, setStatus] = useState<LoadStatus>('loading')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState<ScenarioPresetResponse | null>(null)

  const [description, setDescription] = useState('')
  const [scenario, setScenario] = useState<RegisteredScenario | null>(null)
  const [scenarioLoading, setScenarioLoading] = useState(false)
  const [config, setConfig] = useState<ScenarioConfigFormState | null>(null)
  const [scenarioUnavailable, setScenarioUnavailable] = useState<string | null>(null)
  const [datasetError, setDatasetError] = useState<string | null>(null)
  const [datasetProjection, setDatasetProjection] = useState<ScenarioRunSizeEstimateResponse | null>(null)
  const [datasetProjectionResult, setDatasetProjectionResult] = useState<DatasetProjectionResult | null>(null)

  const [saving, setSaving] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const load = async (): Promise<void> => {
      let response: ScenarioPresetResponse
      try {
        response = await scenarioPresetsApi.get(decodedName)
      } catch (err) {
        if (cancelled) return
        const apiError = toApiError(err)
        setLoadError(apiError.detail)
        setStatus(apiError.status === 404 ? 'not-found' : 'error')
        return
      }
      if (cancelled) return
      setLoaded(response)
      setDescription(response.preset.description ?? '')
      setScenarioLoading(true)
      setStatus('ready')

      // The preset's scenario is fetched in full for the configured dataset caps the
      // form shows. A preset may legitimately name a scenario this deployment lacks;
      // that is an unavailable scenario, not a missing preset, so it is not a 404 here.
      try {
        const full = await scenariosApi.getScenario(response.preset.scenario_name)
        if (cancelled) return
        setScenario(full)
        setConfig(presetToConfigState(full, response.preset))
      } catch (scenarioErr) {
        if (cancelled) return
        setScenarioUnavailable(toApiError(scenarioErr).detail)
      } finally {
        if (!cancelled) setScenarioLoading(false)
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [decodedName])

  const scenarioReady = scenario !== null && config !== null
  const datasetMetadata = useScenarioDatasets(scenario?.scenario_name ?? '', scenarioReady)
  const datasetBaseline = useMemo(
    () => (scenario
      ? {
        defaults: datasetProjection?.dataset_defaults ?? scenarioDatasetDefaults(scenario),
        options: datasetProjection?.dataset_options ?? scenarioDatasetOptions(scenario),
      }
      : null),
    [datasetProjection, scenario],
  )

  const dynamicParameters = useMemo(
    () => (scenario ? dynamicScenarioParameters(scenario) : []),
    [scenario],
  )
  const techniqueOptions = useMemo(
    () => (scenario ? uniqueTechniqueOptions(scenario).techniques : []),
    [scenario],
  )
  const carriedScenarioParams = useMemo(
    () => (scenario ? Object.keys(uneditableScenarioParams(scenario, loaded?.preset ?? null)) : []),
    [loaded, scenario],
  )
  const techniqueIssues = loaded?.issues.filter((issue) => issue.field === 'techniques') ?? []
  const builtConfig = useMemo(
    () => (scenario && config ? buildPresetScenarioConfig(scenario, config, loaded?.preset ?? null) : null),
    [config, loaded, scenario],
  )
  const scenarioName = scenario?.scenario_name
  const projectionRequest = builtConfig?.ok ? builtConfig.config : null
  const projectionIsCurrent = datasetProjectionResult?.request === projectionRequest
    && datasetProjectionResult?.scenarioName === scenarioName
  const datasetProjectionLoading = Boolean(scenarioName && projectionRequest && !projectionIsCurrent)
  const datasetProjectionError = projectionIsCurrent ? datasetProjectionResult?.error : null

  useEffect(() => {
    if (!scenarioName || !projectionRequest) return

    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void scenariosApi.estimateRun(scenarioName, projectionRequest, controller.signal)
        .then((response) => {
          if (controller.signal.aborted) return
          setDatasetProjection(response)
          setDatasetProjectionResult({ request: projectionRequest, scenarioName, error: null })
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return
          setDatasetProjectionResult({ request: projectionRequest, scenarioName, error: toApiError(err).detail })
        })
    }, DATASET_PROJECTION_DEBOUNCE_MS)

    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [projectionRequest, scenarioName])

  const updateConfig = (patch: Partial<ScenarioConfigFormState>): void => {
    setConfig((current) => (current ? { ...current, ...patch } : current))
    setValidationError(null)
  }

  const updateScenarioParam = useCallback((parameterName: string, value: ParameterFormValue) => {
    setConfig((current) => (current
      ? { ...current, scenarioParamValues: { ...current.scenarioParamValues, [parameterName]: value } }
      : current))
    setValidationError(null)
  }, [])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (saving || loaded === null) {
      return
    }
    setSaveError(null)
    if (!scenario || !config) {
      setValidationError('Wait for the scenario to finish loading.')
      return
    }
    if (!builtConfig?.ok) {
      setValidationError(builtConfig?.error ?? 'Wait for the scenario to finish loading.')
      return
    }
    if (datasetError) {
      setValidationError(datasetError)
      return
    }
    if (loaded.version === null) {
      setSaveError(`'${loaded.preset.name}' is defined in code and cannot be edited here.`)
      return
    }
    setValidationError(null)

    const preset = configToPreset(
      { name: loaded.preset.name, scenarioName: scenario.scenario_name, description },
      builtConfig.config,
      { scenario, previous: loaded.preset },
    )

    setSaving(true)
    try {
      await scenarioPresetsApi.update(loaded.preset.name, preset, loaded.version)
      navigate(PRESETS_ROUTE)
    } catch (err) {
      setSaveError(toApiError(err).detail)
      setSaving(false)
    }
  }

  if (status === 'loading') {
    return (
      <section className={styles.root}>
        <div className={styles.centeredState}>
          <Spinner label="Loading scenario..." />
        </div>
      </section>
    )
  }

  if (status !== 'ready' || loaded === null) {
    return (
      <section className={styles.root}>
        <div className={styles.centeredState} data-testid="editor-error-state">
          <MessageBar intent="error">
            <MessageBarBody>
              {status === 'not-found' ? `No saved scenario named "${decodedName}".` : loadError}
            </MessageBarBody>
          </MessageBar>
          <Button appearance="primary" onClick={() => navigate(PRESETS_ROUTE)}>
            Back to presets
          </Button>
        </div>
      </section>
    )
  }

  return (
    <section
      className={styles.root}
      data-testid="scenario-preset-editor"
      aria-labelledby="scenario-preset-editor-title"
    >
      <div className={styles.headerText}>
        <Text id="scenario-preset-editor-title" as="h1" size={600} weight="semibold">
          Edit {loaded.preset.scenario_name}
        </Text>
        <Text size={300} className={styles.subtitle}>
          Saved as {loaded.preset.name}. The target, concurrency, and retries are chosen at launch.
        </Text>
      </div>

      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        {saveError && (
          <MessageBar intent="error">
            <MessageBarBody role="alert">{saveError}</MessageBarBody>
          </MessageBar>
        )}
        {validationError && (
          <MessageBar intent="warning">
            <MessageBarBody role="alert">{validationError}</MessageBarBody>
          </MessageBar>
        )}
        {techniqueIssues.length > 0 && (
          <MessageBar intent="warning" data-testid="dropped-techniques-warning">
            <MessageBarBody>
              {techniqueIssues.map((issue) => issue.message).join(' ')} Stored techniques
              are preserved on save unless you remove them below.
            </MessageBarBody>
          </MessageBar>
        )}
        {carriedScenarioParams.length > 0 && (
          <MessageBar intent="info" data-testid="carried-params-notice">
            <MessageBarBody>
              This scenario carries parameters this editor has no field for
              ({carriedScenarioParams.join(', ')}). They are preserved as-is on save.
            </MessageBarBody>
          </MessageBar>
        )}
        {scenarioUnavailable && (
          <MessageBar intent="warning" data-testid="scenario-unavailable-warning">
            <MessageBarBody>
              {loaded.preset.scenario_name} is not available here ({scenarioUnavailable}). Its
              configuration cannot be edited in this deployment.
            </MessageBarBody>
          </MessageBar>
        )}

        <div className={styles.section}>
          <Field label="Description">
            <Textarea
              className={styles.control}
              value={description}
              disabled={saving}
              onChange={(_, data) => setDescription(data.value)}
              data-testid="preset-description-input"
            />
          </Field>
        </div>

        {scenarioLoading && <Spinner label="Loading scenario..." />}

        {scenario && config && (
          <>
            <ScenarioTechniqueSelector
              techniqueOptions={techniqueOptions}
              selectedTechniques={config.techniques}
              includeBaseline={config.includeBaseline}
              isBaselineForbidden={scenario.baseline_policy === 'forbidden'}
              disabled={saving}
              onTechniquesChange={(techniques) => updateConfig({ techniques })}
              onIncludeBaselineChange={(includeBaseline) => updateConfig({ includeBaseline })}
            />

            <section className={styles.section} aria-labelledby="preset-parameters-title">
              <Text id="preset-parameters-title" as="h2" size={400} weight="semibold">
                Parameters
              </Text>
              <div className={styles.dynamicParameters}>
                {dynamicParameters.map((parameter) => (
                  <ParameterField
                    key={parameter.name}
                    parameter={parameter}
                    value={config.scenarioParamValues[parameter.name] ?? ''}
                    disabled={saving}
                    onChange={updateScenarioParam}
                    testIdPrefix="preset-param"
                  />
                ))}
                {datasetProjectionLoading && <Spinner size="tiny" label="Updating dataset preview..." />}
                {datasetProjectionError && (
                  <MessageBar intent="warning">
                    <MessageBarBody>
                      Dataset preview could not be updated: {datasetProjectionError}
                    </MessageBarBody>
                  </MessageBar>
                )}
                {datasetBaseline && <>
                  <ScenarioDatasetTable
                    request={config.datasets}
                    defaults={datasetBaseline.defaults}
                    effective={datasetProjection?.effective_datasets ?? datasetBaseline.defaults}
                    options={datasetBaseline.options}
                    datasets={datasetMetadata.items}
                    disabled={saving}
                    loading={datasetMetadata.loading}
                    metadataError={datasetMetadata.error}
                    compatibleOnly={usesCompatibleDatasetPicker(scenario)}
                    onReload={datasetMetadata.reload}
                    onChange={(datasets) => updateConfig({ datasets })}
                    onValidationChange={setDatasetError}
                  />
                  <DatasetActions
                    scenarioName={scenario.scenario_name}
                    canGenerate={acceptsAnyNamedDataset(scenario, datasetBaseline.options)}
                  />
                </>}
              </div>
            </section>
          </>
        )}

        <div className={styles.actions}>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            type="submit"
            disabled={saving || !scenarioReady}
            data-testid="save-preset-btn"
          >
            {saving ? 'Saving...' : 'Save scenario'}
          </Button>
          <Button
            className={styles.touchTarget}
            appearance="secondary"
            disabled={saving}
            onClick={() => navigate(PRESETS_ROUTE)}
          >
            Cancel
          </Button>
        </div>
      </form>
    </section>
  )
}
