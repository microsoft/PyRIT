import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, useLocation } from 'react-router'

import { scenarioPresetsApi, scenariosApi, scenarioSuitesApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import { makeTarget } from '@/test-utils/targetFixtures'
import type {
  ListRegisteredScenariosResponse,
  PresetIssue,
  RegisteredScenario,
  ScenarioPreset,
  ScenarioPresetListResponse,
  ScenarioPresetResponse,
  ScenarioRunSizeEstimateResponse,
  ScenarioSuiteEntryStatus,
  ScenarioSuiteListResponse,
  ScenarioSuiteResponse,
  TargetInstance,
} from '@/types'

import ScenarioCatalog from './ScenarioCatalog'

interface LaunchSuiteDialogStubProps {
  suite: ScenarioSuiteResponse
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  operationId: string | null
  defaultsReady: boolean
  onSuiteChanged: () => void
}

jest.mock('@/services/api', () => ({
  scenariosApi: {
    estimateRun: jest.fn(),
    listCatalog: jest.fn(),
  },
  scenarioPresetsApi: {
    list: jest.fn(),
  },
  scenarioSuitesApi: {
    list: jest.fn(),
  },
}))

jest.mock('@/components/ScenarioSuites/LaunchSuiteDialog', () => ({
  __esModule: true,
  default: ({
    suite,
    targets,
    defaultObjectiveTarget,
    defaultAdversarialTarget,
    labels,
    operationId,
    defaultsReady,
    onSuiteChanged,
  }: LaunchSuiteDialogStubProps) => (
    <div
      role="dialog"
      aria-label={`Launch ${suite.suite.name}`}
      data-targets={targets.map((target) => target.target_registry_name).join(',')}
      data-objective-target={defaultObjectiveTarget?.target_registry_name ?? 'none'}
      data-adversarial-target={defaultAdversarialTarget?.target_registry_name ?? 'none'}
      data-labels={JSON.stringify(labels)}
      data-operation-id={operationId ?? 'none'}
      data-defaults-ready={String(defaultsReady)}
    >
      <button type="button" onClick={onSuiteChanged}>Report suite change</button>
    </div>
  ),
}))

const mockListCatalog = scenariosApi.listCatalog as jest.Mock
const mockEstimateRun = scenariosApi.estimateRun as jest.Mock
const mockListPresets = scenarioPresetsApi.list as jest.Mock
const mockListSuites = scenarioSuitesApi.list as jest.Mock

const OBJECTIVE_TARGET = makeTarget({ target_registry_name: 'objective-target' })

const CATALOG_PROPS: React.ComponentProps<typeof ScenarioCatalog> = {
  targets: [OBJECTIVE_TARGET],
  defaultObjectiveTarget: OBJECTIVE_TARGET,
  defaultAdversarialTarget: null,
  labels: { operator: 'red-team' },
  operationId: 'operation-1',
  defaultsReady: true,
}

const REMOVED_NORMAL_ESTIMATE_LABELS = new RegExp(
  [
    ['Run', 'size', 'calculated'].join(' '),
    ['Final', 'count', 'set', 'at', 'launch'].join(' '),
  ].join('|'),
  'i',
)

function LocationProbe() {
  const location = useLocation()
  return <output aria-label="Current route">{location.pathname}</output>
}

function TestWrapper({ children }: { children: React.ReactNode }) {
  return (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter>
        {children}
        <LocationProbe />
      </MemoryRouter>
    </FluentProvider>
  )
}

function makeScenario(overrides: Partial<RegisteredScenario> & { scenario_name: string }): RegisteredScenario {
  const description = overrides.description ?? 'A demo scenario.'
  const defaultTechnique = overrides.default_technique ?? 'default_technique'
  return {
    scenario_type: 'DemoScenario',
    scenario_version: 1,
    aggregate_techniques: [],
    aggregate_technique_expansions: {},
    all_techniques: ['default_technique'],
    technique_summaries: [
      {
        name: 'default_technique',
        description: 'Runs the default attack.',
        tags: ['default'],
      },
    ],
    default_datasets: [],
    dataset_size_limit: {
      default_scope: 'none',
      default_count: null,
      override_scope: 'per_dataset',
    },
    baseline_policy: 'enabled',
    include_baseline_by_default: true,
    uses_default_adversarial_target: true,
    supported_parameters: [],
    supported_parameter_names: [],
    default_run_size: {
      dataset_size: { kind: 'indeterminate', detail: 'Population configuration is not available.' },
      dataset_limit: { state: 'scenario_default' },
      estimated_attack_count: null,
      minimum_attack_count: null,
      maximum_attack_count: null,
      components: [],
      datasets: [],
      note: 'Default sizing is not available.',
    },
    ...overrides,
    description,
    description_markdown: overrides.description_markdown ?? description,
    default_technique: defaultTechnique,
    default_techniques: overrides.default_techniques ?? [defaultTechnique],
  }
}

function catalogPage(items: RegisteredScenario[]): ListRegisteredScenariosResponse {
  return { items, pagination: { limit: 200, has_more: false } }
}

function makePreset(preset: ScenarioPreset, issues: PresetIssue[] = []): ScenarioPresetResponse {
  return { preset, origin: 'stored', version: 'v1', issues }
}

function presetList(items: ScenarioPresetResponse[]): ScenarioPresetListResponse {
  return { source: '', items }
}

function makeSuite(name: string, presetNames: string[], description: string | null = null): ScenarioSuiteResponse {
  const entries = presetNames.map((presetName, index) => ({
    entry_id: `${name}-entry-${index + 1}`,
    preset_name: presetName,
  }))
  return {
    suite: { name, schema_version: 1, description, entries },
    origin: 'stored',
    version: 'v1',
    entry_statuses: entries.map((entry): ScenarioSuiteEntryStatus => ({
      ...entry,
      origin: 'stored',
      preset_version: 'v1',
      issues: [],
    })),
  }
}

function suiteList(items: ScenarioSuiteResponse[]): ScenarioSuiteListResponse {
  return { source: '', items }
}

function makeEstimate(attacks: number): ScenarioRunSizeEstimateResponse {
  return {
    dataset_size: { kind: 'bounded', value: attacks },
    dataset_limit: { state: 'scenario_default' },
    estimated_attack_count: attacks,
    components: [{ label: 'Configured attacks', count: attacks, is_baseline: false, note: null }],
    datasets: [],
    note: null,
  }
}

function rowTestIds(): (string | null)[] {
  return screen.queryAllByTestId(/^(suite|scenario|preset)-card-/).map((row) => row.getAttribute('data-testid'))
}

async function waitForRunSizes(): Promise<void> {
  await waitFor(() => expect(screen.queryAllByText('Calculating...')).toHaveLength(0))
}

describe('ScenarioCatalog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockListSuites.mockResolvedValue(suiteList([]))
    mockListPresets.mockResolvedValue(presetList([]))
    mockEstimateRun.mockResolvedValue(makeEstimate(6))
  })

  it('shows a loading state while fetching', () => {
    mockListCatalog.mockReturnValue(new Promise(() => {}))
    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)
    expect(screen.getByText('Loading scenarios...')).toBeInTheDocument()
  })

  it('renders every scenario from a single page', async () => {
    mockListCatalog.mockResolvedValue({
      items: [
        makeScenario({ scenario_name: 'foundry.red_team_agent', description: 'Red teams a target.' }),
        makeScenario({ scenario_name: 'encoding.base64', description: 'Encodes prompts.' }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findByText('foundry.red_team_agent')).toBeInTheDocument()
    expect(screen.getByText('encoding.base64')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Scanner' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Scanner' })).toHaveAttribute(
      'href',
      'https://microsoft.github.io/PyRIT/latest/scanner/scanner/',
    )
    expect(
      screen.getByText('Launch comprehensive testing campaigns with multiple attack techniques against a target.'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Browse registered scenarios/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Read the scanner documentation/i)).not.toBeInTheDocument()
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(2))
  })

  it('renders metadata while default estimates are calculated', async () => {
    let resolveEstimates: ((value: {
      items: RegisteredScenario[]
      pagination: { limit: number; has_more: boolean }
    }) => void) | undefined
    const pendingScenario = makeScenario({
      scenario_name: 'scenario.pending',
      default_datasets: ['harmbench'],
    })
    const estimatedScenario = makeScenario({
      scenario_name: 'scenario.pending',
      default_datasets: ['harmbench'],
      default_run_size: {
        dataset_size: { kind: 'bounded', value: 4 },
        dataset_limit: { state: 'scenario_default' },
        estimated_attack_count: 4,
        minimum_attack_count: null,
        maximum_attack_count: null,
        components: [{ label: 'Default', count: 4, is_baseline: false, note: null }],
        datasets: [
          {
            name: 'harmbench',
            kind: 'dataset',
            logical_seed_group_count: 400,
            selected_seed_group_count: 4,
            configured_caps: [],
            selection_note: null,
          },
        ],
        note: null,
      },
    })
    mockListCatalog
      .mockResolvedValueOnce({
        items: [pendingScenario],
        pagination: { limit: 200, has_more: false },
      })
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveEstimates = resolve
      }))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findByText('scenario.pending')).toBeInTheDocument()
    expect(screen.getAllByText('Calculating...')).toHaveLength(1)
    expect(screen.getByText('harmbench')).toBeInTheDocument()
    expect(mockListCatalog).toHaveBeenNthCalledWith(1, 200, undefined, false)

    await act(async () => {
      resolveEstimates?.({
        items: [estimatedScenario],
        pagination: { limit: 200, has_more: false },
      })
    })

    expect(await screen.findByText('4 attacks')).toBeInTheDocument()
    expect(screen.queryByText('Calculating...')).not.toBeInTheDocument()
  })

  it('keeps metadata visible when default estimates fail', async () => {
    mockListCatalog
      .mockResolvedValueOnce({
        items: [makeScenario({ scenario_name: 'scenario.unsized' })],
        pagination: { limit: 200, has_more: false },
      })
      .mockRejectedValueOnce(new Error('estimate failed'))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findByText('scenario.unsized')).toBeInTheDocument()
    expect(await screen.findByText(/Default run sizes could not be calculated/)).toBeInTheDocument()
    expect(screen.queryByText('Calculating...')).not.toBeInTheDocument()
  })

  it('renders Markdown descriptions and lets users expand long previews', async () => {
    const user = userEvent.setup()
    const clientHeight = jest.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(60)
    const scrollHeight = jest.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(120)
    mockListCatalog.mockResolvedValue({
      items: [
        makeScenario({
          scenario_name: 'scenario.markdown',
          description_markdown: [
            'Tests **formatted text** and ``inline_code``.',
            '',
            'This additional detail makes the description long enough to collapse in the catalog preview. ',
            'Users can expand the row to read the complete scenario purpose without leaving the catalog.',
          ].join('\n'),
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const row = await screen.findByTestId('scenario-card-scenario.markdown')
    expect(within(row).getByText('formatted text').tagName).toBe('STRONG')
    expect(within(row).getByText('inline_code').tagName).toBe('CODE')

    const expandButton = await within(row).findByRole('button', {
      name: 'Expand row details for scenario.markdown',
    })
    expect(expandButton).toHaveAttribute('aria-expanded', 'false')

    await user.click(expandButton)

    expect(within(row).getByRole('button', {
      name: 'Collapse row details for scenario.markdown',
    })).toHaveAttribute('aria-expanded', 'true')
    clientHeight.mockRestore()
    scrollHeight.mockRestore()
  })

  it('does not show an expand control for a fully visible description', async () => {
    const clientHeight = jest.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(60)
    const scrollHeight = jest.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(61)
    mockListCatalog.mockResolvedValue({
      items: [makeScenario({ scenario_name: 'scenario.visible' })],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const row = await screen.findByTestId('scenario-card-scenario.visible')
    expect(within(row).queryByRole('button', {
      name: 'Expand row details for scenario.visible',
    })).not.toBeInTheDocument()
    clientHeight.mockRestore()
    scrollHeight.mockRestore()
  })

  it('ignores a catalog response that resolves after unmount', async () => {
    let resolveRequest: ((value: {
      items: RegisteredScenario[]
      pagination: { limit: number; has_more: boolean }
    }) => void) | undefined
    mockListCatalog.mockImplementationOnce(() => new Promise((resolve) => {
      resolveRequest = resolve
    }))

    const { unmount } = render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(1))
    unmount()
    await act(async () => {
      resolveRequest?.({
        items: [makeScenario({ scenario_name: 'late.scenario' })],
        pagination: { limit: 200, has_more: false },
      })
    })
  })

  it('ignores a catalog failure that arrives after unmount', async () => {
    let rejectRequest: ((reason?: unknown) => void) | undefined
    mockListCatalog.mockImplementationOnce(() => new Promise((_resolve, reject) => {
      rejectRequest = reject
    }))

    const { unmount } = render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(1))
    unmount()
    await act(async () => {
      rejectRequest?.(new Error('late failure'))
    })
  })

  it('renders the exact launch-index column order and applies spacing to every cell', async () => {
    mockListCatalog.mockResolvedValue({
      items: [makeScenario({ scenario_name: 'foundry.red_team_agent' })],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const table = await screen.findByRole('table', { name: 'Individual scanners' })
    expect(
      screen.getByText('Launch comprehensive testing campaigns with multiple attack techniques against a target.'),
    ).toBeInTheDocument()
    const headers = within(table).getAllByRole('columnheader')
    expect(headers).toHaveLength(4)
    expect(headers.map((header) => header.textContent)).toEqual([
      'Scenario / purpose',
      'Default datasets',
      'Default techniques',
      'Default run size',
    ])
    expect(headers.every((cell) => cell.classList.contains('scenario-catalog-cell-padding'))).toBe(true)
    const cells = within(screen.getByTestId('scenario-card-foundry.red_team_agent')).getAllByRole('cell')
    expect(cells).toHaveLength(4)
    expect(cells.every((cell) => cell.classList.contains('scenario-catalog-cell-padding'))).toBe(true)
    expect(within(cells[0]).getByRole('link', { name: 'foundry.red_team_agent' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /show details|hide details/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /details/i })).not.toBeInTheDocument()
  })

  it('follows the cursor to load every page automatically', async () => {
    mockListCatalog
      .mockResolvedValueOnce({
        items: [makeScenario({ scenario_name: 'scenario.page1' })],
        pagination: { limit: 1, has_more: true, next_cursor: 'cursor-1' },
      })
      .mockResolvedValueOnce({
        items: [makeScenario({ scenario_name: 'scenario.page2' })],
        pagination: { limit: 1, has_more: false },
      })
      .mockResolvedValueOnce({
        items: [makeScenario({ scenario_name: 'scenario.page1' })],
        pagination: { limit: 1, has_more: true, next_cursor: 'cursor-1' },
      })
      .mockResolvedValueOnce({
        items: [makeScenario({ scenario_name: 'scenario.page2' })],
        pagination: { limit: 1, has_more: false },
      })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findByText('scenario.page1')).toBeInTheDocument()
    expect(screen.getByText('scenario.page2')).toBeInTheDocument()
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(4))
    expect(mockListCatalog).toHaveBeenNthCalledWith(2, 200, 'cursor-1', false)
    expect(mockListCatalog).toHaveBeenNthCalledWith(3, 200, undefined)
    expect(mockListCatalog).toHaveBeenNthCalledWith(4, 200, 'cursor-1')
  })

  it('stops paging if the backend repeats a cursor instead of looping forever', async () => {
    mockListCatalog.mockResolvedValue({
      items: [makeScenario({ scenario_name: 'scenario.loop' })],
      pagination: { limit: 1, has_more: true, next_cursor: 'same-cursor' },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findAllByText('scenario.loop')).toHaveLength(1)
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(4))
    // Give any additional (incorrect) fetch a chance to fire before asserting it didn't.
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(mockListCatalog).toHaveBeenCalledTimes(4)
  })

  it('shows an empty state when no scenarios are registered', async () => {
    mockListCatalog.mockResolvedValueOnce({ items: [], pagination: { limit: 200, has_more: false } })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findByTestId('empty-state')).toBeInTheDocument()
  })

  it('shows an error MessageBar with a retry action on failure', async () => {
    mockListCatalog.mockRejectedValueOnce(new Error('Network error — check that the backend is running and reachable.'))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findByTestId('error-state')).toBeInTheDocument()
    expect(screen.getByText(/Network error/)).toBeInTheDocument()
    expect(screen.getByTestId('retry-btn')).toBeInTheDocument()
  })

  it('retries the fetch when Retry is clicked', async () => {
    const user = userEvent.setup()
    mockListCatalog
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValue({
        items: [makeScenario({ scenario_name: 'scenario.recovered' })],
        pagination: { limit: 200, has_more: false },
      })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    await screen.findByTestId('error-state')
    await user.click(screen.getByTestId('retry-btn'))

    expect(await screen.findByText('scenario.recovered')).toBeInTheDocument()
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(3))
  })

  it('filters scenarios by the search box across name, description, techniques, and datasets', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValue({
      items: [
        makeScenario({ scenario_name: 'foundry.red_team_agent', description: 'Red teams a target.' }),
        makeScenario({
          scenario_name: 'encoding.base64',
          description: 'Applies text encodings.',
          default_datasets: ['harmbench'],
          default_technique: 'multi_turn',
          default_techniques: ['crescendo'],
          aggregate_techniques: ['multi_turn'],
          aggregate_technique_expansions: { multi_turn: ['crescendo'] },
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    await screen.findByText('foundry.red_team_agent')

    await user.type(screen.getByLabelText('Search scenarios'), 'Multi-turn')

    expect(screen.queryByText('foundry.red_team_agent')).not.toBeInTheDocument()
    expect(screen.getByText('encoding.base64')).toBeInTheDocument()
  })

  it('searches dataset metadata and renders singular counts with no default techniques', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValue({
      items: [
        makeScenario({
          scenario_name: 'scenario.one',
          default_techniques: [],
          default_datasets: ['dataset-one'],
          default_run_size: {
            dataset_size: { kind: 'bounded', value: 1 }, dataset_limit: { state: 'value', value: 1 },
            estimated_attack_count: null,
            components: [],
            datasets: [{
              name: 'dataset-one',
              kind: 'dataset',
              logical_seed_group_count: 1,
              selected_seed_group_count: 1,
              configured_caps: [],
              selection_note: null,
            }],
            note: null,
          },
        }),
        makeScenario({
          scenario_name: 'scenario.two',
          default_datasets: ['dataset-two'],
          default_run_size: {
            dataset_size: { kind: 'indeterminate', detail: 'Population configuration is not available.' },
            dataset_limit: { state: 'scenario_default' },
            estimated_attack_count: null,
            components: [],
            datasets: [{
              name: 'dataset-two',
              kind: 'dataset',
              logical_seed_group_count: 2,
              selected_seed_group_count: 2,
              configured_caps: [],
              selection_note: 'Dataset metadata is searchable.',
            }],
            note: null,
          },
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)
    await screen.findByText('scenario.one')
    await user.type(screen.getByLabelText('Search scenarios'), 'dataset')

    const firstRow = screen.getByTestId('scenario-card-scenario.one')
    expect(within(firstRow).getByText('Up to 1 objective')).toBeInTheDocument()
    expect(within(firstRow).getByText(/dataset-one/)).toBeInTheDocument()
    expect(within(firstRow).getByText('No default techniques')).toBeInTheDocument()
    expect(screen.getByText('scenario.two')).toBeInTheDocument()
  })

  it('shows a no-results state when the search matches nothing', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValue({
      items: [makeScenario({ scenario_name: 'foundry.red_team_agent' })],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)
    await screen.findByText('foundry.red_team_agent')

    await user.type(screen.getByLabelText('Search scenarios'), 'no-such-scenario')

    expect(await screen.findByTestId('no-results-state')).toBeInTheDocument()
  })

  it('links each card to its encoded scenario detail route', async () => {
    mockListCatalog.mockResolvedValue({
      items: [makeScenario({ scenario_name: 'foundry/red_team_agent' })],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const card = await screen.findByRole('link', { name: /foundry\/red_team_agent/i })
    expect(card).toHaveAttribute('href', '/scanner/foundry%2Fred_team_agent')
  })

  it('shows configured limits rather than known population counts', async () => {
    mockListCatalog.mockResolvedValue({
      items: [
        makeScenario({
          scenario_name: 'scenario.compound',
          default_datasets: ['population-a', 'population-b'],
          dataset_size_limit: {
            default_scope: 'none',
            default_count: null,
            override_scope: 'per_dataset',
          },
          default_run_size: {
            dataset_size: { kind: 'bounded', value: 10 }, dataset_limit: { state: 'value', value: 10 },
            estimated_attack_count: null,
            components: [],
            datasets: [
              {
                name: 'population-a',
                kind: 'dataset',
                logical_seed_group_count: 100,
                selected_seed_group_count: 4,
                configured_caps: [],
                selection_note: null,
              },
              {
                name: 'population-b',
                kind: 'synthesized',
                logical_seed_group_count: 20,
                selected_seed_group_count: 2,
                configured_caps: [],
                selection_note: null,
              },
            ],
            note: null,
          },
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const row = await screen.findByTestId('scenario-card-scenario.compound')
    expect(within(row).getByText('Up to 10 objectives')).toBeInTheDocument()
    expect(within(row).getByText('population-a · population-b')).toBeInTheDocument()
  })

  it('shows an adaptive estimate as a plain attack range', async () => {
    mockListCatalog.mockResolvedValue({
      items: [
        makeScenario({
          scenario_name: 'adaptive.text_adaptive',
          default_run_size: {
            dataset_size: { kind: 'indeterminate', detail: 'Population configuration is not available.' },
            dataset_limit: { state: 'scenario_default' },
            estimated_attack_count: null,
            minimum_attack_count: 21,
            maximum_attack_count: 42,
            components: [
              {
                label: 'Baseline',
                count: 21,
                is_baseline: true,
                note: null,
              },
              {
                label: 'Adaptive objectives',
                count: 21,
                is_baseline: false,
                note: null,
              },
            ],
            datasets: [],
            note: null,
          },
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const row = await screen.findByTestId('scenario-card-adaptive.text_adaptive')
    expect(within(row).getByText('21-42 attacks')).toBeInTheDocument()
    expect(within(row).queryByText(/progress units|attack attempts/i)).not.toBeInTheDocument()
  })

  it('keeps declared datasets visible when backend population summaries are unavailable', async () => {
    mockListCatalog.mockResolvedValue({
      items: [
        makeScenario({
          scenario_name: 'scenario.unsized',
          default_datasets: ['harmbench'],
          dataset_size_limit: {
            default_scope: 'none',
            default_count: null,
            override_scope: 'per_dataset',
          },
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)
    const row = await screen.findByTestId('scenario-card-scenario.unsized')
    expect(within(row).queryByText('Population counts unavailable')).not.toBeInTheDocument()
    expect(within(row).getByText('harmbench')).toBeInTheDocument()
  })

  it('keeps the authoritative default comparison values in the launch row', async () => {
    mockListCatalog.mockResolvedValue({
      items: [
        makeScenario({
          scenario_name: 'airt.jailbreak',
          scenario_version: 4,
          default_technique: 'default',
          default_techniques: ['prompt_sending', 'jailbreak_system_prompt'],
          aggregate_techniques: ['default', 'easy'],
          aggregate_technique_expansions: {
            default: ['prompt_sending', 'jailbreak_system_prompt'],
            easy: ['prompt_sending'],
          },
          all_techniques: ['prompt_sending', 'jailbreak_system_prompt', 'flip'],
          default_datasets: ['harmbench'],
          dataset_size_limit: {
            default_scope: 'none',
            default_count: null,
            override_scope: 'per_dataset',
          },
          default_run_size: {
            estimated_attack_count: null,
            minimum_attack_count: 12,
            maximum_attack_count: 20,
            dataset_size: { kind: 'bounded', value: 4 }, dataset_limit: { state: 'value', value: 4 },
            components: [
              {
                label: 'Default attacks',
                count: 20,
                is_baseline: false,
                note: null,
              },
            ],
            datasets: [
              {
                name: 'harmbench',
                kind: 'dataset',
                logical_seed_group_count: 400,
                selected_seed_group_count: 4,
                configured_caps: [
                  {
                    label: 'Jailbreak templates',
                    count: 2,
                    configured_on: 'configuration',
                    dataset_name: null,
                  },
                ],
                selection_note: 'One incompatible group is excluded.',
              },
            ],
            note: 'Retries and internal turns are excluded.',
          },
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const row = await screen.findByTestId('scenario-card-airt.jailbreak')
    expect(within(row).getByText('Up to 4 objectives')).toBeInTheDocument()
    expect(within(row).getByText('harmbench')).toBeInTheDocument()
    expect(within(row).getByText('2 techniques')).toBeInTheDocument()
    expect(within(row).getByText('prompt_sending · jailbreak_system_prompt')).toBeInTheDocument()
    expect(within(row).getByText('12-20 attacks')).toBeInTheDocument()
    expect(within(row).queryByText('default')).not.toBeInTheDocument()
    expect(within(row).queryByText(/aggregate presets|compatible concrete/i)).not.toBeInTheDocument()
    expect(within(row).queryByText(REMOVED_NORMAL_ESTIMATE_LABELS)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /show details|hide details/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /details/i })).not.toBeInTheDocument()
  })

  it('merges listed presets into the scenario list in name order', async () => {
    mockListCatalog.mockResolvedValue(catalogPage([
      makeScenario({ scenario_name: 'zeta.scenario' }),
      makeScenario({ scenario_name: 'alpha.scenario' }),
    ]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'omega-preset', scenario_name: 'zeta.scenario' }),
      makePreset({ name: 'beta-preset', scenario_name: 'alpha.scenario' }),
      makePreset({ name: 'Zulu-preset', scenario_name: 'alpha.scenario' }),
    ]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    await screen.findByTestId('preset-card-omega-preset')
    expect(rowTestIds()).toEqual([
      'preset-card-Zulu-preset',
      'scenario-card-alpha.scenario',
      'preset-card-beta-preset',
      'preset-card-omega-preset',
      'scenario-card-zeta.scenario',
    ])
    expect(screen.getByRole('table', { name: 'Individual scanners' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Individual scanners' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('scanner-suites')).not.toBeInTheDocument()
    expect(mockListSuites).toHaveBeenCalledWith(expect.any(AbortSignal))
    expect(mockListPresets).toHaveBeenCalledWith(expect.any(AbortSignal), false)

    await waitForRunSizes()
    expect(mockListCatalog).toHaveBeenCalledTimes(2)
    expect(mockListCatalog).toHaveBeenNthCalledWith(1, 200, undefined, false)
    expect(mockListCatalog).toHaveBeenNthCalledWith(2, 200, undefined)
    expect(mockEstimateRun).toHaveBeenCalledTimes(3)
  })

  it('keeps a preset named after its scenario as a separate scanner row', async () => {
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'garak.encoding' })]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'garak.encoding', scenario_name: 'garak.encoding' }),
    ]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const presetRow = await screen.findByTestId('preset-card-garak.encoding')
    expect(rowTestIds()).toEqual(['scenario-card-garak.encoding', 'preset-card-garak.encoding'])
    expect(
      within(screen.getByTestId('scenario-card-garak.encoding')).getByRole('link', { name: 'garak.encoding' }),
    ).toHaveAttribute('href', '/scanner/garak.encoding')
    expect(within(presetRow).getByRole('link', { name: 'garak.encoding' })).toHaveAttribute(
      'href',
      '/scanner/garak.encoding?preset=garak.encoding',
    )
    await waitForRunSizes()
  })

  it('leaves presets hidden from the Scanner out of the list while suites still describe them', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValue(catalogPage([
      makeScenario({ scenario_name: 'airt.jailbreak' }),
      makeScenario({ scenario_name: 'garak.encoding' }),
    ]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'quick-jailbreak', scenario_name: 'airt.jailbreak' }),
      makePreset({ name: 'hidden-encoding', scenario_name: 'garak.encoding', show_in_scanner: false }),
    ]))
    mockListSuites.mockResolvedValue(suiteList([makeSuite('nightly', ['quick-jailbreak', 'hidden-encoding'])]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const presetRow = await screen.findByTestId('preset-card-quick-jailbreak')
    expect(await within(presetRow).findByText('6 attacks')).toBeInTheDocument()
    await waitForRunSizes()
    expect(screen.queryByTestId('preset-card-hidden-encoding')).not.toBeInTheDocument()
    expect(mockEstimateRun).toHaveBeenCalledTimes(1)
    expect(mockEstimateRun).toHaveBeenCalledWith('airt.jailbreak', expect.anything(), expect.any(AbortSignal))

    await user.click(screen.getByRole('button', { name: 'Show scenarios in nightly' }))

    const members = screen.getByRole('list', { name: 'Scenarios in nightly' })
    expect(within(members).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'quick-jailbreak · Runs airt.jailbreak',
      'hidden-encoding · Runs garak.encoding',
    ])
  })

  it('links a preset to its pre-filled scenario form and leaves an orphaned preset unlinked', async () => {
    const missingScenario: PresetIssue[] = [
      { field: 'scenario_name', message: 'Scenario retired.scenario is not registered.' },
    ]
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'foundry/red_team_agent' })]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'quick & safe', scenario_name: 'foundry/red_team_agent' }),
      makePreset({ name: 'orphaned', scenario_name: 'retired.scenario', techniques: ['flip'] }, missingScenario),
      makePreset({ name: 'orphaned-defaults', scenario_name: 'retired.scenario' }, missingScenario),
    ]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const linkedRow = await screen.findByTestId('preset-card-quick & safe')
    expect(within(linkedRow).getByRole('link', { name: 'quick & safe' })).toHaveAttribute(
      'href',
      '/scanner/foundry%2Fred_team_agent?preset=quick%20%26%20safe',
    )
    expect(within(linkedRow).getByText('Preset')).toBeInTheDocument()
    expect(within(linkedRow).getByText('Runs foundry/red_team_agent')).toBeInTheDocument()

    const orphanedRow = screen.getByTestId('preset-card-orphaned')
    expect(within(orphanedRow).queryByRole('link')).not.toBeInTheDocument()
    expect(within(orphanedRow).getByText('orphaned')).toBeInTheDocument()
    expect(within(orphanedRow).getByText('Runs retired.scenario')).toBeInTheDocument()
    expect(within(orphanedRow).getByText('1 technique')).toBeInTheDocument()
    expect(within(orphanedRow).getByText('flip')).toBeInTheDocument()
    expect(within(orphanedRow).getByText('No default datasets')).toBeInTheDocument()

    const defaultsRow = screen.getByTestId('preset-card-orphaned-defaults')
    expect(within(defaultsRow).queryByRole('link')).not.toBeInTheDocument()
    expect(within(defaultsRow).getByText('No default techniques')).toBeInTheDocument()
    expect(within(defaultsRow).getByText('No default datasets')).toBeInTheDocument()
    await waitForRunSizes()
  })

  it('shows a spinner until the preset estimate arrives and then the preset run size', async () => {
    const estimate = deferred<ScenarioRunSizeEstimateResponse>()
    mockListCatalog.mockResolvedValue(catalogPage([
      makeScenario({ scenario_name: 'airt.jailbreak', default_datasets: ['harmbench'] }),
    ]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({
        name: 'quick-jailbreak',
        scenario_name: 'airt.jailbreak',
        techniques: ['flip'],
        datasets: { sources: [{ name: 'advbench' }] },
        include_baseline: false,
        scenario_params: { max_turns: 2 },
      }),
    ]))
    mockEstimateRun.mockReturnValueOnce(estimate.promise)

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const row = await screen.findByTestId('preset-card-quick-jailbreak')
    expect(within(row).getByText('Calculating...')).toBeInTheDocument()
    expect(within(row).getByText('advbench')).toBeInTheDocument()
    expect(within(row).queryByText('Not runnable here')).not.toBeInTheDocument()
    expect(screen.queryByTestId('preset-issues-quick-jailbreak')).not.toBeInTheDocument()
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledWith(
      'airt.jailbreak',
      {
        techniques: ['flip'],
        datasets: { sources: [{ name: 'advbench' }] },
        include_baseline: false,
        scenario_params: { max_turns: 2 },
      },
      expect.any(AbortSignal),
    ))

    await act(async () => {
      estimate.resolve({
        ...makeEstimate(6),
        datasets: [{
          name: 'advbench',
          kind: 'dataset',
          logical_seed_group_count: 520,
          selected_seed_group_count: 6,
          configured_caps: [],
          selection_note: null,
        }],
      })
    })

    expect(within(row).getByText('6 attacks')).toBeInTheDocument()
    expect(within(row).getByText('Up to 6 objectives')).toBeInTheDocument()
    expect(within(row).getByText('advbench')).toBeInTheDocument()
    expect(within(row).queryByText('Calculating...')).not.toBeInTheDocument()
  })

  it('names the estimated datasets when neither the preset nor its scenario declares any', async () => {
    const estimate: ScenarioRunSizeEstimateResponse = {
      ...makeEstimate(12),
      dataset_size: { kind: 'all_available' },
      datasets: [{
        name: 'garak_prompts',
        kind: 'dataset',
        logical_seed_group_count: 12,
        selected_seed_group_count: 12,
        configured_caps: [],
        selection_note: null,
      }],
    }
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'garak.encoding' })]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'encoding-sweep', scenario_name: 'garak.encoding', datasets: { sources: [] } }),
    ]))
    mockEstimateRun.mockResolvedValueOnce(estimate)

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const row = await screen.findByTestId('preset-card-encoding-sweep')
    expect(await within(row).findByText('12 attacks')).toBeInTheDocument()
    expect(within(row).getByText('All available data (configured child limits still apply)')).toBeInTheDocument()
    expect(within(row).getByText('garak_prompts')).toBeInTheDocument()
  })

  it('flags a preset that cannot run here and skips its estimate', async () => {
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'airt.jailbreak' })]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset(
        { name: 'stale-preset', scenario_name: 'airt.jailbreak', techniques: ['retired_technique'] },
        [
          { field: 'techniques', message: 'Technique retired_technique is not available.' },
          { field: 'datasets', message: 'Dataset gone_dataset is not registered.' },
        ],
      ),
    ]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const row = await screen.findByTestId('preset-card-stale-preset')
    expect(within(row).getByText('Not runnable here')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('preset-issues-stale-preset')).getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      'Technique retired_technique is not available.',
      'Dataset gone_dataset is not registered.',
    ])
    expect(within(row).getByText('retired_technique')).toBeInTheDocument()
    expect(await within(row).findByText('—')).toBeInTheDocument()
    expect(mockEstimateRun).not.toHaveBeenCalled()
  })

  it('expands pinned technique sets and falls back to the scenario defaults', async () => {
    mockListCatalog.mockResolvedValue(catalogPage([
      makeScenario({
        scenario_name: 'airt.jailbreak',
        default_technique: 'default',
        default_techniques: ['prompt_sending', 'jailbreak_system_prompt'],
        aggregate_techniques: ['default', 'multi_turn'],
        aggregate_technique_expansions: { multi_turn: ['crescendo', 'red_teaming'] },
        default_datasets: ['harmbench'],
      }),
    ]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({
        name: 'deep',
        scenario_name: 'airt.jailbreak',
        description: 'Multi-turn sweep over two datasets.',
        techniques: ['multi_turn', 'crescendo', 'flip'],
        datasets: { sources: [{ name: 'advbench' }, { name: 'xstest' }] },
      }),
      makePreset({ name: 'recommended', scenario_name: 'airt.jailbreak', techniques: ['default'] }),
      makePreset({ name: 'unpinned', scenario_name: 'airt.jailbreak', techniques: [], datasets: { sources: [] } }),
    ]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const deepRow = await screen.findByTestId('preset-card-deep')
    expect(within(deepRow).getByText('3 techniques')).toBeInTheDocument()
    expect(within(deepRow).getByText('crescendo · red_teaming · flip')).toBeInTheDocument()
    expect(within(deepRow).getByText('advbench · xstest')).toBeInTheDocument()
    expect(screen.getByTestId('preset-description-deep')).toHaveTextContent('Multi-turn sweep over two datasets.')

    const recommendedRow = screen.getByTestId('preset-card-recommended')
    expect(within(recommendedRow).getByText('2 techniques')).toBeInTheDocument()
    expect(within(recommendedRow).getByText('prompt_sending · jailbreak_system_prompt')).toBeInTheDocument()
    expect(within(recommendedRow).getByText('harmbench')).toBeInTheDocument()

    const unpinnedRow = screen.getByTestId('preset-card-unpinned')
    expect(within(unpinnedRow).getByText('prompt_sending · jailbreak_system_prompt')).toBeInTheDocument()
    expect(within(unpinnedRow).getByText('harmbench')).toBeInTheDocument()
    expect(screen.queryByTestId('preset-description-unpinned')).not.toBeInTheDocument()
    await waitForRunSizes()
  })

  it('lists suites ahead of the individual scanners under their own headings', async () => {
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'airt.jailbreak' })]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'quick-jailbreak', scenario_name: 'airt.jailbreak' }),
    ]))
    mockListSuites.mockResolvedValue(suiteList([
      makeSuite('weekly', ['quick-jailbreak']),
      makeSuite('nightly', ['quick-jailbreak'], 'Runs every night.'),
    ]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    await screen.findByTestId('suite-card-nightly')
    expect(rowTestIds()).toEqual([
      'suite-card-nightly',
      'suite-card-weekly',
      'scenario-card-airt.jailbreak',
      'preset-card-quick-jailbreak',
    ])
    expect(screen.getByRole('region', { name: 'Scenario suites' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Individual scanners' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Individual scanners' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Individual scanners' })).toBeInTheDocument()
    expect(within(screen.getByTestId('suite-card-nightly')).getByText('Runs every night.')).toBeInTheDocument()
    expect(screen.getByTestId('launch-suite-nightly')).toBeEnabled()
    await waitForRunSizes()
  })

  it('still lists scenarios when suites and presets fail to load', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'airt.jailbreak' })]))
    mockListSuites
      .mockRejectedValueOnce(new Error('suite store offline'))
      .mockResolvedValue(suiteList([makeSuite('nightly', ['quick-jailbreak'])]))
    mockListPresets
      .mockRejectedValueOnce(new Error('preset store offline'))
      .mockResolvedValue(presetList([makePreset({ name: 'quick-jailbreak', scenario_name: 'airt.jailbreak' })]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findByTestId('scenario-card-airt.jailbreak')).toBeInTheDocument()
    expect(screen.getByText('Scenario suites could not be loaded: suite store offline')).toBeInTheDocument()
    expect(screen.getByText('Scenario presets could not be loaded: preset store offline')).toBeInTheDocument()
    expect(screen.queryByTestId('error-state')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scanner-suites')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Individual scanners' })).not.toBeInTheDocument()
    expect(mockEstimateRun).not.toHaveBeenCalled()

    const refreshButton = screen.getByRole('button', { name: 'Refresh' })
    await waitFor(() => expect(refreshButton).toBeEnabled())
    await user.click(refreshButton)

    expect(await screen.findByTestId('suite-card-nightly')).toBeInTheDocument()
    expect(screen.getByTestId('preset-card-quick-jailbreak')).toBeInTheDocument()
    expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument()
    await waitForRunSizes()
  })

  it('shows only the error when the scenario catalog fails even though suites and presets load', async () => {
    mockListCatalog.mockRejectedValueOnce(new Error('catalog offline'))
    mockListSuites.mockResolvedValue(suiteList([makeSuite('nightly', ['quick-jailbreak'])]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'quick-jailbreak', scenario_name: 'airt.jailbreak' }),
    ]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    const errorState = await screen.findByTestId('error-state')
    expect(within(errorState).getByText('catalog offline')).toBeInTheDocument()
    expect(within(errorState).getByTestId('retry-btn')).toBeInTheDocument()
    expect(screen.queryByTestId('scanner-suites')).not.toBeInTheDocument()
    expect(screen.queryByTestId('preset-card-quick-jailbreak')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(mockListCatalog).toHaveBeenCalledTimes(1)
    expect(mockEstimateRun).not.toHaveBeenCalled()
  })

  it('shows the empty state instead of suites when no scenarios are registered', async () => {
    mockListCatalog.mockResolvedValueOnce(catalogPage([]))
    mockListSuites.mockResolvedValue(suiteList([makeSuite('nightly', ['quick-jailbreak'])]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'quick-jailbreak', scenario_name: 'airt.jailbreak' }),
    ]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findByTestId('empty-state')).toBeInTheDocument()
    expect(screen.queryByTestId('scanner-suites')).not.toBeInTheDocument()
    expect(screen.queryByTestId('preset-card-quick-jailbreak')).not.toBeInTheDocument()
    expect(mockEstimateRun).not.toHaveBeenCalled()
  })

  it('reports the presets whose run sizes could not be calculated', async () => {
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'airt.jailbreak' })]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'first-preset', scenario_name: 'airt.jailbreak' }),
      makePreset({ name: 'second-preset', scenario_name: 'airt.jailbreak' }),
      makePreset({ name: 'third-preset', scenario_name: 'airt.jailbreak' }),
    ]))
    mockEstimateRun
      .mockRejectedValueOnce(new Error('estimate timed out'))
      .mockRejectedValueOnce(new Error('dataset missing'))
      .mockResolvedValueOnce(makeEstimate(3))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    expect(await screen.findByText(
      'Preset run sizes could not be calculated: first-preset: estimate timed out; second-preset: dataset missing',
    )).toBeInTheDocument()
    expect(within(screen.getByTestId('preset-card-first-preset')).getByText('—')).toBeInTheDocument()
    expect(within(screen.getByTestId('preset-card-second-preset')).getByText('—')).toBeInTheDocument()
    expect(within(screen.getByTestId('preset-card-third-preset')).getByText('3 attacks')).toBeInTheDocument()
    expect(screen.queryByText(/Default run sizes could not be calculated/)).not.toBeInTheDocument()
    await waitForRunSizes()
  })

  it('reloads the Scanner and explains why when a suite changes during launch', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValue(catalogPage([makeScenario({ scenario_name: 'airt.jailbreak' })]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'quick-jailbreak', scenario_name: 'airt.jailbreak' }),
    ]))
    mockListSuites.mockResolvedValue(suiteList([makeSuite('nightly', ['quick-jailbreak'])]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    await user.click(await screen.findByTestId('launch-suite-nightly'))

    const dialog = screen.getByRole('dialog', { name: 'Launch nightly' })
    expect(dialog).toHaveAttribute('data-targets', 'objective-target')
    expect(dialog).toHaveAttribute('data-objective-target', 'objective-target')
    expect(dialog).toHaveAttribute('data-adversarial-target', 'none')
    expect(dialog).toHaveAttribute('data-labels', JSON.stringify({ operator: 'red-team' }))
    expect(dialog).toHaveAttribute('data-operation-id', 'operation-1')
    expect(dialog).toHaveAttribute('data-defaults-ready', 'true')

    await user.click(within(dialog).getByRole('button', { name: 'Report suite change' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('suite-changed-notice')).toHaveTextContent(
      "Suite 'nightly' or one of its presets changed while you were launching it. Review it and launch again.",
    )
    expect(await screen.findByTestId('suite-card-nightly')).toBeInTheDocument()
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(4))
    expect(mockListSuites).toHaveBeenCalledTimes(2)
    expect(mockListPresets).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('suite-changed-notice')).toBeInTheDocument()

    const refreshButton = screen.getByRole('button', { name: 'Refresh' })
    await waitFor(() => expect(refreshButton).toBeEnabled())
    await user.click(refreshButton)

    expect(screen.queryByTestId('suite-changed-notice')).not.toBeInTheDocument()
    expect(await screen.findByTestId('suite-card-nightly')).toBeInTheDocument()
    await waitForRunSizes()
  })

  it('searches suites by their presets and the scenarios those presets run', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValue(catalogPage([
      makeScenario({ scenario_name: 'airt.jailbreak', description: 'Jailbreak attempts.' }),
      makeScenario({ scenario_name: 'garak.encoding', description: 'Encoding attacks.' }),
    ]))
    mockListPresets.mockResolvedValue(presetList([
      makePreset({
        name: 'quick-jailbreak',
        scenario_name: 'airt.jailbreak',
        description: 'Fast smoke run.',
        techniques: ['multi_turn'],
        datasets: { sources: [{ name: 'advbench' }] },
      }),
      makePreset({ name: 'hidden-encoding', scenario_name: 'garak.encoding', show_in_scanner: false }),
    ]))
    mockListSuites.mockResolvedValue(suiteList([
      makeSuite('nightly', ['quick-jailbreak'], 'Runs every night.'),
      makeSuite('encoding-sweep', ['hidden-encoding', 'deleted-preset']),
    ]))

    render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    await screen.findByTestId('suite-card-nightly')
    await waitForRunSizes()
    expect(rowTestIds()).toEqual([
      'suite-card-encoding-sweep',
      'suite-card-nightly',
      'scenario-card-airt.jailbreak',
      'scenario-card-garak.encoding',
      'preset-card-quick-jailbreak',
    ])
    const search = screen.getByLabelText('Search scenarios')

    await user.type(search, 'every')
    expect(rowTestIds()).toEqual(['suite-card-nightly'])
    expect(screen.queryByRole('table', { name: 'Individual scanners' })).not.toBeInTheDocument()

    await user.clear(search)
    await user.type(search, 'hidden')
    expect(rowTestIds()).toEqual(['suite-card-encoding-sweep'])

    await user.clear(search)
    await user.type(search, 'garak')
    expect(rowTestIds()).toEqual(['suite-card-encoding-sweep', 'scenario-card-garak.encoding'])

    await user.clear(search)
    await user.type(search, 'smoke')
    expect(rowTestIds()).toEqual(['preset-card-quick-jailbreak'])
    expect(screen.queryByTestId('scanner-suites')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Individual scanners' })).toBeInTheDocument()

    await user.clear(search)
    await user.type(search, 'ADVBENCH')
    expect(rowTestIds()).toEqual(['preset-card-quick-jailbreak'])

    await user.clear(search)
    await user.type(search, 'zzz')
    expect(screen.getByTestId('no-results-state')).toHaveTextContent('No scenarios match "zzz"')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(rowTestIds()).toEqual([])
  })

  it('aborts suite, preset, and estimate requests on unmount and ignores their late results', async () => {
    const scenarioEstimates = deferred<ListRegisteredScenariosResponse>()
    const presetEstimate = deferred<ScenarioRunSizeEstimateResponse>()
    mockListCatalog
      .mockResolvedValueOnce(catalogPage([makeScenario({ scenario_name: 'airt.jailbreak' })]))
      .mockReturnValueOnce(scenarioEstimates.promise)
    mockListPresets.mockResolvedValue(presetList([
      makePreset({ name: 'quick-jailbreak', scenario_name: 'airt.jailbreak' }),
    ]))
    mockEstimateRun.mockReturnValueOnce(presetEstimate.promise)

    const { unmount } = render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    await screen.findByTestId('preset-card-quick-jailbreak')
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledTimes(1))
    const signals = [
      mockListSuites.mock.calls[0][0] as AbortSignal,
      mockListPresets.mock.calls[0][0] as AbortSignal,
      mockEstimateRun.mock.calls[0][2] as AbortSignal,
    ]
    expect(signals.map((signal) => signal.aborted)).toEqual([false, false, false])

    unmount()

    expect(signals.map((signal) => signal.aborted)).toEqual([true, true, true])
    await act(async () => {
      scenarioEstimates.resolve(catalogPage([makeScenario({ scenario_name: 'airt.jailbreak' })]))
      presetEstimate.resolve(makeEstimate(6))
    })
  })

  it('ignores a default estimate failure that arrives after unmount', async () => {
    const scenarioEstimates = deferred<ListRegisteredScenariosResponse>()
    mockListCatalog
      .mockResolvedValueOnce(catalogPage([makeScenario({ scenario_name: 'airt.jailbreak' })]))
      .mockReturnValueOnce(scenarioEstimates.promise)

    const { unmount } = render(<TestWrapper><ScenarioCatalog {...CATALOG_PROPS} /></TestWrapper>)

    await screen.findByTestId('scenario-card-airt.jailbreak')
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(2))
    unmount()
    await act(async () => {
      scenarioEstimates.reject(new Error('late estimate failure'))
    })
  })
})
