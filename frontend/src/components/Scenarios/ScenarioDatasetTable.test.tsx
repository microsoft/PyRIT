import { useState } from 'react'

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { ScenarioDatasetOptions, ScenarioDatasetRequest, ScenarioDatasetSettings } from '@/types'

import ScenarioDatasetTable from './ScenarioDatasetTable'

const DEFAULTS: ScenarioDatasetSettings = {
  sources: [{ name: 'stored', max_size: 2, fetch: 'never', configured_provider: false }],
  max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {},
}
const OPTIONS: ScenarioDatasetOptions = {
  mode: 'any', allowed_names: [], per_dataset_limit: true, total_limit: true, supports_overrides: true, filters: true,
}

interface HarnessProps {
  options?: ScenarioDatasetOptions
  defaults?: ScenarioDatasetSettings
  onChange: jest.Mock
}

function Harness({ options = OPTIONS, defaults = DEFAULTS, onChange }: HarnessProps) {
  const [request, setRequest] = useState<ScenarioDatasetRequest>({})
  const [validation, setValidation] = useState<string | null>(null)
  return <FluentProvider theme={webLightTheme}><ScenarioDatasetTable
    request={request} defaults={defaults} effective={defaults} options={options} disabled={false}
    datasets={[
      { name: 'stored', selection_key: 'dataset:named:stored', origins: ['local', 'unknown'], logical_examples: 8, loaded: true, provider_available: false, seed_pieces: 8, objectives: 8, modalities: ['text'], harm_categories: [], has_unlabeled_harm_categories: false },
      { name: 'remote', selection_key: 'dataset:named:remote', origins: [], logical_examples: null, loaded: false, provider_available: true, seed_pieces: null, objectives: null, modalities: [], harm_categories: [], has_unlabeled_harm_categories: false },
    ]}
    loading={false} metadataError={null} onReload={jest.fn()} onValidationChange={setValidation}
    onChange={(next) => { setRequest(next); onChange(next) }}
  /><button disabled={validation !== null}>Launch</button></FluentProvider>
}

