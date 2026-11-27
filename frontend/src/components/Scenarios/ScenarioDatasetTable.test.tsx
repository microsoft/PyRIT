import { useState } from 'react'

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import type { DatasetInfo, ScenarioDatasetOptions, ScenarioDatasetRequest, ScenarioDatasetSettings } from '@/types'

import ScenarioDatasetTable from './ScenarioDatasetTable'

const DEFAULTS: ScenarioDatasetSettings = {
  sources: [{ name: 'stored', max_size: 2, fetch: 'never', configured_provider: false }],
  max_per_dataset: 5, max_total: 4, fetch: 'if_missing', filters: {},
}
const OPTIONS: ScenarioDatasetOptions = {
  mode: 'any', allowed_names: [], per_dataset_limit: true, total_limit: true, supports_overrides: true, filters: true,
}
const STORED: DatasetInfo = {
  name: 'stored', selection_key: 'dataset:named:stored', loaded: true, provider_available: false,
  origins: ['local'], logical_examples: 8, seed_pieces: 8, objectives: 8,
  modalities: ['text'], harm_categories: ['hate', 'violence'], has_unlabeled_harm_categories: false,
}
const REMOTE: DatasetInfo = {
  ...STORED, name: 'remote', selection_key: 'dataset:named:remote', loaded: false,
  provider_available: true, logical_examples: null, origins: [], modalities: [], harm_categories: [],
}
const DATASETS = [STORED, REMOTE, { ...STORED, name: 'other', selection_key: 'dataset:named:other' }]

interface HarnessProps {
  options?: ScenarioDatasetOptions
  defaults?: ScenarioDatasetSettings
  datasets?: DatasetInfo[]
  initialRequest?: ScenarioDatasetRequest
  onChange?: jest.Mock
  loading?: boolean
  loadingNames?: string[]
  loadErrors?: Record<string, string>
  onRetryLoad?: jest.Mock
  compatibleOnly?: boolean
  metadataError?: string | null
  onReload?: jest.Mock
  required?: boolean
  selectionError?: string | null
}
function Harness({
  options = OPTIONS, defaults = DEFAULTS, datasets = DATASETS, initialRequest = {}, onChange = jest.fn(),
  loading = false, loadingNames = [], loadErrors = {}, onRetryLoad = jest.fn(), compatibleOnly = false,
  metadataError = null, onReload = jest.fn(), required = false, selectionError = null,
}: HarnessProps) {
  const [request, setRequest] = useState<ScenarioDatasetRequest>(initialRequest)
  const [validation, setValidation] = useState<string | null>(null)
  const effective: ScenarioDatasetSettings = {
    ...defaults,
    sources: (request.sources ?? defaults.sources).map((source) => ({
      name: source.name,
      max_size: typeof source.max_size === 'number' || source.max_size === 'all'
        ? source.max_size : defaults.sources.find((item) => item.name === source.name)?.max_size ?? defaults.max_per_dataset,
      fetch: source.fetch === 'never' || source.fetch === 'if_missing' || source.fetch === 'always' ? source.fetch
        : defaults.sources.find((item) => item.name === source.name)?.fetch ?? defaults.fetch,
      configured_provider: defaults.sources.find((item) => item.name === source.name)?.configured_provider ?? false,
    })),
  }
  return <FluentProvider theme={webLightTheme}><MemoryRouter>
    <ScenarioDatasetTable request={request} defaults={defaults} effective={effective} options={options}
      datasets={datasets} disabled={false} loading={loading} metadataError={metadataError}
      loadingNames={loadingNames} loadErrors={loadErrors} onRetryLoad={onRetryLoad}
      compatibleOnly={compatibleOnly} required={required} selectionError={selectionError}
      onReload={onReload} onValidationChange={setValidation}
      onChange={(next) => { setRequest(next); onChange(next) }} />
    <button disabled={validation !== null}>Launch</button>
  </MemoryRouter></FluentProvider>
}

