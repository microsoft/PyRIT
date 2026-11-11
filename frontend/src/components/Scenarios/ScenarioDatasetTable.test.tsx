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
  onLoad?: jest.Mock
  loading?: boolean
  preparing?: boolean
  compatibleOnly?: boolean
  metadataError?: string | null
  onReload?: jest.Mock
}
function Harness({
  options = OPTIONS, defaults = DEFAULTS, datasets = DATASETS, initialRequest = {}, onChange = jest.fn(), onLoad = jest.fn(),
  loading = false, preparing = false, compatibleOnly = false, metadataError = null, onReload = jest.fn(),
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
      configured_provider: false,
    })),
  }
  return <FluentProvider theme={webLightTheme}><MemoryRouter>
    <ScenarioDatasetTable request={request} defaults={defaults} effective={effective} options={options}
      datasets={datasets} disabled={false} loading={loading} preparing={preparing} metadataError={metadataError}
      compatibleOnly={compatibleOnly}
      automaticLoading onRetryPreparation={onLoad} onReload={onReload} onValidationChange={setValidation}
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
  await user.click(screen.getByRole('option', { name: `${name} (${dataset?.origins?.join(', ') || 'unknown'})`, exact: true }))
}

describe('ScenarioDatasetTable', () => {
  it('keeps ASSERT choices compatible-only with one metadata load and explicit failure Retry', async () => {
    const user = userEvent.setup()
    const reload = jest.fn()
    const load = jest.fn()
    const view = render(<Harness compatibleOnly defaults={{ ...DEFAULTS, sources: [] }}
      datasets={[STORED]} onReload={reload} onLoad={load} />)
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    await user.click(screen.getByRole('option', { name: 'stored (local)', exact: true }))
    expect(reload).not.toHaveBeenCalled()
    expect(load).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Reset datasets', exact: true })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load datasets', exact: true })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove stored' })).toBeInTheDocument()
    view.rerender(<Harness compatibleOnly defaults={{ ...DEFAULTS, sources: [] }} datasets={[STORED]}
      onReload={reload} onLoad={load} metadataError="Compatible listing unavailable" />)
    await user.click(screen.getByRole('button', { name: 'Retry loading compatible datasets' }))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('preserves generic default fallback, final-source guard, Reset and metadata refresh', async () => {
    const user = userEvent.setup()
    const reload = jest.fn()
    const load = jest.fn()
    render(<Harness datasets={[REMOTE]} onReload={reload} onLoad={load} />)
    expect(screen.queryByRole('button', { name: 'Remove stored' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset datasets', exact: true })).toBeEnabled()
    expect(screen.queryByRole('radiogroup', { name: 'Dataset loading' })).not.toBeInTheDocument()
    expect(load).not.toHaveBeenCalled()
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    expect(reload).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('option', { name: 'remote (unknown)', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Remove stored' }))
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    expect(screen.getByRole('option', { name: 'stored (unknown)', exact: true })).toBeInTheDocument()
    expect(load).not.toHaveBeenCalled()
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
    expect(screen.getAllByRole('separator')).toHaveLength(2)
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

  it('adds a provider immediately from a name-only dropdown without fetching it', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const onLoad = jest.fn()
    render(<Harness onChange={onChange} onLoad={onLoad} />)
    await addDataset(user, 'remote')
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ sources: [{ name: 'stored' }, { name: 'remote' }] })
    expect(screen.getByRole('button', { name: 'Edit remote objective limit' })).toHaveTextContent('Up to 4')
    expect(screen.getByText('Not stored in memory')).toBeInTheDocument()
    expect(onLoad).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
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
    await user.click(screen.getByRole('option', { name: 'remote (unknown)', exact: true }))
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

  it('links to the dataset browser next to reset and for required sources', () => {
    const view = render(<Harness />)
    expect(screen.getByRole('link', { name: 'Browse datasets' })).toHaveAttribute('href', '/datasets')
    expect(screen.getByRole('button', { name: 'Reset datasets' })).toBeInTheDocument()
    view.rerender(<Harness options={{ ...OPTIONS, mode: 'fixed' }} />)
    expect(screen.getByRole('link', { name: 'Browse datasets' })).toHaveAttribute('href', '/datasets')
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

  it('replaces loading-policy controls with missing-only loading information', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    await addDataset(user, 'remote')
    expect(screen.queryByRole('radiogroup', { name: 'Dataset loading' })).not.toBeInTheDocument()
    expect(screen.getByText(/This scan loads only missing selected datasets/)).toBeInTheDocument()
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ sources: [{ name: 'stored' }, { name: 'remote' }] })
  })

  it('keeps stored contents and never exposes implicit regeneration', () => {
    const onChange = jest.fn()
    const defaults = { ...DEFAULTS, sources: [{ name: 'remote', max_size: 4 as const, fetch: 'if_missing' as const, configured_provider: false }] }
    const view = render(<Harness defaults={defaults} onChange={onChange} />)
    expect(screen.queryByRole('radio', { name: 'Refresh' })).not.toBeInTheDocument()
    view.rerender(<Harness defaults={defaults} onChange={onChange} datasets={[{ ...REMOTE, loaded: true, logical_examples: 100 }]} />)
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByText(/All selected datasets are loaded/)).not.toBeInTheDocument()
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

    expect(screen.getByText('No dataset selected.')).toBeInTheDocument()
    expect(screen.queryByText('This scenario has no named datasets.')).not.toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Selected datasets' })).not.toBeInTheDocument()
    await addDataset(user, 'stored')

    expect(onChange).toHaveBeenLastCalledWith({ sources: [{ name: 'stored' }] })
    const table = screen.getByRole('table', { name: 'Selected datasets' })
    expect(within(table).getByText('stored (local)')).toBeInTheDocument()
    expect(screen.queryByText('No dataset selected.')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove stored' })).not.toBeInTheDocument()
  })

  it('offers only permitted datasets when choosing the first one for a subset scenario', async () => {
    const user = userEvent.setup()
    render(<Harness defaults={{ ...DEFAULTS, sources: [] }} options={{ ...OPTIONS, mode: 'subset', allowed_names: ['other'] }} />)

    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(['other (local)'])
  })

  it('does not pretend a global policy can replace protected source-specific loading rules', () => {
    render(<Harness options={{ ...OPTIONS, mode: 'fixed', source_settings: false }}
      datasets={[{ ...STORED, loaded: false, provider_available: true }]} />)
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
    expect(screen.getByText(/protected policies require the SDK\/API/)).toBeInTheDocument()
  })

  it('explains normalization without changing saved refresh settings', () => {
    const onLoad = jest.fn()
    render(<Harness onLoad={onLoad} initialRequest={{
      sources: [{ name: 'remote', fetch: 'always' }],
    }} />)
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
    expect(screen.getByText(/Saved never\/always policies are normalized/)).toBeInTheDocument()
    expect(onLoad).not.toHaveBeenCalled()
  })

  it('keeps source edits usable while provider preparation is blocked', async () => {
    const user = userEvent.setup()
    const onLoad = jest.fn()
    render(<Harness onLoad={onLoad} preparing initialRequest={{
      sources: [{ name: 'stored', fetch: 'always' }, { name: 'remote', fetch: 'if_missing' }],
    }} />)
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled()
    expect(screen.getByRole('combobox', { name: 'Add dataset' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Remove remote' }))
    expect(screen.queryByRole('row', { name: /remote/ })).not.toBeInTheDocument()
    expect(onLoad).not.toHaveBeenCalled()
  })

  it('keeps metadata refresh status outside responsive table columns and clears it after checking', () => {
    const view = render(<Harness loading />)
    expect(screen.getByRole('status')).toHaveTextContent('Checking dataset details...')
    expect(within(screen.getByRole('table', { name: 'Selected datasets' })).queryByRole('status')).not.toBeInTheDocument()
    view.rerender(<Harness />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it.each([true, false])('announces loading while reading metadata or importing data (metadata: %s)', (loading) => {
    render(<Harness initialRequest={{ sources: [{ name: 'remote' }] }} loading={loading} preparing={!loading} />)
    expect(screen.getByRole('status')).toHaveTextContent(loading ? 'Checking dataset details...' : 'Loading selected datasets in the background')
    expect(screen.queryByText('Not stored in memory')).not.toBeInTheDocument()
  })

  it('shows recorded origins in both rows and selection without changing source names', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    expect(screen.getByText('stored (local)')).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    expect(screen.getByRole('option', { name: 'other (local)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'remote (unknown)' })).toBeInTheDocument()
  })

  it('explains that stored data wins', () => {
    render(<Harness />)
    expect(screen.getByText(/Stored contents are kept, including generated datasets/)).toBeInTheDocument()
  })
})
