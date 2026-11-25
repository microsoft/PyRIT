import type { ReactNode } from 'react'

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { Link, MemoryRouter, Route, Routes, useNavigate } from 'react-router'

import { promptGeneratorsApi, scenarioPresetsApi, scenariosApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import { makeScenario } from '@/test-utils/scenarioFixtures'
import type {
  Parameter,
  RegisteredScenario,
  ScenarioDatasetSettings,
  ScenarioPreset,
  ScenarioRunSizeEstimateResponse,
} from '@/types'

import ScenarioPresetEditor from './ScenarioPresetEditor'

jest.mock('@/services/api', () => ({
  datasetsApi: {
    listDatasets: jest.fn().mockResolvedValue({
      items: ['harmbench', 'xstest'].map((name) => ({
        name, selection_key: `dataset:named:${name}`, loaded: true, provider_available: true,
        logical_examples: 8, seed_pieces: null, objectives: null, modalities: ['text'],
        harm_categories: ['violence'], has_unlabeled_harm_categories: false,
      })),
    }),
  },
  promptGeneratorsApi: { list: jest.fn() },
  scenarioPresetsApi: {
    get: jest.fn(),
    update: jest.fn(),
  },
  scenariosApi: {
    listCompatibleDatasets: jest.fn().mockResolvedValue({
      items: ['harmbench', 'xstest'].map((name) => ({
        name, selection_key: `dataset:named:${name}`, loaded: true, provider_available: true,
        logical_examples: 8, modalities: ['text'], harm_categories: ['violence'],
        has_unlabeled_harm_categories: false,
      })),
    }),
    loadDatasets: jest.fn(),
    getScenario: jest.fn(),
    estimateRun: jest.fn(),
  },
}))

const mockGet = scenarioPresetsApi.get as jest.Mock
const mockUpdate = scenarioPresetsApi.update as jest.Mock
const mockGetScenario = scenariosApi.getScenario as jest.Mock
const mockEstimateRun = scenariosApi.estimateRun as jest.Mock
const mockListGenerators = promptGeneratorsApi.list as jest.Mock

const mockNavigate = jest.fn()

jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  useNavigate: jest.fn(() => mockNavigate),
}))

const mockUseNavigate = jest.mocked(useNavigate)
const SCENARIO = makeScenario()

const STORED_PRESET: ScenarioPreset = {
  name: 'nightly_probe',
  scenario_name: 'foundry.red_team_agent',
  description: 'Nightly smoke test.',
  techniques: ['crescendo'],
  include_baseline: false,
}

function apiError(status: number, detail: string): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { detail } },
  }
}

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}


/** Drives the shared dataset table's limit popover the same way its own suite does. */
async function editLimit(user: ReturnType<typeof userEvent.setup>, label: string, value: string): Promise<void> {
  await user.click(await screen.findByRole('button', { name: `Edit ${label}` }))
  const all = screen.getByRole('checkbox', { name: `All available for ${label}` })
  if (all instanceof HTMLInputElement && all.checked) await user.click(all)
  const input = screen.getByRole('textbox', { name: label })
  await user.clear(input)
  await user.type(input, value)
}

function renderEdit(name = 'nightly_probe') {
  return render(
    <MemoryRouter initialEntries={[`/registry/scenario-presets/${name}/edit`]}>
      <Routes>
        <Route
          path="/registry/scenario-presets/:presetName/edit"
          element={<ScenarioPresetEditor />}
        />
      </Routes>
    </MemoryRouter>,
    { wrapper: TestWrapper },
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  for (const mock of [mockGet, mockUpdate, mockGetScenario, mockEstimateRun, mockListGenerators]) {
    mock.mockReset()
  }
  mockListGenerators.mockResolvedValue({ items: [] })
  mockUseNavigate.mockReturnValue(mockNavigate)

  mockGetScenario.mockResolvedValue(SCENARIO)
  mockEstimateRun.mockResolvedValue(SCENARIO.default_run_size)
  mockGet.mockResolvedValue({ preset: STORED_PRESET, version: 'v1', issues: [] })
  mockUpdate.mockResolvedValue({ preset: STORED_PRESET, version: 'v2', issues: [] })
})

