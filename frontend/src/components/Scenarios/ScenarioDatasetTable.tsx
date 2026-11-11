import { useEffect, useState } from 'react'

import {
  Badge, Button, Combobox, Divider, MessageBar, MessageBarBody, Option, Spinner,
  Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, ToggleButton, Tooltip,
} from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import { MODALITY_LABELS } from '@/components/Config/targetFilters'
import HelpField from '@/components/HelpField'
import type {
  DatasetInfo, ScenarioDatasetLimit, ScenarioDatasetOptions, ScenarioDatasetRequest,
  ScenarioDatasetSettings, ScenarioDatasetSourceInput,
} from '@/types'

import DatasetLimitControl from './DatasetLimitControl'
import { datasetChoices, datasetFacts, datasetNameWithOrigin, datasetPolicies, datasetSize, datasetTotalLimit, parseCaseLimit } from './datasetPresentation'
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
  preparing?: boolean
  loadError?: string | null
  /** Choices are already certified by read-only scenario discovery. */
  compatibleOnly?: boolean
  /** Marks the picker required until the first dataset is chosen. */
  required?: boolean
  showCatalogLink?: boolean
  automaticLoading?: boolean
  onRetryPreparation?: () => void
  onReload: () => void
  onChange: (request: ScenarioDatasetRequest) => void
  onValidationChange: (error: string | null) => void
}

