import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import {
  Badge,
  Button,
  Combobox,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Option,
  Spinner,
  Text,
  Textarea,
} from '@fluentui/react-components'
import {
  AddRegular,
  ChevronDownRegular,
  ChevronUpRegular,
  DeleteRegular,
} from '@fluentui/react-icons'
import { useBeforeUnload, useBlocker, useNavigate, useParams } from 'react-router'

import ConfirmDialog from '@/components/ConfirmDialog'
import { scenarioPresetsApi, scenarioSuitesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ScenarioPresetResponse,
  ScenarioSuite,
  ScenarioSuiteEntry,
  ScenarioSuiteEntryStatus,
  ScenarioSuiteResponse,
} from '@/types'
import { generateClientId } from '@/utils/clientId'
import { validateRegistryName } from '@/utils/registryName'
import { routerPathParamValue } from '@/utils/routeParams'

import { useScenarioSuiteEditorStyles } from './ScenarioSuiteEditor.styles'
import { SUITES_ROUTE } from './suiteRoutes'

type LoadStatus = 'loading' | 'ready' | 'not-found' | 'error'

interface SuiteDraft {
  name: string
  description: string
  entries: ScenarioSuiteEntry[]
}

function entryStatusLabel(status: ScenarioSuiteEntryStatus): string {
  if (status.origin === 'registered') {
    return 'Defined in code'
  }
  if (status.origin === 'stored') {
    return 'Stored preset'
  }
  return 'Unavailable'
}

function entryStatusColor(status: ScenarioSuiteEntryStatus): 'brand' | 'informative' | 'warning' {
  if (status.issues.length > 0 || status.origin === null) {
    return 'warning'
  }
  return status.origin === 'registered' ? 'informative' : 'brand'
}

function draftSignature(draft: SuiteDraft): string {
  return JSON.stringify(draft)
}

export default function ScenarioSuiteEditor({ mode }: { mode: 'create' | 'edit' }) {
  const { suiteName } = useParams<{ suiteName: string }>()
  return <ScenarioSuiteEditorContent key={suiteName ?? 'new'} mode={mode} suiteName={suiteName} />
}

interface ScenarioSuiteEditorContentProps {
  mode: 'create' | 'edit'
  suiteName: string | undefined
}