describe('ScenarioPresetEditor edit mode', () => {
  it('leads with the scenario and keeps the stored name as secondary text', async () => {
    renderEdit()

    expect(await screen.findByRole('heading', { level: 1, name: 'Edit foundry.red_team_agent' })).toBeInTheDocument()
    expect(screen.getByText(/^Saved as nightly_probe\./)).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Scenario' })).not.toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    expect(screen.getByTestId('preset-description-input')).toHaveValue('Nightly smoke test.')
  })

  it('updates with the version it read so a concurrent edit is not overwritten', async () => {
    const user = userEvent.setup()
    renderEdit()

    await user.click(await screen.findByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ name: 'nightly_probe', techniques: ['crescendo'] }),
      'v1',
    ))

    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-presets')
  })

  it('preserves stored list values when edit metadata finishes loading', async () => {
    const user = userEvent.setup()
    const metadata = deferred<RegisteredScenario>()
    const languages = ['French', 'German']
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, scenario_params: { languages } },
      version: 'v1',
      issues: [],
    })
    mockGetScenario.mockReturnValueOnce(metadata.promise)
    renderEdit()

    expect(await screen.findByRole('button', { name: 'Save scenario' })).toBeDisabled()
    await act(async () => {
      metadata.resolve(makeScenario({
        supported_parameters: [
          { name: 'languages', type_name: 'list[str]', is_list: true, required: false, default: ['English'] },
        ],
      }))
    })
    expect(await screen.findByRole('textbox', { name: 'languages' })).toHaveValue('French, German')
    await user.type(screen.getByRole('textbox', { name: 'Description' }), ' Updated.')
    await user.click(screen.getByRole('button', { name: 'Save scenario' }))

    expect(mockUpdate).toHaveBeenCalledWith(
      STORED_PRESET.name,
      expect.objectContaining({ techniques: STORED_PRESET.techniques, scenario_params: { languages } }),
      'v1',
    )
  })

  it('reports a version conflict instead of navigating away', async () => {
    const user = userEvent.setup()
    mockUpdate.mockRejectedValue(apiError(409, 'The preset changed since it was loaded.'))
    renderEdit()

    await user.click(await screen.findByTestId('save-preset-btn'))

    expect(await screen.findByText('The preset changed since it was loaded.')).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('keeps pinned techniques this deployment does not offer instead of rewriting them', async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, techniques: ['crescendo', 'retired_attack'] },
      version: 'v1',
      issues: [{ field: 'techniques', message: "Technique 'retired_attack' is not available." }],
    })

    renderEdit()

    expect(await screen.findByTestId('dropped-techniques-warning')).toHaveTextContent(
      'retired_attack',
    )
    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ techniques: ['crescendo', 'retired_attack'] }),
      'v1',
    ))
  })

  it('keeps a converter-qualified technique through an unrelated edit', async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, techniques: ['crescendo:converter.translation_spanish'] },
      version: 'v1',
      issues: [],
    })

    renderEdit()

    await user.type(await screen.findByTestId('preset-description-input'), ' Updated.')
    expect(screen.queryByTestId('dropped-techniques-warning')).not.toBeInTheDocument()
    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ techniques: ['crescendo:converter.translation_spanish'] }),
      'v1',
    ))
  })

  it('shows modifier validation from the server even when the base technique is available', async () => {
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, techniques: ['crescendo:converter.missing'] },
      version: 'v1',
      issues: [{ field: 'techniques', message: "Converter 'missing' is not registered." }],
    })

    renderEdit()

    expect(await screen.findByTestId('dropped-techniques-warning')).toHaveTextContent(
      "Converter 'missing' is not registered.",
    )
    expect(screen.getByTestId('techniques-without-checkbox')).toHaveTextContent('crescendo:converter.missing')
  })

  it('does not invent a technique issue for a token the server accepts', async () => {
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, techniques: ['server_extension'] },
      version: 'v1',
      issues: [{ field: 'scenario_params', message: 'A parameter is unavailable.' }],
    })

    renderEdit()

    expect(await screen.findByTestId('techniques-without-checkbox')).toHaveTextContent('server_extension')
    expect(screen.queryByTestId('dropped-techniques-warning')).not.toBeInTheDocument()
  })

  it('shows a pinned aggregate technique as a tag the operator can remove', async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, techniques: ['all'] },
      version: 'v1',
      issues: [],
    })

    renderEdit()

    expect(await screen.findByTestId('techniques-without-checkbox')).toHaveTextContent('all')
    expect(screen.getByTestId('technique-crescendo')).not.toBeChecked()
    expect(screen.queryByTestId('dropped-techniques-warning')).not.toBeInTheDocument()

    await user.click(screen.getByTestId('technique-crescendo'))
    await user.click(screen.getByRole('button', { name: 'Remove all' }))
    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ techniques: ['crescendo'] }),
      'v1',
    ))
  })

  it('reports a missing preset as not found', async () => {
    mockGet.mockRejectedValue(apiError(404, 'No such preset.'))

    renderEdit()

    expect(await screen.findByTestId('editor-error-state')).toHaveTextContent(
      'No saved scenario named "nightly_probe"',
    )
  })

  it('reports an unavailable scenario as unavailable, not as a missing preset', async () => {
    mockGetScenario.mockRejectedValue(apiError(404, 'Unknown scenario "foundry.red_team_agent".'))

    renderEdit()

    expect(await screen.findByTestId('scenario-unavailable-warning')).toBeInTheDocument()
    expect(screen.queryByTestId('editor-error-state')).not.toBeInTheDocument()
    expect(screen.getByTestId('save-preset-btn')).toBeDisabled()
  })

  it('decodes a preset name that was escaped into the route', async () => {
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, name: 'a_b' },
      version: 'v1',
      issues: [],
    })

    renderEdit('a_b')

    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('a_b'))
  })

  it('saves the edited description', async () => {
    const user = userEvent.setup()
    renderEdit()

    const description = await screen.findByTestId('preset-description-input')
    await user.clear(description)
    await user.type(description, 'Weekly smoke test.')
    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ description: 'Weekly smoke test.' }),
      'v1',
    ))
  })

  it('keeps a hidden preset hidden through an unrelated edit', async () => {
    const user = userEvent.setup()
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, show_in_scanner: false },
      version: 'v1',
      issues: [],
    })
    renderEdit()

    const description = await screen.findByTestId('preset-description-input')
    await user.type(description, ' Suite only.')
    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ show_in_scanner: false }),
      'v1',
    ))
  })

  it('associates the unavailable baseline with its scenario-owned explanation', async () => {
    mockGetScenario.mockResolvedValue(makeScenario({
      baseline_policy: 'forbidden', include_baseline_by_default: false,
    }))
    renderEdit()

    const baseline = await screen.findByRole('checkbox', { name: 'baseline', exact: true })
    expect(baseline).toBeDisabled()
    expect(baseline).toHaveAccessibleDescription('This scenario does not support a baseline comparison.')
  })

  it('saves the techniques and baseline choice the operator changed', async () => {
    const user = userEvent.setup()
    renderEdit()

    await user.click(await screen.findByTestId('technique-default_technique'))
    await user.click(screen.getByTestId('baseline-checkbox'))
    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({
        techniques: ['crescendo', 'default_technique'],
        include_baseline: true,
      }),
      'v1',
    ))
  })

  it('saves the dataset selection the operator made', async () => {
    const user = userEvent.setup()
    renderEdit()

    await user.click(await screen.findByRole('combobox', { name: 'Add dataset' }))
    await user.click(screen.getByRole('option', { name: 'xstest (Unknown)', exact: true }))
    await editLimit(user, 'Maximum objectives', '25')
    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled())
    const saved = mockUpdate.mock.calls.at(-1)?.[1] as ScenarioPreset
    expect(saved.datasets?.sources?.map((source) => source.name)).toEqual(['harmbench', 'xstest'])
    expect(saved.datasets?.max_total).toBe(25)
  })

  it('opens the dataset catalog in a new tab so unsaved preset edits stay', async () => {
    renderEdit()

    const actions = await screen.findByRole('group', { name: 'Dataset actions' })
    expect(within(actions).getByRole('link', { name: 'Browse datasets' })).toHaveAttribute('target', '_blank')
    expect(screen.getAllByRole('link', { name: 'Browse datasets' })).toHaveLength(1)
  })

  it("offers Generate datasets for the preset's scenario once its generators load", async () => {
    const generators = deferred<{ items: unknown[] }>()
    mockListGenerators.mockReturnValue(generators.promise)
    renderEdit()

    const actions = await screen.findByRole('group', { name: 'Dataset actions' })
    expect(within(actions).getByRole('button', { name: 'Generate datasets' })).toHaveAttribute('aria-disabled', 'true')
    await act(async () => {
      generators.resolve({ items: [{
        name: 'objective', display_name: 'Objective dataset', description: 'Objectives.',
        stages: [], target_roles: [], follow_up_scenario: STORED_PRESET.scenario_name,
      }] })
    })

    expect(await within(actions).findByRole('link', { name: 'Generate datasets' }))
      .toHaveAttribute('href', '/datasets/generate/new?generator=objective')
    expect(within(actions).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Browse datasets', 'Generate datasets',
    ])
  })

  it('offers only Browse datasets when the scenario takes only named datasets', async () => {
    mockGetScenario.mockResolvedValue(makeScenario({
      dataset_options: {
        mode: 'subset', allowed_names: ['harmbench', 'xstest'], max_selected: null, per_dataset_limit: true,
        total_limit: true, supports_overrides: true, filters: true, source_settings: true,
      },
    }))
    renderEdit()

    const actions = await screen.findByRole('group', { name: 'Dataset actions' })
    expect(within(actions).getByRole('link', { name: 'Browse datasets' })).toBeInTheDocument()
    expect(within(actions).queryByRole('button', { name: 'Generate datasets' })).not.toBeInTheDocument()
    expect(within(actions).queryByRole('link', { name: 'Generate datasets' })).not.toBeInTheDocument()
    expect(mockListGenerators).not.toHaveBeenCalled()
  })

  it('refuses to save while the dataset control reports an invalid limit', async () => {
    const user = userEvent.setup()
    renderEdit()

    await editLimit(user, 'Maximum objectives', '0')
    await user.keyboard('{Escape}')
    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(screen.getByTestId('save-preset-btn')).toBeEnabled())
    expect(mockUpdate).not.toHaveBeenCalled()
  })

  it('offers no objective cap on a scenario that sizes itself by prompt generation', async () => {
    const generativeScenario = makeScenario({
      default_run_size: {
        dataset_size: { kind: 'indeterminate', detail: 'Generated at run time.' },
        dataset_limit: { state: 'not_applicable' },
        estimated_attack_count: null,
        components: [],
        datasets: [],
        note: null,
      },
    })
    mockGetScenario.mockResolvedValue(generativeScenario)
    renderEdit()

    expect(await screen.findByRole('combobox', { name: 'Add dataset' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit Maximum objectives' })).not.toBeInTheDocument()
  })

  it('leaves the editor without saving when cancelled', async () => {
    const user = userEvent.setup()
    renderEdit()

    await user.click(await screen.findByRole('button', { name: 'Cancel' }))

    expect(mockUpdate).not.toHaveBeenCalled()
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-presets')
  })

  it('returns to the library from the not-found state', async () => {
    const user = userEvent.setup()
    mockGet.mockRejectedValue(apiError(404, 'No such preset.'))

    renderEdit()

    await user.click(await screen.findByRole('button', { name: 'Back to presets' }))

    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-presets')
  })

  it('reports a read failure that is not a missing preset as an error', async () => {
    mockGet.mockRejectedValue(apiError(503, 'Preset storage is not configured.'))

    renderEdit()

    expect(await screen.findByTestId('editor-error-state')).toHaveTextContent(
      'Preset storage is not configured.',
    )
  })
})

describe('ScenarioPresetEditor dataset projection', () => {
  const defaults: ScenarioDatasetSettings = {
    sources: [{ name: 'harmbench', max_size: 5, fetch: 'if_missing', configured_provider: false }],
    max_per_dataset: 5,
    max_total: 'all',
    fetch: 'if_missing',
    filters: {},
  }

  function projection(limit: number | 'all'): ScenarioRunSizeEstimateResponse {
    return {
      ...SCENARIO.default_run_size,
      dataset_defaults: defaults,
      effective_datasets: {
        ...defaults,
        max_per_dataset: limit,
        sources: [{ ...defaults.sources[0], max_size: limit }],
      },
    }
  }

  beforeEach(() => {
    jest.useFakeTimers()
    mockGetScenario.mockResolvedValue(makeScenario({ dataset_defaults: defaults }))
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it.each([
    { limit: 2, text: 'Up to 2 of 8' },
    { limit: 'all', text: '8 examples' },
  ] as const)('shows the effective size for max_per_dataset=$limit', async ({ limit, text }) => {
    const datasets = { max_per_dataset: limit }
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, datasets },
      version: 'v1',
      issues: [],
    })
    mockEstimateRun.mockResolvedValue(projection(limit))

    renderEdit()

    const row = await screen.findByRole('row', { name: /harmbench/ })
    expect(await within(row).findByText(text)).toBeInTheDocument()
    expect(mockEstimateRun).toHaveBeenCalledWith(
      STORED_PRESET.scenario_name,
      expect.objectContaining({ techniques: ['crescendo'], datasets, include_baseline: false }),
      expect.any(AbortSignal),
    )
  })

  it('uses projected limits for a source that is not in the scenario defaults', async () => {
    const datasets = { sources: [{ name: 'xstest' }], max_per_dataset: 2 }
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, datasets },
      version: 'v1',
      issues: [],
    })
    mockEstimateRun.mockResolvedValue({
      ...projection(2),
      effective_datasets: {
        ...defaults,
        max_per_dataset: 2,
        sources: [{ name: 'xstest', max_size: 2, fetch: 'if_missing', configured_provider: false }],
      },
    })

    renderEdit()

    const row = await screen.findByRole('row', { name: /xstest/ })
    expect(await within(row).findByText('Up to 2 of 8')).toBeInTheDocument()
  })

  it('preserves a source-specific cap while projecting the inherited per-dataset cap', async () => {
    const sourceDefaults: ScenarioDatasetSettings = {
      ...defaults,
      sources: [
        { ...defaults.sources[0], max_size: 3 },
        { ...defaults.sources[0], name: 'xstest' },
      ],
    }
    mockGetScenario.mockResolvedValue(makeScenario({ dataset_defaults: sourceDefaults }))
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, datasets: { max_per_dataset: 2 } },
      version: 'v1',
      issues: [],
    })
    mockEstimateRun.mockResolvedValue({
      ...projection(2),
      dataset_defaults: sourceDefaults,
      effective_datasets: {
        ...sourceDefaults,
        max_per_dataset: 2,
        sources: [sourceDefaults.sources[0], { ...sourceDefaults.sources[1], max_size: 2 }],
      },
    })

    renderEdit()

    const inherited = await screen.findByRole('row', { name: /xstest/ })
    expect(await within(inherited).findByText('Up to 2 of 8')).toBeInTheDocument()
    const pinned = screen.getByRole('row', { name: /harmbench/ })
    expect(within(pinned).getByText('Up to 3 of 8')).toBeInTheDocument()
  })

  it('reports projection errors without preventing a preset from being saved', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    mockEstimateRun.mockRejectedValue(new Error('Projection unavailable.'))

    renderEdit()

    expect(await screen.findByText(
      'Dataset preview could not be updated: Projection unavailable.',
    )).toBeInTheDocument()
    await user.click(screen.getByTestId('save-preset-btn'))
    await waitFor(() => expect(mockUpdate).toHaveBeenCalled())
  })

  it('ignores a pending projection after the operator resets the datasets', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    let complete: (value: ScenarioRunSizeEstimateResponse) => void = () => {}
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, datasets: { max_per_dataset: 2 } },
      version: 'v1',
      issues: [],
    })
    mockEstimateRun
      .mockReturnValueOnce(new Promise<ScenarioRunSizeEstimateResponse>((resolve) => { complete = resolve }))
      .mockResolvedValue(projection(5))

    renderEdit()

    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledTimes(1))
    const signal: AbortSignal = mockEstimateRun.mock.calls[0][2]
    await user.click(screen.getByRole('button', { name: 'Reset datasets' }))
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledTimes(2))
    expect(signal.aborted).toBe(true)

    await act(async () => { complete(projection(2)) })

    const row = screen.getByRole('row', { name: /harmbench/ })
    expect(within(row).getByText('Up to 5 of 8')).toBeInTheDocument()
    expect(within(row).queryByText('Up to 2 of 8')).not.toBeInTheDocument()
  })

  it('stops the preview spinner when an invalid edit cancels the pending request', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    mockEstimateRun.mockReturnValueOnce(new Promise<ScenarioRunSizeEstimateResponse>(() => {}))

    renderEdit()

    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledTimes(1))
    expect(screen.getByText('Updating dataset preview...')).toBeInTheDocument()
    const signal: AbortSignal = mockEstimateRun.mock.calls[0][2]

    await user.click(screen.getByTestId('technique-crescendo'))

    expect(signal.aborted).toBe(true)
    expect(screen.queryByText('Updating dataset preview...')).not.toBeInTheDocument()
    await user.click(screen.getByTestId('save-preset-btn'))
    expect(await screen.findByText('Select at least one technique.')).toBeInTheDocument()
    expect(mockUpdate).not.toHaveBeenCalled()
  })
})

