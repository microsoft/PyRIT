import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'

import {
  Button,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Option,
  Spinner,
  Combobox,
  Text,
  Textarea,
} from '@fluentui/react-components'
import { useNavigate, useParams } from 'react-router'

import ParameterField from '@/components/Parameters/ParameterField'
import type { ParameterFormValue } from '@/components/Parameters/parameterForm'
import ScenarioDatasetTable from '@/components/Scenarios/ScenarioDatasetTable'
import ScenarioTechniqueSelector from '@/components/Scenarios/ScenarioTechniqueSelector'
import { scenarioDatasetDefaults, scenarioDatasetOptions } from '@/components/Scenarios/datasetPresentation'
import {
  buildScenarioConfig,
  dynamicScenarioParameters,
  initialScenarioConfigState,
  uniqueTechniqueOptions,
  type ScenarioConfigFormState,
} from '@/components/Scenarios/scenarioConfigForm'
import { useScenarioDatasets } from '@/hooks/useScenarioDatasets'
import { scenarioPresetsApi, scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  RegisteredScenario,
  ScenarioPresetResponse,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunSizeEstimateResponse,
} from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'
import { routerPathParamValue } from '@/utils/routeParams'

import { useScenarioPresetEditorStyles } from './ScenarioPresetEditor.styles'
import { PRESETS_ROUTE } from './presetRoutes'
import {
  configToPreset,
  presetToConfigState,
  uneditableScenarioParams,
  validatePresetName,
} from './scenarioPresetForm'

/** Items requested per catalog page while paging the scenario picker's options. */
const CATALOG_PAGE_SIZE = 200
const DATASET_PROJECTION_DEBOUNCE_MS = 250

type LoadStatus = 'loading' | 'ready' | 'not-found' | 'error'

interface DatasetProjectionResult {
  request: ScenarioRunSizeEstimateRequest
  scenarioName: string
  error: string | null
}

export default function ScenarioPresetEditor({ mode }: { mode: 'create' | 'edit' }) {
  const { presetName } = useParams<{ presetName: string }>()
  // Remount on navigation between two presets so every field resets to the
  // newly loaded document rather than retaining the previous one's edits.
  return <ScenarioPresetEditorContent key={presetName ?? 'new'} mode={mode} presetName={presetName} />
}

interface ScenarioPresetEditorContentProps {
  mode: 'create' | 'edit'
  presetName: string | undefined
}