function ScenarioSuiteEditorContent({ mode, suiteName }: ScenarioSuiteEditorContentProps) {
  const styles = useScenarioSuiteEditorStyles()
  const navigate = useNavigate()
  const decodedName = suiteName === undefined ? '' : routerPathParamValue(suiteName)
  const allowNavigation = useRef(false)

  const [status, setStatus] = useState<LoadStatus>('loading')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [presetLoadError, setPresetLoadError] = useState<string | null>(null)
  const [availablePresets, setAvailablePresets] = useState<ScenarioPresetResponse[]>([])
  const [loaded, setLoaded] = useState<ScenarioSuiteResponse | null>(null)
  const [savedDraft, setSavedDraft] = useState<SuiteDraft>({
    name: mode === 'edit' ? decodedName : '',
    description: '',
    entries: [],
  })

  const [name, setName] = useState(mode === 'edit' ? decodedName : '')
  const [description, setDescription] = useState('')
  const [entries, setEntries] = useState<ScenarioSuiteEntry[]>([])
  const [selectedPresetName, setSelectedPresetName] = useState('')
  const [saving, setSaving] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  const currentDraft: SuiteDraft = { name, description, entries }
  const hasUnsavedChanges = status === 'ready'
    && draftSignature(currentDraft) !== draftSignature(savedDraft)
  const shouldBlockNavigation = useCallback(
    (): boolean => hasUnsavedChanges && !allowNavigation.current,
    [hasUnsavedChanges],
  )
  const blocker = useBlocker(shouldBlockNavigation)

  useBeforeUnload(useCallback((event: BeforeUnloadEvent): void => {
    if (hasUnsavedChanges && !allowNavigation.current) {
      event.preventDefault()
      event.returnValue = ''
    }
  }, [hasUnsavedChanges]))

  useEffect(() => {
    let cancelled = false

    const load = async (): Promise<void> => {
      setPresetLoadError(null)
      try {
        const presetsPromise = scenarioPresetsApi.list(undefined, false)
        if (mode === 'create') {
          const presets = await presetsPromise
          if (cancelled) return
          setAvailablePresets(presets.items)
          setStatus('ready')
          return
        }

        const [suiteResult, presetsResult] = await Promise.allSettled([
          scenarioSuitesApi.get(decodedName),
          presetsPromise,
        ])
        if (cancelled) return
        if (suiteResult.status === 'rejected') {
          throw suiteResult.reason
        }
        const suite = suiteResult.value
        const nextDraft: SuiteDraft = {
          name: suite.suite.name,
          description: suite.suite.description ?? '',
          entries: suite.suite.entries,
        }
        if (presetsResult.status === 'fulfilled') {
          setAvailablePresets(presetsResult.value.items)
        } else {
          setAvailablePresets([])
          setPresetLoadError(toApiError(presetsResult.reason).detail)
        }
        setLoaded(suite)
        setName(nextDraft.name)
        setDescription(nextDraft.description)
        setEntries(nextDraft.entries)
        setSavedDraft(nextDraft)
        setStatus('ready')
      } catch (err: unknown) {
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

  const presetsByName = useMemo(
    () => new Map(availablePresets.map((item) => [item.preset.name, item])),
    [availablePresets],
  )
  const loadedStatusesByEntryId = useMemo(
    () => new Map(loaded?.entry_statuses.map((entryStatus) => [entryStatus.entry_id, entryStatus])),
    [loaded],
  )

  const statusForEntry = (entry: ScenarioSuiteEntry): ScenarioSuiteEntryStatus => {
    const loadedStatus = loadedStatusesByEntryId.get(entry.entry_id)
    if (loadedStatus?.preset_name === entry.preset_name) {
      return loadedStatus
    }
    const preset = presetsByName.get(entry.preset_name)
    return {
      entry_id: entry.entry_id,
      preset_name: entry.preset_name,
      origin: preset?.origin ?? null,
      preset_version: preset?.version ?? null,
      issues: preset?.issues ?? [
        {
          field: 'preset_name',
          message: `Preset '${entry.preset_name}' is not available in this deployment.`,
        },
      ],
    }
  }

  const addEntry = (): void => {
    if (selectedPresetName === '') {
      return
    }
    setEntries((current) => {
      const existingIds = new Set(current.map((entry) => entry.entry_id))
      let entryId = generateClientId()
      while (existingIds.has(entryId)) {
        entryId = generateClientId()
      }
      return [...current, { entry_id: entryId, preset_name: selectedPresetName }]
    })
    setSelectedPresetName('')
    setValidationError(null)
  }

  const removeEntry = (entryId: string): void => {
    setEntries((current) => current.filter((entry) => entry.entry_id !== entryId))
    setValidationError(null)
  }

  const moveEntry = (entryId: string, offset: -1 | 1): void => {
    setEntries((current) => {
      const index = current.findIndex((entry) => entry.entry_id === entryId)
      const nextIndex = index + offset
      if (index < 0 || nextIndex < 0 || nextIndex >= current.length) {
        return current
      }
      const reordered = [...current]
      const [entry] = reordered.splice(index, 1)
      reordered.splice(nextIndex, 0, entry)
      return reordered
    })
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (saving) {
      return
    }
    setSaveError(null)

    const trimmedName = name.trim()
    const nameError = validateRegistryName(trimmedName)
    if (nameError) {
      setValidationError(nameError)
      return
    }
    setValidationError(null)

    const suite: ScenarioSuite = {
      name: trimmedName,
      schema_version: loaded?.suite.schema_version ?? 1,
      entries: entries.map((entry) => ({ ...entry })),
    }
    const trimmedDescription = description.trim()
    if (trimmedDescription !== '') {
      suite.description = trimmedDescription
    }
    if (loaded?.suite.author != null) {
      suite.author = loaded.suite.author
    }

    setSaving(true)
    try {
      if (loaded === null) {
        await scenarioSuitesApi.create(suite)
      } else {
        await scenarioSuitesApi.update(loaded.suite.name, suite, loaded.version)
      }
      allowNavigation.current = true
      navigate(SUITES_ROUTE)
    } catch (err: unknown) {
      setSaveError(toApiError(err).detail)
      setSaving(false)
    }
  }

  const discardAndLeave = (): void => {
    if (blocker.state === 'blocked') {
      blocker.proceed()
    }
  }

  const keepEditing = (): void => {
    if (blocker.state === 'blocked') {
      blocker.reset()
    }
  }

  if (status === 'loading') {
    return (
      <section className={styles.root}>
        <div className={styles.centeredState}>
          <Spinner label="Loading suite..." />
        </div>
      </section>
    )
  }

  if (status !== 'ready') {
    return (
      <section className={styles.root}>
        <div className={styles.centeredState} data-testid="suite-editor-error-state">
          <MessageBar intent="error">
            <MessageBarBody>
              {status === 'not-found' ? `No suite named "${decodedName}".` : loadError}
            </MessageBarBody>
          </MessageBar>
          <Button appearance="primary" onClick={() => navigate(SUITES_ROUTE)}>
            Back to suites
          </Button>
        </div>
      </section>
    )
  }

  const editing = loaded !== null

  return (
    <section
      className={styles.root}
      data-testid="scenario-suite-editor"
      aria-labelledby="scenario-suite-editor-title"
    >
      <div className={styles.headerText}>
        <Text id="scenario-suite-editor-title" as="h1" size={600} weight="semibold">
          {editing ? `Edit ${decodedName}` : 'New suite'}
        </Text>
        <Text size={300} className={styles.subtitle}>
          Arrange scenario presets in the order they should appear. Saving a suite does not run it.
        </Text>
      </div>

      <form className={styles.form} onSubmit={(event) => void handleSubmit(event)} noValidate>
        {presetLoadError && (
          <MessageBar intent="warning">
            <MessageBarBody role="alert">
              Preset library unavailable. Existing entries can still be edited and saved, but new presets cannot
              be added. {presetLoadError}
            </MessageBarBody>
          </MessageBar>
        )}
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

        <section className={styles.section} aria-labelledby="suite-definition-title">
          <div className={styles.sectionHeader}>
            <Text id="suite-definition-title" as="h2" size={400} weight="semibold">
              Definition
            </Text>
            <Text size={200} className={styles.meta}>
              Schema version {loaded?.suite.schema_version ?? 1}
              {editing ? ` · Author: ${loaded?.suite.author ?? '—'}` : ''}
            </Text>
          </div>
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
              data-testid="suite-name-input"
            />
          </Field>
          <Field label="Description">
            <Textarea
              className={styles.control}
              value={description}
              disabled={saving}
              onChange={(_, data) => setDescription(data.value)}
              data-testid="suite-description-input"
            />
          </Field>
        </section>

        <section className={styles.section} aria-labelledby="suite-entries-title">
          <div className={styles.sectionHeader}>
            <div className={styles.sectionHeading}>
              <Text id="suite-entries-title" as="h2" size={400} weight="semibold">
                Entries
              </Text>
              <Text size={200} className={styles.meta}>
                Duplicate preset references are allowed. Entry IDs remain stable when reordered.
              </Text>
            </div>
            <Text size={200}>{entries.length} entr{entries.length === 1 ? 'y' : 'ies'}</Text>
          </div>

          {availablePresets.length === 0 ? (
            <MessageBar intent="info">
              <MessageBarBody>
                No presets are available to add. Existing unavailable references can still be
                preserved, reordered, removed, and saved.
              </MessageBarBody>
            </MessageBar>
          ) : (
            <div className={styles.addRow}>
              <Field label="Preset" className={styles.presetPicker}>
                <Combobox
                  value={selectedPresetName}
                  selectedOptions={selectedPresetName === '' ? [] : [selectedPresetName]}
                  placeholder="Select a preset"
                  disabled={saving}
                  onOptionSelect={(_, data) => setSelectedPresetName(data.optionValue ?? '')}
                  data-testid="suite-preset-select"
                >
                  {availablePresets.map((item) => (
                    <Option key={item.preset.name} value={item.preset.name}>
                      {item.preset.name}
                    </Option>
                  ))}
                </Combobox>
              </Field>
              <Button
                className={styles.touchTarget}
                type="button"
                appearance="secondary"
                icon={<AddRegular />}
                disabled={saving || selectedPresetName === ''}
                onClick={addEntry}
                data-testid="add-suite-entry"
              >
                Add preset
              </Button>
            </div>
          )}

          {entries.length === 0 ? (
            <Text className={styles.meta}>This suite has no entries.</Text>
          ) : (
            <ol className={styles.entryList}>
              {entries.map((entry, index) => {
                const entryStatus = statusForEntry(entry)
                return (
                  <li
                    key={entry.entry_id}
                    className={styles.entryCard}
                    data-testid={`suite-entry-${entry.entry_id}`}
                  >
                    <div className={styles.entryTitle}>
                      <div className={styles.entryIdentity}>
                        <Text weight="semibold">
                          {index + 1}. {entry.preset_name}
                        </Text>
                        <Badge
                          appearance="tint"
                          color={entryStatusColor(entryStatus)}
                        >
                          {entryStatusLabel(entryStatus)}
                        </Badge>
                      </div>
                      <div className={styles.entryActions}>
                        <Button
                          className={styles.touchTarget}
                          type="button"
                          appearance="subtle"
                          size="small"
                          icon={<ChevronUpRegular />}
                          aria-label={`Move entry ${index + 1} up`}
                          disabled={saving || index === 0}
                          onClick={() => moveEntry(entry.entry_id, -1)}
                        />
                        <Button
                          className={styles.touchTarget}
                          type="button"
                          appearance="subtle"
                          size="small"
                          icon={<ChevronDownRegular />}
                          aria-label={`Move entry ${index + 1} down`}
                          disabled={saving || index === entries.length - 1}
                          onClick={() => moveEntry(entry.entry_id, 1)}
                        />
                        <Button
                          className={styles.touchTarget}
                          type="button"
                          appearance="subtle"
                          size="small"
                          icon={<DeleteRegular />}
                          aria-label={`Remove entry ${index + 1}`}
                          disabled={saving}
                          onClick={() => removeEntry(entry.entry_id)}
                        />
                      </div>
                    </div>
                    <Text size={200} className={styles.meta}>
                      Entry ID: {entry.entry_id}
                    </Text>
                    {entryStatus.preset_version && (
                      <Text size={200} className={styles.meta}>
                        Current preset version: {entryStatus.preset_version}
                      </Text>
                    )}
                    {entryStatus.issues.length > 0 ? (
                      <ul className={styles.issueList}>
                        {entryStatus.issues.map((issue) => (
                          <li key={`${issue.field}:${issue.message}`}>
                            <Text size={200}>{issue.message}</Text>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <Text size={200} className={styles.meta}>No advisory issues.</Text>
                    )}
                  </li>
                )
              })}
            </ol>
          )}
        </section>

        <div className={styles.actions}>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            type="submit"
            disabled={saving}
            data-testid="save-suite-btn"
          >
            {saving ? 'Saving...' : 'Save suite'}
          </Button>
          <Button
            className={styles.touchTarget}
            appearance="secondary"
            type="button"
            disabled={saving}
            onClick={() => navigate(SUITES_ROUTE)}
          >
            Cancel
          </Button>
        </div>
      </form>

      <ConfirmDialog
        open={blocker.state === 'blocked'}
        title="Discard unsaved changes?"
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
        onConfirm={discardAndLeave}
        onCancel={keepEditing}
      >
        Your unsaved suite changes will be lost if you continue.
      </ConfirmDialog>
    </section>
  )
}