export default function ScenarioDatasetTable({
  request, defaults, effective, options, datasets, disabled, loading, metadataError,
  preparing = false, loadError, automaticLoading = false, onRetryPreparation, compatibleOnly = false,
  required = false, showCatalogLink = true,
  onReload, onChange, onValidationChange,
}: ScenarioDatasetTableProps) {
  const styles = useScenarioDatasetTableStyles()
  const [search, setSearch] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [emptySelections, setEmptySelections] = useState<string[]>([])
  const selected: ScenarioDatasetSourceInput[] = request.sources ?? defaults.sources.map((source) => ({ name: source.name }))
  const canSelect = options.supports_overrides && options.mode !== 'fixed'
  const canEditSources = options.supports_overrides && options.source_settings !== false
  const canLimit = canEditSources && options.per_dataset_limit
  const canFilter = options.supports_overrides && options.filters
  const canAdd = options.max_selected == null || selected.length < options.max_selected
  const replaceSingleSource = options.max_selected === 1
  const busy = disabled
  const total = datasetTotalLimit(request, defaults)
  const filters = { ...defaults.filters, ...request.filters }
  const candidates = datasetChoices(datasets, compatibleOnly ? [] : defaults.sources, options)
    .filter((name) => !selected.some((source) => source.name === name) && name.toLowerCase().includes(search.toLowerCase()))
  const noCompatibleDatasets = compatibleOnly && datasets.length === 0 && selected.length === 0
    && !loading && !metadataError
  const { loaded } = datasetFacts(datasets, selected.map((source) => source.name))
  const availableMetadata: Record<string, string[]> = {
    harm_categories: [...new Set(loaded.flatMap((dataset) => dataset.harm_categories))],
    data_types: [...new Set(loaded.flatMap((dataset) => dataset.modalities))],
  }
  const selectedMetadata = (key: string): string[] => emptySelections.includes(key)
    ? [] : filters[key]?.length ? filters[key] : availableMetadata[key] ?? []
  const metadataSelected = (key: string, value: string): boolean => selectedMetadata(key).includes(value)
  const settingsFor = (name: string) => effective.sources.find((source) => source.name === name)
    ?? defaults.sources.find((source) => source.name === name)
  const requiresImport = !loading && !metadataError && selected.some((source) => {
    const metadata = datasets.find((item) => item.name === source.name)
    return !metadata?.loaded && !metadata?.provider_available && !settingsFor(source.name)?.configured_provider
  })
  const updateDraft = (key: string, text: string | null): void => {
    setDrafts((previous) => {
      const next = { ...previous }
      if (text === null) delete next[key]
      else next[key] = text
      return next
    })
  }
  const clearSourceDrafts = (name?: string): void => {
    setDrafts((previous) => Object.fromEntries(Object.entries(previous).filter(([key]) =>
      name === undefined ? !key.startsWith('source:') : key !== `source:${name}`,
    )))
  }
  const updateTotal = (value: ScenarioDatasetLimit): void => {
    const edits = { ...request }
    if (value === 'default') delete edits.max_total
    else edits.max_total = value
    onChange(edits)
  }
  const toggleFilter = (key: string, value: string): void => {
    const current = selectedMetadata(key)
    const next = current.includes(value) ? current.filter((item) => item !== value) : [...current, value]
    if (next.length === 0) {
      setEmptySelections((previous) => [...new Set([...previous, key])])
      return
    }
    setEmptySelections((previous) => previous.filter((item) => item !== key))
    const edits = { ...request.filters }
    const allSelected = (availableMetadata[key] ?? []).every((item) => next.includes(item))
      && next.every((item) => (availableMetadata[key] ?? []).includes(item))
    if (allSelected && !(defaults.filters[key]?.length)) delete edits[key]
    else edits[key] = allSelected ? [] : next
    const result = { ...request }
    if (Object.keys(edits).length > 0) result.filters = edits
    else delete result.filters
    onChange(result)
  }
  const labels: Record<string, string> = {}
  if (options.supports_overrides && options.total_limit) labels.total = 'Maximum objectives'
  if (canLimit) for (const source of selected) labels[`source:${source.name}`] = `${source.name} objective limit`
  const errors = Object.entries(labels)
    .filter(([key]) => drafts[key] !== undefined && parseCaseLimit(drafts[key]) === null)
    .map(([, label]) => `${label} must be a positive integer.`)
  if (canFilter) for (const key of emptySelections) {
    if (availableMetadata[key]?.length) errors.push(`Select at least one ${key === 'harm_categories' ? 'harm category' : 'data type'}.`)
  }
  if (options.mode === 'subset') {
    const invalid = selected.filter((source) => !options.allowed_names.includes(source.name))
    if (invalid.length > 0) errors.push(`Select a permitted source: ${invalid.map((source) => source.name).join(', ')}`)
  }
  const validationError = errors[0] ?? null
  useEffect(() => { onValidationChange(validationError) }, [validationError, onValidationChange])

  const policies = datasetPolicies(request, defaults, effective, options).map((source) => source.fetch)
  const savedPolicies = [...new Set(policies)].join(', ')
  const picker = <Combobox aria-label={replaceSingleSource ? 'Choose dataset' : 'Add dataset'}
    placeholder={replaceSingleSource ? 'Choose dataset' : 'Add dataset'} value={search} selectedOptions={[]} disabled={busy}
    onOpenChange={(_, data) => { if (data.open && !compatibleOnly) onReload() }}
    onChange={(event) => setSearch(event.target.value)}
    onOptionSelect={(_, data) => {
      const name = data.optionValue
      if (!name || !candidates.includes(name)) return
      if (replaceSingleSource) clearSourceDrafts()
      onChange({ ...request, sources: replaceSingleSource ? [{ name }] : [...selected, { name }] })
      setSearch('')
    }}>
    {candidates.map((name) => {
      const label = datasetNameWithOrigin(name, datasets.find((dataset) => dataset.name === name))
      return <Option key={name} value={name} text={label} aria-label={label}>{label}</Option>
    })}
  </Combobox>

  return (
    <div className={styles.root}>
      {compatibleOnly && loading && <Spinner size="tiny" label="Loading compatible datasets..." />}
      {selected.length > 0 && (loading || preparing) && <Text role="status" size={200} className={styles.loadingHint}>
        {preparing ? 'Loading selected datasets in the background. You can launch the scan now.' : 'Checking dataset details...'}
      </Text>}
      {selected.length > 0 && <div className={styles.toolbar}>
        {requiresImport && <Text size={200}>Import the missing datasets into PyRIT first.</Text>}
        {loadError && onRetryPreparation && <Button className={styles.button} disabled={busy || preparing}
          onClick={onRetryPreparation}>Retry loading datasets</Button>}
        {metadataError && <Button className={styles.button} disabled={busy} onClick={onReload}>Retry checking datasets</Button>}
      </div>}
      {compatibleOnly && metadataError && <Button className={styles.button} disabled={disabled}
        onClick={onReload}>Retry loading compatible datasets</Button>}
      {[metadataError, loadError, ...errors].filter((error): error is string => Boolean(error))
        .map((error) => <MessageBar key={error} intent="error"><MessageBarBody role="alert">{error}</MessageBarBody></MessageBar>)}
      {noCompatibleDatasets ? <Text>No compatible datasets available.</Text>
        : selected.length === 0 ? (!compatibleOnly || !loading) && <Text>
          {canSelect ? 'No dataset selected.' : 'This scenario has no named datasets.'}</Text>
        : <div className={styles.scroll}>
        <Table className={styles.table} aria-label="Selected datasets">
          <TableHeader><TableRow>
            <TableHeaderCell>Dataset</TableHeaderCell>
            <TableHeaderCell>Size</TableHeaderCell>
            <TableHeaderCell>Include Criteria</TableHeaderCell>
            <TableHeaderCell className={styles.actionsCell} aria-label="Remove dataset" />
          </TableRow></TableHeader>
          <TableBody>{selected.map((source) => {
            const metadata = datasets.find((item) => item.name === source.name)
            const sourceDefault = defaults.sources.find((item) => item.name === source.name)?.max_size ?? defaults.max_per_dataset
            const inherited = request.max_per_dataset != null && request.max_per_dataset !== 'default'
              ? settingsFor(source.name)?.max_size ?? sourceDefault : sourceDefault
            const cap = typeof source.max_size === 'number' || source.max_size === 'all' ? source.max_size : inherited
            const displayedCap = !canLimit || total === 'all' ? cap : cap === 'all' ? total : Math.min(cap, total)
            const size = datasetSize(metadata, canLimit ? displayedCap : 'all')
            return <TableRow key={source.name}>
              <TableCell><Text className={styles.datasetName} weight="semibold">
                {datasetNameWithOrigin(source.name, metadata)}
              </Text></TableCell>
              <TableCell>{canLimit ? <DatasetLimitControl label={`${source.name} objective limit`}
                showLabel={false} text={size} value={source.max_size} defaultValue={inherited}
                draft={drafts[`source:${source.name}`]} disabled={busy}
                hint={total === 'all' ? undefined : `Maximum objectives also limits the combined selection to ${total}.`}
                onDraftChange={(text) => updateDraft(`source:${source.name}`, text)}
                onChange={(value) => onChange({ ...request, sources: selected.map((item) =>
                  item.name === source.name ? { ...item, max_size: value } : item) })} /> : <Text>{size}</Text>}</TableCell>
              <TableCell>{metadata?.loaded ? <div className={styles.metadata}>
                {metadata.harm_categories.map((category) => canFilter
                  ? <ToggleButton key={`harm:${category}`} className={styles.chip} appearance="transparent" disabled={busy}
                    checked={metadataSelected('harm_categories', category)} aria-label={`Filter harm category ${category}`}
                    onClick={() => toggleFilter('harm_categories', category)}>
                    <Badge color="brand" appearance={metadataSelected('harm_categories', category) ? 'filled' : 'outline'}>{category}</Badge>
                  </ToggleButton>
                  : <Badge key={`harm:${category}`} color="brand" appearance="outline">{category}</Badge>)}
                {metadata.modalities.map((type) => canFilter
                  ? <ToggleButton key={`type:${type}`} className={styles.chip} appearance="transparent" disabled={busy}
                    checked={metadataSelected('data_types', type)} aria-label={`Filter data type ${MODALITY_LABELS[type] ?? type}`}
                    onClick={() => toggleFilter('data_types', type)}>
                    <Badge color="success" appearance={metadataSelected('data_types', type) ? 'filled' : 'outline'}>{MODALITY_LABELS[type] ?? type}</Badge>
                  </ToggleButton>
                  : <Badge key={`type:${type}`} color="success" appearance="outline">{MODALITY_LABELS[type] ?? type}</Badge>)}
                {metadata.has_unlabeled_harm_categories && <Tooltip content="Some examples have no harm category." relationship="description">
                  <Badge appearance="outline" color="subtle">Unlabeled</Badge>
                </Tooltip>}
                {metadata.harm_categories.length === 0 && metadata.modalities.length === 0 && <Text size={200}>No metadata labels.</Text>}
              </div> : <Text size={200}>
                {loading || preparing ? 'Loading...' : metadataError ? 'Details unavailable' : 'Not stored in memory'}
              </Text>}</TableCell>
              <TableCell className={styles.actionsCell}>
                {canSelect && (compatibleOnly || selected.length > 1) && <Button appearance="subtle" className={styles.button}
                  icon={<DismissRegular />} aria-label={`Remove ${source.name}`} disabled={busy}
                  onClick={() => {
                    clearSourceDrafts(source.name)
                    onChange({ ...request, sources: selected.filter((item) => item.name !== source.name) })
                  }} />}
              </TableCell>
            </TableRow>
          })}</TableBody>
        </Table>
      </div>}
      {!noCompatibleDatasets && (selected.length > 0 || canSelect) && <div className={styles.controls}>
        {canSelect && <>
        {(canAdd || replaceSingleSource) && (required ? <HelpField
          label={replaceSingleSource ? 'Choose dataset' : 'Add dataset'} required={selected.length === 0}>
          {picker}
        </HelpField> : picker)}
        {!compatibleOnly && <Button className={styles.button} disabled={busy} onClick={() => {
          const edits = { ...request }
          delete edits.sources
          delete edits.max_per_dataset
          clearSourceDrafts()
          setSearch('')
          onChange(edits)
        }}>Reset datasets</Button>}
        </>}
        {showCatalogLink && <Link className={styles.browseLink} to="/datasets">Browse datasets</Link>}
      </div>}
      {(selected.length > 0 || canSelect) && <Divider />}
      {canFilter && emptySelections.length > 0 && <Button className={styles.button} disabled={busy}
        onClick={() => setEmptySelections([])}>Restore metadata selection</Button>}
      {selected.length > 0 && <Text size={200} className={styles.loadingHint}>
        {automaticLoading
          ? 'This scan loads only missing selected datasets. Stored contents are kept, including generated datasets.'
          : `Saved loading policies: ${savedPolicies}. Editing this preset does not load datasets or change these policies.`}
        {automaticLoading && policies.some((policy) => policy !== 'if_missing')
          && ' Saved never/always policies are normalized to missing-only for this scan when overrides are permitted; protected policies require the SDK/API. The saved configuration is unchanged.'}
      </Text>}
      {selected.length > 0 && options.supports_overrides && options.total_limit && <Divider />}
      {options.supports_overrides && options.total_limit && <DatasetLimitControl label="Maximum objectives"
        value={request.max_total} defaultValue={defaults.max_total} draft={drafts.total} disabled={busy}
        onDraftChange={(text) => updateDraft('total', text)} onChange={updateTotal} />}
      {options.total_limit && <Text size={200}>Each objective can run through several techniques</Text>}
    </div>
  )
}