describe('ScenarioPresetEditor dynamic parameters', () => {
  const GOALS_PARAMETER: Parameter = {
    name: 'goals',
    type_name: 'list[str]',
    is_list: true,
    required: false,
    default: ['Default goal'],
  }
  const SCENARIO_WITH_PARAM = makeScenario({
    supported_parameters: [
      {
        name: 'max_turns',
        type_name: 'int',
        required: false,
        default: '5',
        description: 'Turn budget.',
      },
    ],
  })

  it('stores a scenario-specific parameter the operator changed', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(SCENARIO_WITH_PARAM)
    renderEdit()

    const field = await screen.findByTestId('preset-param-max_turns')
    await user.clear(field)
    await user.type(field, '9')
    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ scenario_params: { max_turns: 9 } }),
      'v1',
    ))
  })

  it('preserves and surfaces stored parameters this editor renders no field for', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(SCENARIO_WITH_PARAM)
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, scenario_params: { max_turns: 5, max_concurrency: 4 } },
      version: 'v1',
      issues: [],
    })

    renderEdit()

    expect(await screen.findByTestId('carried-params-notice')).toHaveTextContent('max_concurrency')

    await user.click(screen.getByTestId('save-preset-btn'))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ scenario_params: { max_turns: 5, max_concurrency: 4 } }),
      'v1',
    ))
  })

  it.each([
    { label: 'comma-containing goals', goals: ['Hello, world!', 'Second goal'] },
    { label: 'a comma-only goal', goals: [','] },
    { label: 'an explicit empty list', goals: [] },
    { label: 'whitespace, duplicates and order', goals: [' third ', 'first', 'first'] },
  ])('should preserve $label during a description-only edit', async ({ goals }) => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({ supported_parameters: [GOALS_PARAMETER] }))
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, scenario_params: { goals } },
      version: 'v1',
      issues: [],
    })
    renderEdit()

    await screen.findByRole('textbox', { name: 'goals' })
    await user.type(screen.getByRole('textbox', { name: 'Description' }), ' Updated.')
    await user.click(screen.getByRole('button', { name: 'Save scenario' }))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ scenario_params: { goals } }),
      'v1',
    ))
  })

  it.each([
    { input: 'a, b', goals: ['a', 'b'] },
    { input: 'padded, Second goal', goals: ['padded', 'Second goal'] },
  ])('should save an explicit list edit to "$input"', async ({ input, goals }) => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({ supported_parameters: [GOALS_PARAMETER] }))
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, scenario_params: { goals: [' padded ', 'Second goal'] } },
      version: 'v1',
      issues: [],
    })
    renderEdit()

    const field = await screen.findByRole('textbox', { name: 'goals' })
    await user.clear(field)
    await user.type(field, input)
    await user.click(screen.getByRole('button', { name: 'Save scenario' }))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ scenario_params: { goals } }),
      'v1',
    ))
  })

  it('should preserve an untouched list when the operator fills a missing required field', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({
      supported_parameters: [
        { name: 'objective', type_name: 'str', required: true, multiline: true },
        GOALS_PARAMETER,
      ],
    }))
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, scenario_params: { goals: ['Hello, world!'] } },
      version: 'v1',
      issues: [],
    })
    renderEdit()

    await user.type(await screen.findByRole('textbox', { name: 'objective' }), 'Test this objective')
    await user.click(screen.getByRole('button', { name: 'Save scenario' }))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({
        scenario_params: { objective: 'Test this objective', goals: ['Hello, world!'] },
      }),
      'v1',
    ))
  })

  it('should estimate with the same untouched list that will be saved', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({ supported_parameters: [GOALS_PARAMETER] }))
    mockGet.mockResolvedValue({
      preset: { ...STORED_PRESET, scenario_params: { goals: ['Hello, world!'] } },
      version: 'v1',
      issues: [],
    })
    renderEdit()

    await screen.findByRole('textbox', { name: 'goals' })
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledWith(
      STORED_PRESET.scenario_name,
      expect.objectContaining({ scenario_params: { goals: ['Hello, world!'] } }),
      expect.any(AbortSignal),
    ))
    await user.click(screen.getByRole('button', { name: 'Save scenario' }))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ scenario_params: { goals: ['Hello, world!'] } }),
      'v1',
    ))
  })

  it('should preserve values from the newly opened preset rather than the previous form', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({ supported_parameters: [GOALS_PARAMETER] }))
    mockGet.mockImplementation((name: string) => Promise.resolve({
      preset: {
        ...STORED_PRESET,
        name,
        scenario_params: { goals: name === 'first' ? ['Hello, world!'] : [' Next, goal '] },
      },
      version: 'v1',
      issues: [],
    }))
    render(
      <FluentProvider theme={webLightTheme}>
        <MemoryRouter initialEntries={['/registry/scenario-presets/first/edit']}>
          <Link to="/registry/scenario-presets/second/edit">Open second preset</Link>
          <Routes>
            <Route
              path="/registry/scenario-presets/:presetName/edit"
              element={<ScenarioPresetEditor />}
            />
          </Routes>
        </MemoryRouter>
      </FluentProvider>,
    )

    await user.type(await screen.findByRole('textbox', { name: 'goals' }), ' discarded edit')
    await user.click(screen.getByRole('link', { name: 'Open second preset' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'goals' })).toHaveValue(' Next, goal '))
    await user.click(screen.getByRole('button', { name: 'Save scenario' }))

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(
      'second',
      expect.objectContaining({ scenario_params: { goals: [' Next, goal '] } }),
      'v1',
    ))
  })
})


describe('ScenarioPresetEditor compatible-only datasets', () => {
  const POLICY = makeScenario({
    dataset_defaults: { sources: [], max_per_dataset: 'all', max_total: 'all', fetch: 'if_missing', filters: {} },
    dataset_options: {
      mode: 'any', allowed_names: [], per_dataset_limit: true, total_limit: true, supports_overrides: true,
      filters: true, compatible_only: true,
    },
  })
  const listing = scenariosApi.listCompatibleDatasets as jest.Mock

  it('shows the launch form\'s failure Retry and empty state for a scenario that checks stored contents', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(POLICY)
    listing.mockRejectedValueOnce(apiError(503, 'Compatible listing unavailable.')).mockResolvedValueOnce({ items: [] })
    renderEdit()

    expect(await screen.findByText('Compatible listing unavailable.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry loading compatible datasets' }))
    expect(await screen.findByText('No compatible datasets available.')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Add dataset' })).not.toBeInTheDocument()
    expect(screen.queryByRole('radiogroup', { name: 'Dataset loading' })).not.toBeInTheDocument()
  })
})
