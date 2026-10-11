import { useState } from 'react'

import { Button, Combobox, Field, Input, MessageBar, MessageBarBody, Option, Select, Text } from '@fluentui/react-components'

import type {
  DatasetInfo, ScenarioDatasetLimit, ScenarioDatasetOptions, ScenarioDatasetRequest,
  ScenarioDatasetSettings, ScenarioDatasetSourceInput,
} from '@/types'

import { useScenarioDatasetTableStyles } from './ScenarioDatasetTable.styles'

interface ScenarioDatasetTableProps {
  request: ScenarioDatasetRequest
  defaults: ScenarioDatasetSettings
  effective: ScenarioDatasetSettings
  options: ScenarioDatasetOptions
  datasets: DatasetInfo[]
  disabled: boolean
  loading: boolean
  metadataError: string | null
  onReload: () => void
  onChange: (request: ScenarioDatasetRequest) => void
  onValidationChange: (error: string | null) => void
}

interface DatasetLimitFieldProps {
  label: string
  value: ScenarioDatasetLimit | null | undefined
  effective: number | 'all'
  disabled: boolean
  onChange: (value: ScenarioDatasetLimit) => void
  onError: (error: string | null) => void
}

function DatasetLimitField({ label, value, effective, disabled, onChange, onError }: DatasetLimitFieldProps) {
  const [invalidText, setInvalidText] = useState<string | null>(null)
  const styles = useScenarioDatasetTableStyles()
  return (
    <Field label={label} hint={`Default/effective: ${effective}. Blank or default retains the scenario setting.`}>
      <Input
        className={styles.input}
        aria-label={label}
        disabled={disabled}
        value={invalidText ?? (value == null || value === 'default' ? '' : String(value))}
        placeholder="default, all, or positive integer"
        onChange={(_, data) => {
          const text = data.value.trim()
          const parsed = Number(text)
          if (text !== '' && text !== 'default' && text !== 'all' && (!Number.isSafeInteger(parsed) || parsed < 1)) {
            setInvalidText(data.value)
            onError(`${label} must be a positive integer, all, or default.`)
            return
          }
          setInvalidText(null)
          onError(null)
          onChange(text === '' || text === 'default' ? 'default' : text === 'all' ? 'all' : parsed)
        }}
      />
    </Field>
  )
}