async function editLimit(user: ReturnType<typeof userEvent.setup>, label: string, value: string): Promise<void> {
  await user.click(screen.getByRole('button', { name: `Edit ${label}` }))
  const all = screen.getByRole('checkbox', { name: `All available for ${label}` })
  if (all instanceof HTMLInputElement && all.checked) await user.click(all)
  const input = screen.getByRole('textbox', { name: label })
  await user.clear(input)
  await user.type(input, value)
}

async function addDataset(user: ReturnType<typeof userEvent.setup>, name: string): Promise<void> {
  await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
  const dataset = DATASETS.find((item) => item.name === name)
  const origin = dataset?.origins?.length ? 'Local files' : 'Unknown'
  await user.click(screen.getByRole('option', { name: `${name} (${origin})`, exact: true }))
}

describe('ScenarioDatasetTable', () => {
  it('keeps ASSERT choices compatible-only with one metadata load and explicit failure Retry', async () => {
    const user = userEvent.setup()
    const reload = jest.fn()
    const view = render(<Harness compatibleOnly defaults={{ ...DEFAULTS, sources: [] }}
      datasets={[STORED]} onReload={reload} />)
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    await user.click(screen.getByRole('option', { name: 'stored (Local files)', exact: true }))
    expect(reload).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Reset datasets', exact: true })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load datasets', exact: true })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove stored' })).toBeInTheDocument()
    // Compatible choices are already stored, so there is no loading rule to pick.
    expect(screen.queryByRole('radiogroup', { name: 'Dataset loading' })).not.toBeInTheDocument()
    // Without the loading rule, one divider separates the selection from the total limit.
    expect(screen.getAllByRole('separator')).toHaveLength(1)
    view.rerender(<Harness compatibleOnly defaults={{ ...DEFAULTS, sources: [] }} datasets={[STORED]}
      onReload={reload} metadataError="Compatible listing unavailable" />)
    // Retry sits in the error it fixes, and the picker waits for a listing.
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Compatible listing unavailable')
    const retry = within(alert.parentElement as HTMLElement).getByRole('button', { name: 'Retry loading compatible datasets' })
    expect(retry).toHaveTextContent('Retry')
    expect(screen.getByRole('combobox', { name: 'Add dataset' })).toBeDisabled()
    await user.click(retry)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('keeps the required picker and its validation when no compatible dataset exists', () => {
    render(<Harness compatibleOnly required selectionError="Select a dataset." defaults={{ ...DEFAULTS, sources: [] }}
      datasets={[]} />)
    const picker = screen.getByRole('combobox', { name: 'Add dataset' })
    expect(picker).toBeRequired()
    expect(picker).toHaveAccessibleDescription(expect.stringContaining('Select a dataset.'))
    expect(screen.getByText('No compatible datasets available.')).toBeInTheDocument()
  })

  it('shows one loading status for a linked selection and names a selection the listing omits', () => {
    const linked = { sources: [{ name: 'linked' }] }
    const view = render(<Harness compatibleOnly loading defaults={{ ...DEFAULTS, sources: [] }} datasets={[]}
      initialRequest={linked} />)
    expect(screen.getByRole('progressbar', { name: 'Loading compatible datasets...' })).toBeInTheDocument()
    expect(screen.queryByText('Checking dataset details...')).not.toBeInTheDocument()
    expect(screen.queryByText('Not loaded')).not.toBeInTheDocument()
    view.rerender(<Harness compatibleOnly defaults={{ ...DEFAULTS, sources: [] }} datasets={[STORED]}
      initialRequest={linked} />)
    expect(screen.getByText('Not in the compatible list')).toBeInTheDocument()
    expect(screen.queryByText('Not stored in memory')).not.toBeInTheDocument()
  })

  it('preserves generic default fallback, final-source guard, Reset and metadata refresh', async () => {
    const user = userEvent.setup()
    const reload = jest.fn()
    render(<Harness datasets={[REMOTE]} onReload={reload} />)
    expect(screen.queryByRole('button', { name: 'Remove stored' })).not.toBeInTheDocument()
    // Reset appears once the sources are edited.
    expect(screen.queryByRole('button', { name: 'Reset datasets', exact: true })).not.toBeInTheDocument()
    // Nothing selected has a provider to refresh from until remote is added.
    expect(screen.queryByRole('radiogroup', { name: 'Dataset loading' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    expect(reload).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('option', { name: 'remote (Unknown)', exact: true }))
    expect(screen.getByRole('button', { name: 'Reset datasets', exact: true })).toBeEnabled()
    expect(screen.getByRole('radiogroup', { name: 'Dataset loading' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove stored' }))
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    expect(screen.getByRole('option', { name: 'stored (Unknown)', exact: true })).toBeInTheDocument()
  })

  it('shows a truthful compatible empty state and no choices until the first response', () => {
    const view = render(<Harness compatibleOnly defaults={{ ...DEFAULTS, sources: [] }} datasets={[]} loading />)
    expect(screen.getByRole('progressbar', { name: 'Loading compatible datasets...' })).toBeInTheDocument()
    expect(screen.queryByText('No compatible datasets available.')).not.toBeInTheDocument()
    view.rerender(<Harness compatibleOnly defaults={{ ...DEFAULTS, sources: [] }} datasets={[]} />)
    expect(screen.getByText('No compatible datasets available.')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Add dataset' })).not.toBeInTheDocument()
  })

  it('shows a compact table with resolved sizes and metadata, without a loading banner for loaded data', () => {
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    expect(screen.getByRole('columnheader', { name: 'Dataset', exact: true })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Size' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Include Criteria' })).toBeInTheDocument()
    // A stored dataset with no provider has nothing to regenerate, so there is no loading choice or its divider.
    expect(screen.queryByRole('radiogroup', { name: 'Dataset loading' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('separator')).toHaveLength(1)
    expect(screen.queryByText(/All selected uses the defaults/)).not.toBeInTheDocument()
    expect(screen.getByText('Maximum objectives:', { exact: true })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit stored objective limit' })).toHaveTextContent('Up to 2 of 8')
    expect(screen.getByRole('button', { name: 'Filter harm category hate' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Filter data type Text' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    expect(screen.queryByText('All selected datasets are loaded.')).not.toBeInTheDocument()
    expect(screen.queryByText('Details and advanced settings')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit Maximum objectives' })).toHaveTextContent('Up to 4')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('adds a provider immediately from a name-only dropdown without a Load button', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    await addDataset(user, 'remote')
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ sources: [{ name: 'stored' }, { name: 'remote' }] })
    expect(screen.getByRole('button', { name: 'Edit remote objective limit' })).toHaveTextContent('Up to 4')
    expect(screen.getByText('Not stored in memory')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Load datasets/ })).not.toBeInTheDocument()
  })

  it('keeps required reference sources complete and does not expose source edits', () => {
    render(<Harness options={{ ...OPTIONS, mode: 'fixed', per_dataset_limit: false }} />)
    expect(screen.getByText('8 examples')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit stored objective limit' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Add dataset' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove stored' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit Maximum objectives' })).toBeInTheDocument()
  })

  it('supports inline numeric, all, and inherited objective limits', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    await editLimit(user, 'Maximum objectives', '12')
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ max_total: 12 })
    await user.click(screen.getByRole('checkbox', { name: 'All available for Maximum objectives' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ max_total: 'all' })
    await user.click(screen.getByRole('button', { name: 'Reset to 4' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({})
  })

  it('keeps invalid global drafts blocking when datasets reset', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await addDataset(user, 'remote')
    await editLimit(user, 'Maximum objectives', '0')
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'Reset datasets' }))
    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('Maximum objectives must be a positive integer')
    await user.click(screen.getByRole('button', { name: 'Edit Maximum objectives' }))
    expect(screen.getByRole('textbox', { name: 'Maximum objectives' })).toHaveValue('0')
    await user.click(screen.getByRole('button', { name: 'Reset to 4' }))
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled()
  })

  it('removes only the invalid row draft and restores other rows and limits on reset', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    await addDataset(user, 'other')
    await editLimit(user, 'other objective limit', '0')
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Remove other' }))
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled()
    expect(onChange.mock.calls.at(-1)?.[0].sources).toEqual([{ name: 'stored' }])
    expect(screen.queryByRole('button', { name: 'Remove stored' })).not.toBeInTheDocument()
    await editLimit(user, 'stored objective limit', '3')
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'Reset datasets' }))
    expect(screen.getByRole('button', { name: 'Edit stored objective limit' })).toHaveTextContent('Up to 2 of 8')
  })

  it('replaces a one-source subset without permitting invalid or additional sources', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness options={{ ...OPTIONS, mode: 'subset', allowed_names: ['stored', 'remote'], max_selected: 1 }} onChange={onChange} />)
    await user.click(screen.getByRole('combobox', { name: 'Choose dataset' }))
    expect(screen.queryByRole('option', { name: 'other', exact: true })).not.toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'remote (Unknown)', exact: true }))
    expect(screen.getByRole('button', { name: 'Reset datasets', exact: true })).toBeEnabled()
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ sources: [{ name: 'remote' }] })
  })

  it('shares category and type filters across rows and preserves inherited filters when deselected', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness defaults={{ ...DEFAULTS, filters: { harm_categories: ['hate'], data_types: ['text'] } }} onChange={onChange} />)
    await addDataset(user, 'other')
    const hate = screen.getAllByRole('button', { name: 'Filter harm category hate' })
    expect(hate.every((button) => button.getAttribute('aria-pressed') === 'true')).toBe(true)
    await user.click(screen.getAllByRole('button', { name: 'Filter harm category violence' })[0])
    await user.click(hate[0])
    expect(onChange.mock.calls.at(-1)?.[0].filters).toEqual({ harm_categories: ['violence'] })
    expect(hate.every((button) => button.getAttribute('aria-pressed') === 'false')).toBe(true)
    await user.click(hate[0])
    expect(onChange.mock.calls.at(-1)?.[0].filters.harm_categories).toEqual([])
    expect(screen.queryByText('Filters:')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove type filter text' })).not.toBeInTheDocument()
  })

  it('preserves custom inherited filters without rendering a separate filter list', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness defaults={{ ...DEFAULTS, filters: { harm_categories: ['custom-substring'] } }} onChange={onChange} />)
    await user.click(screen.getByRole('button', { name: 'Filter harm category hate' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ filters: { harm_categories: ['custom-substring', 'hate'] } })
    expect(screen.queryByText('Filters:')).not.toBeInTheDocument()
  })

  it('starts with every label checked and restores the default request after reselecting all labels', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    expect(screen.getByRole('button', { name: 'Filter data type Text' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Filter harm category hate' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ filters: { harm_categories: ['violence'] } })
    await user.click(screen.getByRole('button', { name: 'Filter harm category hate' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({})
  })

  it('offers reset only for edited sources or limits', async () => {
    const user = userEvent.setup()
    const view = render(<Harness />)
    expect(screen.queryByRole('button', { name: 'Reset datasets' })).not.toBeInTheDocument()
    await addDataset(user, 'remote')
    expect(screen.getByRole('button', { name: 'Reset datasets' })).toBeInTheDocument()
    view.unmount()

    render(<Harness initialRequest={{ max_per_dataset: 1 }} />)
    expect(screen.getByRole('button', { name: 'Reset datasets' })).toBeInTheDocument()
  })

  it('does not offer reset for a saved request that leaves sources and per-dataset limits unset', () => {
    // Saved presets and runs send every dataset field, with null or 'default' for the unset ones.
    render(<Harness initialRequest={{ sources: null, max_per_dataset: 'default', max_total: 5, fetch: null, filters: null }} />)
    expect(screen.getByText('stored (Local files)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reset datasets' })).not.toBeInTheDocument()
  })

  it('blocks an empty selection instead of sending an empty list that means include all', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    await user.click(screen.getByRole('button', { name: 'Filter data type Text' }))
    expect(screen.getByRole('button', { name: 'Filter data type Text' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('Select at least one data type.')
    expect(onChange).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Filter data type Text' }))
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled()
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({})
  })

  it('uses a two-stop pill track that writes only the scan-level policy', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    await addDataset(user, 'remote')
    const group = screen.getByRole('radiogroup', { name: 'Dataset loading' })
    expect(within(group).getAllByRole('radio').map((radio) => radio.getAttribute('value'))).toEqual(['if_missing', 'always'])
    await user.click(within(group).getByRole('radio', { name: 'Regenerate' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ fetch: 'always', sources: [{ name: 'stored' }, { name: 'remote' }] })
    await user.click(within(group).getByRole('radio', { name: 'Cached' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ fetch: 'if_missing', sources: [{ name: 'stored' }, { name: 'remote' }] })
  })

  it('shows Cached by default and allows Regenerate after data is loaded', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const defaults = { ...DEFAULTS, sources: [{ name: 'remote', max_size: 4 as const, fetch: 'if_missing' as const, configured_provider: false }] }
    const view = render(<Harness defaults={defaults} onChange={onChange} />)
    expect(screen.getByRole('radio', { name: 'Cached' })).toBeChecked()
    view.rerender(<Harness defaults={defaults} onChange={onChange} datasets={[{ ...REMOTE, loaded: true, logical_examples: 100 }]} />)
    await user.click(screen.getByRole('radio', { name: 'Regenerate' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ fetch: 'always' })
    expect(screen.queryByText('Uses stored copy')).not.toBeInTheDocument()
  })

  it.each<[ScenarioDatasetRequest, string]>([
    [{}, 'Cached'],
    [{ fetch: 'if_missing' }, 'Cached'],
    [{ fetch: 'never' }, 'Cached'],
    [{ fetch: 'default' }, 'Cached'],
    [{ sources: [{ name: 'remote', fetch: 'always' }] }, 'Cached'],
    [{ fetch: 'always' }, 'Regenerate'],
    [{ fetch: 'always', sources: [{ name: 'remote', fetch: 'never' }] }, 'Regenerate'],
  ])('reads saved settings %j as %s', (initialRequest: ScenarioDatasetRequest, choice: string) => {
    render(<Harness initialRequest={{ sources: [{ name: 'remote' }], ...initialRequest }} />)
    expect(screen.getByRole('radio', { name: choice })).toBeChecked()
  })

  it('shows a scenario default of always as Regenerate until the scan chooses Cached', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness defaults={{ ...DEFAULTS, fetch: 'always' }} onChange={onChange}
      initialRequest={{ sources: [{ name: 'stored' }, { name: 'remote' }] }} />)
    expect(screen.getByRole('radio', { name: 'Regenerate' })).toBeChecked()
    expect(within(screen.getByRole('row', { name: /stored \(Local files\)/ })).getByText('Uses stored copy')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Cached' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ fetch: 'if_missing', sources: [{ name: 'stored' }, { name: 'remote' }] })
    expect(screen.getByRole('radio', { name: 'Cached' })).toBeChecked()
  })

  it('offers the loading choice only when a selected dataset can refresh', () => {
    const view = render(<Harness initialRequest={{ sources: [{ name: 'stored' }, { name: 'other' }] }} />)
    expect(screen.queryByRole('radiogroup', { name: 'Dataset loading' })).not.toBeInTheDocument()
    view.unmount()
    // A configured provider counts even when the catalog has no registered one.
    const configured = { ...DEFAULTS, sources: [{ ...DEFAULTS.sources[0], configured_provider: true }] }
    const configuredView = render(<Harness defaults={configured} />)
    expect(screen.getByRole('radiogroup', { name: 'Dataset loading' })).toBeInTheDocument()
    configuredView.unmount()
    render(<Harness datasets={[STORED, REMOTE]} initialRequest={{ sources: [{ name: 'remote' }] }}
      options={{ ...OPTIONS, supports_overrides: false }} />)
    expect(screen.queryByRole('radiogroup', { name: 'Dataset loading' })).not.toBeInTheDocument()
  })

  it('says a stored dataset without a provider keeps its stored copy under Regenerate', async () => {
    const user = userEvent.setup()
    render(<Harness initialRequest={{ sources: [{ name: 'stored' }, { name: 'remote' }] }} />)
    const storedRow = screen.getByRole('row', { name: /stored \(Local files\)/ })
    const remoteRow = screen.getByRole('row', { name: /remote \(Unknown\)/ })
    expect(within(storedRow).queryByText('Uses stored copy')).not.toBeInTheDocument()
    expect(screen.getByText('Uses stored datasets and loads missing ones.')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Regenerate' }))
    expect(within(storedRow).getByText('Uses stored copy')).toBeInTheDocument()
    expect(within(remoteRow).queryByText('Uses stored copy')).not.toBeInTheDocument()
    expect(screen.getByText('Downloads a fresh copy when the scan starts.')).toBeInTheDocument()
  })

  it('does not assume stored counts are exact selected sizes after filters and a total cap', () => {
    render(<Harness initialRequest={{ max_total: 1, filters: { harm_categories: ['hate'] } }} />)
    expect(screen.getByRole('button', { name: 'Edit stored objective limit' })).toHaveTextContent('Up to 1 of 8')
    const row = screen.getByRole('row', { name: /stored/ })
    expect(within(row).queryByText('1 of 8', { exact: true })).not.toBeInTheDocument()
  })

  it('keeps an inline-only scenario from choosing named datasets while its limits stay editable', () => {
    // The backend reports inline seeds as fixed sources and keeps the other overrides.
    render(<Harness defaults={{ ...DEFAULTS, sources: [] }} options={{ ...OPTIONS, mode: 'fixed' }} />)
    expect(screen.getByText('This scenario has no named datasets.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Add dataset' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit Maximum objectives' })).toBeEnabled()
  })

  it('lets a scenario without default datasets choose its first dataset', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness defaults={{ ...DEFAULTS, sources: [] }} onChange={onChange} />)

    expect(screen.queryByText('This scenario has no named datasets.')).not.toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Selected datasets' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reset datasets' })).not.toBeInTheDocument()
    await addDataset(user, 'stored')

    expect(onChange).toHaveBeenLastCalledWith({ sources: [{ name: 'stored' }] })
    const table = screen.getByRole('table', { name: 'Selected datasets' })
    expect(within(table).getByText('stored (Local files)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove stored' })).not.toBeInTheDocument()
  })

  it('offers only permitted datasets when choosing the first one for a subset scenario', async () => {
    const user = userEvent.setup()
    render(<Harness defaults={{ ...DEFAULTS, sources: [] }} options={{ ...OPTIONS, mode: 'subset', allowed_names: ['other'] }} />)

    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(['other (Local files)'])
  })

  it('sets the scan-level policy even when the scenario protects source settings', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness options={{ ...OPTIONS, mode: 'fixed', source_settings: false }} onChange={onChange}
      datasets={[{ ...STORED, loaded: false, provider_available: true }]} />)
    expect(screen.getByRole('radio', { name: 'Cached' })).toBeChecked()
    await user.click(screen.getByRole('radio', { name: 'Regenerate' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ fetch: 'always' })
  })

  it('shows each failed load with a Retry for only the datasets that failed together', async () => {
    const user = userEvent.setup()
    const onRetryLoad = jest.fn()
    render(<Harness onRetryLoad={onRetryLoad}
      datasets={[STORED, REMOTE, { ...REMOTE, name: 'second', selection_key: 'dataset:named:second' }]}
      initialRequest={{ sources: [{ name: 'stored' }, { name: 'remote' }, { name: 'second' }] }}
      loadErrors={{ remote: 'Provider offline.', second: 'Provider offline.', removed: 'Not selected.' }} />)
    const alert = screen.getByRole('alert')
    // The message names the datasets it belongs to, so each Retry is tied to visible names.
    expect(alert).toHaveTextContent(/^Couldn't load remote, second: Provider offline\.$/)
    expect(screen.queryByText(/Not selected\./)).not.toBeInTheDocument()
    await user.click(within(alert.parentElement as HTMLElement).getByRole('button', { name: 'Retry loading remote, second' }))
    expect(onRetryLoad).toHaveBeenCalledWith(['remote', 'second'])
    expect(screen.queryByRole('button', { name: /Load datasets/ })).not.toBeInTheDocument()
  })

  it('drops a failed load once another load has stored that dataset', () => {
    render(<Harness datasets={[STORED, { ...REMOTE, loaded: true }]}
      initialRequest={{ sources: [{ name: 'stored' }, { name: 'remote' }] }}
      loadErrors={{ remote: 'Provider offline.' }} />)
    expect(screen.queryByText('Provider offline.')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry loading/ })).not.toBeInTheDocument()
  })

  it('keeps the import-first message for a missing dataset with no provider', () => {
    render(<Harness initialRequest={{ sources: [{ name: 'stored' }, { name: 'manual' }] }} />)
    expect(screen.getByText('Import the missing datasets into PyRIT first.')).toBeInTheDocument()
  })

  it('keeps metadata refresh status outside responsive table columns and clears it after checking', () => {
    const view = render(<Harness loading />)
    expect(screen.getByRole('status')).toHaveTextContent('Checking dataset details...')
    expect(within(screen.getByRole('table', { name: 'Selected datasets' })).queryByRole('status')).not.toBeInTheDocument()
    view.rerender(<Harness />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it.each([true, false])('announces loading while reading metadata or importing data (metadata: %s)', (loading) => {
    render(<Harness initialRequest={{ sources: [{ name: 'remote' }] }} loading={loading}
      loadingNames={loading ? [] : ['remote']} />)
    expect(screen.getByRole('status')).toHaveTextContent(loading ? 'Checking dataset details...' : 'Loading datasets...')
    expect(screen.queryByText('Not stored in memory')).not.toBeInTheDocument()
  })

  it('does not announce a load for a dataset that is no longer selected', () => {
    render(<Harness loadingNames={['remote']} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('keeps the selection editable while datasets load and marks only the loading rows', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness loadingNames={['remote']} onChange={onChange}
      initialRequest={{ sources: [{ name: 'stored' }, { name: 'remote' }] }} />)
    // The row's load state sits under its name, which stays visible when the other columns scroll off a narrow screen.
    const [nameCell, , criteriaCell] = within(screen.getByRole('row', { name: /remote \(Unknown\)/ })).getAllByRole('cell')
    expect(within(nameCell).getByText('Loading...')).toBeInTheDocument()
    expect(criteriaCell).toBeEmptyDOMElement()
    expect(within(screen.getByRole('row', { name: /stored \(Local files\)/ })).queryByText('Loading...')).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Regenerate' })).toBeEnabled()
    await addDataset(user, 'other')
    await user.click(screen.getByRole('button', { name: 'Remove remote' }))
    expect(onChange).toHaveBeenLastCalledWith({ sources: [{ name: 'stored' }, { name: 'other' }] })
  })

  it('shows recorded origins in both rows and selection without changing source names', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    expect(screen.getByText('stored (Local files)')).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    expect(screen.getByRole('option', { name: 'other (Local files)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'remote (Unknown)' })).toBeInTheDocument()
  })

  it('supports keyboard selection and terse policy descriptions', async () => {
    const user = userEvent.setup()
    render(<Harness initialRequest={{ sources: [{ name: 'remote' }] }} />)
    const group = screen.getByRole('radiogroup', { name: 'Dataset loading' })
    expect(group).toHaveAccessibleDescription('Uses stored datasets and loads missing ones.')
    await user.click(within(group).getByRole('radio', { name: 'Cached' }))
    await user.keyboard('{ArrowRight}')
    expect(within(group).getByRole('radio', { name: 'Regenerate' })).toBeChecked()
    expect(group).toHaveAccessibleDescription('Downloads a fresh copy when the scan starts.')
    await user.keyboard('{ArrowLeft}')
    expect(within(group).getByRole('radio', { name: 'Cached' })).toBeChecked()
  })
})
