import { useState } from 'react'

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import type {
  DatasetInfo, ScenarioDatasetOptions, ScenarioDatasetRequest, ScenarioDatasetSettings,
} from '@/types'

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
  metadataError?: string | null
  onReload?: jest.Mock
}
function Harness({
  options = OPTIONS, defaults = DEFAULTS, datasets = DATASETS, initialRequest = {}, onChange = jest.fn(),
  loading = false, metadataError = null, onReload = jest.fn(),
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
      datasets={datasets} disabled={false} loading={loading} metadataError={metadataError}
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
  await user.click(screen.getByRole('option', { name: `${name} (${dataset?.origins?.join(', ') || 'unknown'})`, exact: true }))
}

describe('ScenarioDatasetTable', () => {
  it('offers only server-eligible choices with stable accessible names', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} datasets={[STORED, DATASETS[2]]} />)
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    const option = screen.getByRole('option', { name: 'other (local)', exact: true })
    expect(screen.queryByRole('option', { name: /^remote/ })).not.toBeInTheDocument()
    await user.click(option)
    expect(onChange.mock.calls.at(-1)?.[0].sources).toEqual([{ name: 'stored' }, { name: 'other' }])
  })

  it('keeps a previously selected source visible without exposing a technique matrix or verification controls', () => {
    render(<Harness datasets={[DATASETS[2]]} />)
    expect(screen.getByText('stored (unknown)')).toBeInTheDocument()
    expect(screen.queryByText(/usable|skipped|Not verified/)).not.toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: 'Show incompatible' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Reset datasets|Load and validate|Load datasets/ })).not.toBeInTheDocument()
  })

  it('selects the first eligible dataset using only the keyboard without reloading choices', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const onReload = jest.fn()
    render(<Harness defaults={{ ...DEFAULTS, sources: [] }} datasets={[STORED]} onChange={onChange} onReload={onReload} />)
    await user.tab()
    expect(screen.getByRole('combobox', { name: 'Add dataset' })).toHaveFocus()
    await user.keyboard('{ArrowDown}{Enter}')
    expect(onChange).toHaveBeenLastCalledWith({ sources: [{ name: 'stored' }] })
    expect(screen.getByRole('table', { name: 'Selected datasets' })).toHaveTextContent('stored (local)')
    expect(onReload).not.toHaveBeenCalled()
  })

  it('does not duplicate the section-level catalog actions in its empty state', () => {
    render(<Harness defaults={{ ...DEFAULTS, sources: [] }} datasets={[]} />)
    expect(screen.getByText('No compatible datasets available.')).toBeInTheDocument()
    expect(screen.queryAllByRole('link')).toHaveLength(0)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByText('No dataset selected.')).not.toBeInTheDocument()
  })

  it('offers retry only after a real eligibility-load failure', async () => {
    const user = userEvent.setup()
    const onReload = jest.fn()
    render(<Harness defaults={{ ...DEFAULTS, sources: [] }} datasets={[]} metadataError="Dataset service is offline." onReload={onReload} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Dataset service is offline.')
    expect(screen.queryByText('No compatible datasets available.')).not.toBeInTheDocument()
    expect(onReload).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Retry loading datasets' }))
    expect(onReload).toHaveBeenCalledTimes(1)
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
    render(<Harness onChange={onChange} />)
    await addDataset(user, 'remote')
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ sources: [{ name: 'stored' }, { name: 'remote' }] })
    expect(screen.getByRole('button', { name: 'Edit remote objective limit' })).toHaveTextContent('Up to 4')
    expect(screen.getByText('Not stored in memory')).toBeInTheDocument()
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

  it('keeps invalid global drafts blocking when the selected source changes', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await editLimit(user, 'Maximum objectives', '0')
    await user.keyboard('{Escape}')
    await addDataset(user, 'other')
    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('Maximum objectives must be a positive integer')
    await user.click(screen.getByRole('button', { name: 'Edit Maximum objectives' }))
    expect(screen.getByRole('textbox', { name: 'Maximum objectives' })).toHaveValue('0')
    await user.click(screen.getByRole('button', { name: 'Reset to 4' }))
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled()
  })

  it('removes only the invalid row draft and can clear the final selected source', async () => {
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
    expect(screen.getByRole('button', { name: 'Remove stored' })).toBeInTheDocument()
    await editLimit(user, 'stored objective limit', '3')
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'Remove stored' }))
    expect(onChange).toHaveBeenLastCalledWith({ sources: [] })
    expect(screen.getByText('No dataset selected.')).toBeInTheDocument()
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

  it('links to the dataset browser for selected and required sources without a reset action', () => {
    const view = render(<Harness />)
    expect(screen.getByRole('link', { name: 'Browse datasets' })).toHaveAttribute('href', '/datasets')
    expect(screen.queryByRole('button', { name: 'Reset datasets' })).not.toBeInTheDocument()
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

  it('uses pill radios that update all selected source loading policies', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    await addDataset(user, 'remote')
    await user.click(screen.getByRole('radio', { name: 'Load missing' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({
      fetch: 'if_missing', sources: [{ name: 'stored', fetch: 'if_missing' }, { name: 'remote', fetch: 'if_missing' }],
    })
    await user.click(screen.getByRole('radio', { name: 'Stored only' }))
    expect(onChange.mock.calls.at(-1)?.[0].sources.every((source: { fetch: string }) => source.fetch === 'never')).toBe(true)
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
  })

  it('keeps missing-data loading on by default and allows refresh after data is loaded', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const defaults = { ...DEFAULTS, sources: [{ name: 'remote', max_size: 4 as const, fetch: 'if_missing' as const, configured_provider: false }] }
    const view = render(<Harness defaults={defaults} onChange={onChange} />)
    expect(screen.getByRole('radio', { name: 'Load missing' })).toBeChecked()
    view.rerender(<Harness defaults={defaults} onChange={onChange} datasets={[{ ...REMOTE, loaded: true, logical_examples: 100 }]} />)
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Refresh' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ fetch: 'always', sources: [{ name: 'remote', fetch: 'always' }] })
    expect(screen.queryByText(/All selected datasets are loaded/)).not.toBeInTheDocument()
  })

  it('does not assume stored counts are exact selected sizes after filters and a total cap', () => {
    render(<Harness initialRequest={{ max_total: 1, filters: { harm_categories: ['hate'] } }} />)
    expect(screen.getByRole('button', { name: 'Edit stored objective limit' })).toHaveTextContent('Up to 1 of 8')
    const row = screen.getByRole('row', { name: /stored/ })
    expect(within(row).queryByText('1 of 8', { exact: true })).not.toBeInTheDocument()
  })

  it('does not expose loading or source selection for an inline-only scenario', () => {
    render(<Harness defaults={{ ...DEFAULTS, sources: [] }}
      options={{ ...OPTIONS, mode: 'fixed', supports_overrides: false }} />)
    expect(screen.getByText('This scenario has no named datasets.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Add dataset' })).not.toBeInTheDocument()
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
    expect(screen.getByRole('button', { name: 'Remove stored' })).toBeInTheDocument()
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
    expect(screen.getByRole('radio', { name: 'Stored only' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Stored only' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
  })

  it('keeps refresh and missing-only policies as launch settings, without a separate load action', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} initialRequest={{
      sources: [{ name: 'remote', fetch: 'always' }],
    }} />)
    expect(screen.queryByRole('button', { name: 'Load datasets' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Load missing' }))
    expect(onChange).toHaveBeenLastCalledWith({
      fetch: 'if_missing', sources: [{ name: 'remote', fetch: 'if_missing' }],
    })
  })

  it('keeps metadata refresh status outside responsive table columns and clears it after checking', () => {
    const view = render(<Harness loading />)
    expect(screen.getByRole('progressbar')).toHaveAccessibleName('Loading datasets...')
    expect(within(screen.getByRole('table', { name: 'Selected datasets' })).queryByRole('progressbar')).not.toBeInTheDocument()
    view.rerender(<Harness />)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('does not demand a selection or show an empty state while the first load is pending', () => {
    render(<Harness defaults={{ ...DEFAULTS, sources: [] }} datasets={[]} loading />)
    expect(screen.getByRole('progressbar')).toHaveAccessibleName('Loading datasets...')
    expect(screen.queryByText('No dataset selected.')).not.toBeInTheDocument()
    expect(screen.queryByText('No compatible datasets available.')).not.toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Add dataset' })).toBeDisabled()
  })

  it('shows recorded origins in both rows and selection without changing source names', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    expect(screen.getByText('stored (local)')).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    expect(screen.getByRole('option', { name: 'other (local)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'remote (unknown)' })).toBeInTheDocument()
  })

  it('supports keyboard selection and terse policy descriptions', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const group = screen.getByRole('radiogroup', { name: 'Dataset loading' })
    const stored = within(group).getByRole('radio', { name: 'Stored only' })
    await user.click(stored)
    expect(screen.getByText('Only use datasets already in memory')).toBeInTheDocument()
    await user.keyboard('{ArrowRight}')
    expect(within(group).getByRole('radio', { name: 'Load missing' })).toBeChecked()
    expect(screen.getByText('Load missing datasets')).toBeInTheDocument()
    await user.keyboard('{ArrowRight}')
    expect(within(group).getByRole('radio', { name: 'Refresh' })).toBeChecked()
    expect(screen.getByText('Refresh datasets for each new scan')).toBeInTheDocument()
  })
})