describe('ScenarioDatasetTable', () => {
  beforeEach(() => jest.clearAllMocks())

  it('shows mixed origins, stored counts, and unknown provider-only counts without sending defaults', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    expect(screen.getByText('local, unknown')).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
    await user.type(screen.getByRole('combobox', { name: 'Add dataset' }), 'remote')
    await user.click(screen.getByRole('button', { name: 'Add source' }))
    const row = screen.getByRole('rowheader', { name: 'remote' }).closest('tr')
    expect(row).not.toBeNull()
    if (row) {
      expect(within(row).getByText('Provider available')).toBeInTheDocument()
      expect(within(row).getAllByText('Unknown')).toHaveLength(2)
    }
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ sources: [{ name: 'stored' }, { name: 'remote' }] })
  })

  it('edits source caps and policies independently of global limits with keyboard-accessible controls', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    await user.type(screen.getByRole('textbox', { name: 'stored limit' }), 'all')
    await user.selectOptions(screen.getByRole('combobox', { name: 'stored fetch' }), 'if_missing')
    await user.type(screen.getByRole('textbox', { name: 'Max total' }), '12')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Default dataset fetch' }), 'never')
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({
      sources: [{ name: 'stored', max_size: 'all', fetch: 'if_missing' }], max_total: 12, fetch: 'never',
    })
    await user.clear(screen.getByRole('textbox', { name: 'Max total' }))
    expect(onChange.mock.calls.at(-1)?.[0]).not.toHaveProperty('max_total')
    screen.getByRole('textbox', { name: 'Max per dataset' }).focus()
    await user.keyboard('3')
    expect(onChange.mock.calls.at(-1)?.[0]).toHaveProperty('max_per_dataset', 3)
  })

  it('does not send fixed or unsupported inputs and supports one-source replacement', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const view = render(<Harness onChange={onChange} options={{
      ...OPTIONS, mode: 'fixed', per_dataset_limit: false, total_limit: false, source_settings: false,
    }} />)
    expect(screen.queryByRole('button', { name: 'Add source' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'stored limit' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Max total' })).not.toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
    view.rerender(<Harness onChange={onChange} options={{ ...OPTIONS, mode: 'subset', max_selected: 1, allowed_names: ['stored', 'remote'] }} />)
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    await user.click(screen.getByRole('option', { name: 'remote' }))
    await user.click(screen.getByRole('button', { name: 'Add source' }))
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({ sources: [{ name: 'remote' }] })
    expect(screen.queryByRole('rowheader', { name: 'stored' })).not.toBeInTheDocument()
  })

  it('retains edits and displays invalid selections when parameter-dependent options change', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const view = render(<Harness onChange={onChange} />)
    await user.type(screen.getByRole('textbox', { name: 'stored limit' }), '1')
    view.rerender(<Harness onChange={onChange}
      options={{ ...OPTIONS, mode: 'subset', allowed_names: ['remote'] }}
      defaults={{ ...DEFAULTS, sources: [{ name: 'remote', max_size: 5, fetch: 'if_missing', configured_provider: false }] }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid selection for current parameters: stored')
    expect(screen.getByRole('textbox', { name: 'stored limit' })).toHaveValue('1')
    await user.click(screen.getByRole('button', { name: 'Use default sources' }))
    expect(screen.queryByRole('rowheader', { name: 'stored' })).not.toBeInTheDocument()
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual({})
  })
  it.each([
    ['Max total', 'max_total', '3'], ['Max total', 'max_total', 'all'],
    ['Max per dataset', 'max_per_dataset', '3'], ['Max per dataset', 'max_per_dataset', 'all'],
  ])('keeps invalid %s blocking launch after source reset (%s=%s)', async (label, key, initial) => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    const input = screen.getByRole('textbox', { name: label })
    await user.type(input, initial)
    await user.click(input)
    await user.keyboard('{Control>}a{/Control}0')
    expect(input).toHaveValue('0')
    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Use default sources' }))
    expect(input).toHaveValue('0')
    expect(screen.getByRole('alert')).toHaveTextContent(`${label} must be a positive integer`)
    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled()
    expect(onChange.mock.calls.at(-1)?.[0]).toHaveProperty(key, initial === 'all' ? 'all' : 3)
    await user.click(input)
    await user.keyboard('{Control>}a{/Control}4')
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled()
  })

  it('clears only removed source errors and preserves other invalid fields', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness onChange={onChange} />)
    await user.type(screen.getByRole('combobox', { name: 'Add dataset' }), 'remote')
    await user.click(screen.getByRole('button', { name: 'Add source' }))
    await user.type(screen.getByRole('textbox', { name: 'remote limit' }), '0')
    await user.type(screen.getByRole('textbox', { name: 'Max total' }), '0')
    await user.click(screen.getByRole('button', { name: 'Remove remote' }))
    expect(screen.queryByRole('textbox', { name: 'remote limit' })).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Max total must be a positive integer')
    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled()
    await user.clear(screen.getByRole('textbox', { name: 'Max total' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled()
    await user.type(screen.getByRole('combobox', { name: 'Add dataset' }), 'remote')
    await user.click(screen.getByRole('button', { name: 'Add source' }))
    expect(screen.getByRole('textbox', { name: 'remote limit' })).toHaveValue('')
  })

  it('resets source drafts even when default sources have the same names', async () => {
    const user = userEvent.setup()
    render(<Harness onChange={jest.fn()} />)
    await user.type(screen.getByRole('textbox', { name: 'stored limit' }), '0')
    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Use default sources' }))
    expect(screen.getByRole('textbox', { name: 'stored limit' })).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled()
  })

  it('replacing the sole selected source removes its invalid draft', async () => {
    const user = userEvent.setup()
    render(<Harness onChange={jest.fn()} options={{ ...OPTIONS, mode: 'subset', max_selected: 1, allowed_names: ['stored', 'remote'] }} />)
    await user.type(screen.getByRole('textbox', { name: 'stored limit' }), '0')
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    await user.click(screen.getByRole('option', { name: 'remote' }))
    await user.click(screen.getByRole('button', { name: 'Add source' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled()
  })

})