/** Displays effective settings while emitting only user edits, never catalog defaults. */
export default function ScenarioDatasetTable({
  request, defaults, effective, options, datasets, disabled, loading, metadataError,
  onReload, onChange, onValidationChange,
}: ScenarioDatasetTableProps) {
  const styles = useScenarioDatasetTableStyles()
  const [newName, setNewName] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const selected: ScenarioDatasetSourceInput[] = request.sources ?? effective.sources.map((source) => ({ name: source.name }))
  const canSelect = options.supports_overrides && options.mode !== 'fixed'
  const canEdit = options.supports_overrides && options.source_settings !== false
  const candidates = datasets.filter((dataset) => options.mode === 'any' || options.allowed_names.includes(dataset.name))
  const setError = (key: string, error: string | null): void => {
    const next = { ...errors }
    if (error) next[key] = error
    else delete next[key]
    setErrors(next)
    onValidationChange(Object.values(next)[0] ?? null)
  }
  const updateSource = (name: string, edits: Partial<ScenarioDatasetSourceInput>): void => {
    onChange({ ...request, sources: selected.map((source) => source.name === name ? { ...source, ...edits } : source) })
  }
  const updateGlobal = (key: 'max_per_dataset' | 'max_total', value: ScenarioDatasetLimit): void => {
    const edits = { ...request }
    if (value === 'default') delete edits[key]
    else edits[key] = value
    onChange(edits)
  }
  const invalidNames = selected.filter((source) =>
    options.mode === 'subset' && !options.allowed_names.includes(source.name),
  )
  return (
    <section className={styles.root} aria-label="Dataset configuration">
      <Text weight="semibold">Datasets</Text>
      <Button className={styles.button} disabled={loading || disabled} onClick={onReload}>Reload dataset metadata</Button>
      {metadataError && <MessageBar intent="warning"><MessageBarBody>{metadataError}</MessageBarBody></MessageBar>}
      {invalidNames.length > 0 && <MessageBar intent="error" role="alert"><MessageBarBody>
        Invalid selection for current parameters: {invalidNames.map((source) => source.name).join(', ')}
      </MessageBarBody></MessageBar>}
      {Object.values(errors).map((error) => <MessageBar key={error} intent="error" role="alert"><MessageBarBody>{error}</MessageBarBody></MessageBar>)}
      {options.supports_overrides && <div className={styles.controls}>
        {options.per_dataset_limit && <DatasetLimitField label="Max per dataset" value={request.max_per_dataset}
          effective={effective.max_per_dataset} disabled={disabled}
          onError={(error) => setError('perDataset', error)}
          onChange={(value) => updateGlobal('max_per_dataset', value)} />}
        {options.total_limit && <DatasetLimitField label="Max total" value={request.max_total}
          effective={effective.max_total} disabled={disabled}
          onError={(error) => setError('total', error)}
          onChange={(value) => updateGlobal('max_total', value)} />}
        <Field label="Default dataset fetch">
          <Select aria-label="Default dataset fetch" disabled={disabled} value={request.fetch ?? 'default'}
            onChange={(_, data) => {
              if (data.value === 'default' || data.value === 'never' || data.value === 'if_missing') {
                const edits = { ...request }
                if (data.value === 'default') delete edits.fetch
                else edits.fetch = data.value
                onChange(edits)
              }
            }}>
            <option value="default">Scenario default ({defaults.fetch})</option>
            <option value="never">Never fetch</option><option value="if_missing">Fetch if missing</option>
          </Select>
        </Field>
      </div>}
      <div className={styles.scroll}>
        <table className={styles.table} aria-label="Selected datasets">
          <thead><tr>{['Name', 'Stored origins', 'Stored examples', 'Limit', 'Fetch policy', 'Selection'].map((label) =>
            <th className={styles.cell} key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{selected.map((source) => {
            const metadata = datasets.find((dataset) => dataset.name === source.name)
            const settings = effective.sources.find((item) => item.name === source.name)
              ?? defaults.sources.find((item) => item.name === source.name)
            const hasProvider = metadata?.provider_available || settings?.configured_provider
            return <tr key={source.name}>
              <th scope="row" className={styles.cell}>{source.name}</th>
              <td className={styles.cell}>{metadata?.origins?.join(', ') || 'Unknown'}
                <div>{hasProvider ? 'Provider available' : metadata ? 'Memory only' : 'Availability unknown'}</div></td>
              <td className={styles.cell}>{metadata?.logical_examples ?? 'Unknown'}</td>
              <td className={styles.cell}>{canEdit && options.per_dataset_limit
                ? <DatasetLimitField label={`${source.name} limit`} value={source.max_size}
                  effective={settings?.max_size ?? effective.max_per_dataset} disabled={disabled}
                  onError={(error) => setError(source.name, error)}
                  onChange={(value) => updateSource(source.name, { max_size: value })} />
                : settings?.max_size ?? 'all'}</td>
              <td className={styles.cell}>{canEdit
                ? <Select aria-label={`${source.name} fetch`} disabled={disabled} value={source.fetch ?? 'default'}
                  onChange={(_, data) => {
                    if (data.value === 'default' || data.value === 'never' || data.value === 'if_missing') {
                      updateSource(source.name, { fetch: data.value })
                    }
                  }}>
                  <option value="default">Scenario default ({settings?.fetch ?? effective.fetch})</option>
                  <option value="never">Never fetch</option><option value="if_missing">Fetch if missing</option>
                </Select> : settings?.fetch ?? effective.fetch}</td>
              <td className={styles.cell}>{canSelect && <Button className={styles.button}
                aria-label={`Remove ${source.name}`} disabled={disabled || selected.length <= 1}
                onClick={() => onChange({ ...request, sources: selected.filter((item) => item.name !== source.name) })}>
                Remove
              </Button>}</td>
            </tr>
          })}</tbody>
        </table>
      </div>
      {canSelect && <div className={styles.controls}>
        <Field label="Add dataset">
          <Combobox aria-label="Add dataset" freeform={options.mode === 'any'} value={newName} disabled={disabled}
            onChange={(event) => setNewName(event.target.value)}
            onOptionSelect={(_, data) => setNewName(data.optionValue ?? '')}>
            {candidates.map((dataset) => <Option key={dataset.name} value={dataset.name}>{dataset.name}</Option>)}
          </Combobox>
        </Field>
        <Button className={styles.button} disabled={disabled || !newName.trim()}
          onClick={() => {
            const name = newName.trim()
            if (selected.some((source) => source.name === name)) { setError('selection', 'Duplicate dataset selection.'); return }
            if (options.mode === 'subset' && !options.allowed_names.includes(name)) { setError('selection', 'Dataset is not allowed.'); return }
            if (options.max_selected && options.max_selected > 1 && selected.length >= options.max_selected) { setError('selection', `Select at most ${options.max_selected} dataset(s).`); return }
            setError('selection', null)
            onChange({ ...request, sources: options.max_selected === 1 ? [{ name }] : [...selected, { name }] })
            setNewName('')
          }}>Add source</Button>
        <Button className={styles.button} disabled={disabled} onClick={() => {
          const { sources, ...edits } = request
          void sources
          onChange(edits)
          setErrors({})
          onValidationChange(null)
        }}>Use default sources</Button>
      </div>}
    </section>
  )
}