function ScenarioPresetEditorContent({ mode, presetName }: ScenarioPresetEditorContentProps) {
  const styles = useScenarioPresetEditorStyles()
  const navigate = useNavigate()
  const decodedName = presetName === undefined ? '' : routerPathParamValue(presetName)

  const [status, setStatus] = useState<LoadStatus>('loading')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [catalog, setCatalog] = useState<RegisteredScenario[]>([])
  const [loaded, setLoaded] = useState<ScenarioPresetResponse | null>(null)

  const [name, setName] = useState(mode === 'edit' ? decodedName : '')
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
      try {
        const scenarios = await fetchAllPages(
          (cursor) => scenariosApi.listCatalog(CATALOG_PAGE_SIZE, cursor, false),
          undefined,
          (entry) => entry.scenario_name,
        )
        if (cancelled) return
        setCatalog(scenarios)

        if (mode === 'create') {
          setStatus('ready')
          return
        }

        const response = await scenarioPresetsApi.get(decodedName)
        if (cancelled) return
        setLoaded(response)
        setName(response.preset.name)
        setDescription(response.preset.description ?? '')
        setStatus('ready')

        // The list endpoint omits run-size estimates, so the preset's scenario is
        // re-fetched in full to get the configured dataset caps the form shows.
        // A preset may legitimately name a scenario this deployment lacks; that
        // is an unavailable scenario, not a missing preset, so it must not be
        // reported as a 404 on the preset itself.
        try {
          const full = await scenariosApi.getScenario(response.preset.scenario_name)
          if (cancelled) return
          setScenario(full)
          setConfig(presetToConfigState(full, response.preset))
        } catch (scenarioErr) {
          if (cancelled) return
          setScenarioUnavailable(toApiError(scenarioErr).detail)
        }
      } catch (err) {
        if (cancelled) return
        const apiError = toApiError(err)
        setLoadError(apiError.detail)
        setStatus(apiError.status === 404 ? 'not-found' : 'error')
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [decodedName, mode])

  const handleScenarioChange = useCallback(async (nextScenarioName: string): Promise<void> => {
    setScenarioLoading(true)
    setSaveError(null)
    setValidationError(null)
    setDatasetProjection(null)
    setDatasetProjectionResult(null)
    try {
      const full = await scenariosApi.getScenario(nextScenarioName)
      setScenario(full)
      setConfig(initialScenarioConfigState(full))
    } catch (err) {
      setSaveError(toApiError(err).detail)
    } finally {
      setScenarioLoading(false)
    }
  }, [])

  const datasetMetadata = useScenarioDatasets()
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
    () => (config ? buildScenarioConfig({
      techniques: config.techniques,
      dynamicParameters,
      scenarioParamValues: config.scenarioParamValues,
      datasets: config.datasets,
      includeBaseline: config.includeBaseline,
    }) : null),
    [config, dynamicParameters],
  )
  const scenarioName = scenario?.scenario_name
  const projectionRequest = !scenarioLoading && builtConfig?.ok ? builtConfig.config : null
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
    if (saving) {
      return
    }
    setSaveError(null)

    const trimmedName = name.trim()
    const nameError = validatePresetName(trimmedName)
    if (nameError) {
      setValidationError(nameError)
      return
    }
    if (!scenario || !config) {
      setValidationError('Select a scenario.')
      return
    }
    if (!builtConfig?.ok) {
      setValidationError(builtConfig?.error ?? 'Select a scenario.')
      return
    }
    if (datasetError) {
      setValidationError(datasetError)
      return
    }
    setValidationError(null)

    const preset = configToPreset(
      { name: trimmedName, scenarioName: scenario.scenario_name, description },
      builtConfig.config,
      { scenario, previous: loaded?.preset ?? null },
    )

    setSaving(true)
    try {
      if (loaded === null) {
        await scenarioPresetsApi.create(preset)
      } else if (loaded.version === null) {
        setSaveError(`Preset '${loaded.preset.name}' is defined in code and cannot be edited here.`)
        setSaving(false)
        return
      } else {
        await scenarioPresetsApi.update(loaded.preset.name, preset, loaded.version)
      }
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
          <Spinner label="Loading preset..." />
        </div>
      </section>
    )
  }

  if (status !== 'ready') {
    return (
      <section className={styles.root}>
        <div className={styles.centeredState} data-testid="editor-error-state">
          <MessageBar intent="error">
            <MessageBarBody>
              {status === 'not-found' ? `No preset named "${decodedName}".` : loadError}
            </MessageBarBody>
          </MessageBar>
          <Button appearance="primary" onClick={() => navigate(PRESETS_ROUTE)}>
            Back to presets
          </Button>
        </div>
      </section>
    )
  }

  const editing = loaded !== null

  return (
    <section
      className={styles.root}
      data-testid="scenario-preset-editor"
      aria-labelledby="scenario-preset-editor-title"
    >
      <div className={styles.headerText}>
        <Text id="scenario-preset-editor-title" as="h1" size={600} weight="semibold">
          {editing ? `Edit ${decodedName}` : 'New preset'}
        </Text>
        <Text size={300} className={styles.subtitle}>
          A preset stores what to test. The target, concurrency and retries are chosen at launch.
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
              This preset carries parameters this editor has no field for
              ({carriedScenarioParams.join(', ')}). They are preserved as-is on save.
            </MessageBarBody>
          </MessageBar>
        )}
        {scenarioUnavailable && (
          <MessageBar intent="warning" data-testid="scenario-unavailable-warning">
            <MessageBarBody>
              This preset targets {loaded?.preset.scenario_name}, which is not available here
              ({scenarioUnavailable}). Its configuration cannot be edited in this deployment.
            </MessageBarBody>
          </MessageBar>
        )}

        <div className={styles.section}>
          <Field
            label="Name"
            required
            hint="Lowercase letters, digits and underscores. Cannot be changed after creation."
          >
            <Input
              className={styles.control}
              value={name}
              disabled={editing || saving}
              onChange={(_, data) => {
                setName(data.value)
                setValidationError(null)
              }}
              data-testid="preset-name-input"
            />
          </Field>
          <Field label="Description">
            <Textarea
              className={styles.control}
              value={description}
              disabled={saving}
              onChange={(_, data) => setDescription(data.value)}
              data-testid="preset-description-input"
            />
          </Field>
          <Field label="Scenario" required hint="Changing the scenario resets the configuration below.">
            <Combobox
              className={styles.control}
              value={scenario?.scenario_name ?? ''}
              selectedOptions={scenario ? [scenario.scenario_name] : []}
              disabled={editing || saving || scenarioLoading}
              placeholder="Select a scenario"
              onOptionSelect={(_, data) => {
                if (data.optionValue) {
                  void handleScenarioChange(data.optionValue)
                }
              }}
              data-testid="preset-scenario-select"
            >
              {catalog.map((entry) => (
                <Option key={entry.scenario_name} value={entry.scenario_name}>
                  {entry.scenario_name}
                </Option>
              ))}
            </Combobox>
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
                    value={config.scenarioParamValues[parameter.name]}
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
                {datasetBaseline && (
                  <ScenarioDatasetTable
                    request={config.datasets}
                    defaults={datasetBaseline.defaults}
                    effective={datasetProjection?.effective_datasets ?? datasetBaseline.defaults}
                    options={datasetBaseline.options}
                    datasets={datasetMetadata.items}
                    disabled={saving}
                    loading={datasetMetadata.loading}
                    metadataError={datasetMetadata.error}
                    onReload={datasetMetadata.reload}
                    onChange={(datasets) => updateConfig({ datasets })}
                    onValidationChange={setDatasetError}
                  />
                )}
              </div>
            </section>
          </>
        )}

        <div className={styles.actions}>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            type="submit"
            disabled={saving || !scenario}
            data-testid="save-preset-btn"
          >
            {saving ? 'Saving...' : 'Save preset'}
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
