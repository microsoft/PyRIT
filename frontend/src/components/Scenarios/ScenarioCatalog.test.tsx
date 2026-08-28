import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, useLocation } from 'react-router'

import { scenariosApi } from '@/services/api'
import type { RegisteredScenario } from '@/types'

import ScenarioCatalog from './ScenarioCatalog'

jest.mock('@/services/api', () => ({
  scenariosApi: {
    listCatalog: jest.fn(),
  },
}))

const mockListCatalog = scenariosApi.listCatalog as jest.Mock

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
    default_dataset_summaries: [],
    baseline_policy: 'enabled',
    include_baseline_by_default: true,
    supported_parameters: [],
    default_run_size: {
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

describe('ScenarioCatalog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('shows a loading state while fetching', () => {
    mockListCatalog.mockReturnValue(new Promise(() => {}))
    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)
    expect(screen.getByText('Loading scenarios...')).toBeInTheDocument()
  })

  it('renders every scenario from a single page', async () => {
    mockListCatalog.mockResolvedValueOnce({
      items: [
        makeScenario({ scenario_name: 'foundry.red_team_agent', description: 'Red teams a target.' }),
        makeScenario({ scenario_name: 'encoding.base64', description: 'Encodes prompts.' }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    expect(await screen.findByText('foundry.red_team_agent')).toBeInTheDocument()
    expect(screen.getByText('encoding.base64')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Scanner' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Read the scanner documentation' })).toHaveAttribute(
      'href',
      'https://microsoft.github.io/PyRIT/scanner/0_scanner/',
    )
    expect(mockListCatalog).toHaveBeenCalledTimes(1)
  })

  it('ignores a catalog response that resolves after unmount', async () => {
    let resolveRequest: ((value: {
      items: RegisteredScenario[]
      pagination: { limit: number; has_more: boolean }
    }) => void) | undefined
    mockListCatalog.mockImplementationOnce(() => new Promise((resolve) => {
      resolveRequest = resolve
    }))

    const { unmount } = render(<TestWrapper><ScenarioCatalog /></TestWrapper>)
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

    const { unmount } = render(<TestWrapper><ScenarioCatalog /></TestWrapper>)
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(1))
    unmount()
    await act(async () => {
      rejectRequest?.(new Error('late failure'))
    })
  })

  it('renders the exact launch-index column order and applies spacing to every cell', async () => {
    mockListCatalog.mockResolvedValueOnce({
      items: [makeScenario({ scenario_name: 'foundry.red_team_agent' })],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    const table = await screen.findByRole('table', { name: 'Registered scenarios' })
    expect(screen.getByText(/packages objective datasets, technique sets or selected techniques/i))
      .toBeInTheDocument()
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

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    expect(await screen.findByText('scenario.page1')).toBeInTheDocument()
    expect(screen.getByText('scenario.page2')).toBeInTheDocument()
    expect(mockListCatalog).toHaveBeenCalledTimes(2)
    expect(mockListCatalog).toHaveBeenNthCalledWith(2, 200, 'cursor-1')
  })

  it('stops paging if the backend repeats a cursor instead of looping forever', async () => {
    mockListCatalog.mockResolvedValue({
      items: [makeScenario({ scenario_name: 'scenario.loop' })],
      pagination: { limit: 1, has_more: true, next_cursor: 'same-cursor' },
    })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    expect(await screen.findAllByText('scenario.loop')).toHaveLength(1)
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(2))
    // Give any additional (incorrect) fetch a chance to fire before asserting it didn't.
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(mockListCatalog).toHaveBeenCalledTimes(2)
  })

  it('shows an empty state when no scenarios are registered', async () => {
    mockListCatalog.mockResolvedValueOnce({ items: [], pagination: { limit: 200, has_more: false } })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    expect(await screen.findByTestId('empty-state')).toBeInTheDocument()
  })

  it('shows an error MessageBar with a retry action on failure', async () => {
    mockListCatalog.mockRejectedValueOnce(new Error('Network error — check that the backend is running and reachable.'))

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    expect(await screen.findByTestId('error-state')).toBeInTheDocument()
    expect(screen.getByText(/Network error/)).toBeInTheDocument()
    expect(screen.getByTestId('retry-btn')).toBeInTheDocument()
  })

  it('retries the fetch when Retry is clicked', async () => {
    const user = userEvent.setup()
    mockListCatalog
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({
        items: [makeScenario({ scenario_name: 'scenario.recovered' })],
        pagination: { limit: 200, has_more: false },
      })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    await screen.findByTestId('error-state')
    await user.click(screen.getByTestId('retry-btn'))

    expect(await screen.findByText('scenario.recovered')).toBeInTheDocument()
    expect(mockListCatalog).toHaveBeenCalledTimes(2)
  })

  it('filters scenarios by the search box across name, description, techniques, and datasets', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValueOnce({
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

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    await screen.findByText('foundry.red_team_agent')

    await user.type(screen.getByLabelText('Search scenarios'), 'Multi-turn')

    expect(screen.queryByText('foundry.red_team_agent')).not.toBeInTheDocument()
    expect(screen.getByText('encoding.base64')).toBeInTheDocument()
  })

  it('searches dataset metadata and renders singular counts with no default techniques', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValueOnce({
      items: [
        makeScenario({
          scenario_name: 'scenario.one',
          default_techniques: [],
          default_datasets: ['dataset-one'],
          default_dataset_summaries: [{
            name: 'dataset-one',
            kind: 'dataset',
            logical_seed_group_count: 1,
            selected_seed_group_count: 1,
            configured_caps: [],
            selection_note: null,
          }],
        }),
        makeScenario({
          scenario_name: 'scenario.two',
          default_datasets: ['dataset-two'],
          default_dataset_summaries: [{
            name: 'dataset-two',
            kind: 'dataset',
            logical_seed_group_count: 2,
            selected_seed_group_count: 2,
            configured_caps: [],
            selection_note: 'Dataset metadata is searchable.',
          }],
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)
    await screen.findByText('scenario.one')
    await user.type(screen.getByLabelText('Search scenarios'), 'dataset')

    const firstRow = screen.getByTestId('scenario-card-scenario.one')
    expect(within(firstRow).getByText('1 objective')).toBeInTheDocument()
    expect(within(firstRow).getByText(/dataset-one/)).toBeInTheDocument()
    expect(within(firstRow).getByText('No default techniques')).toBeInTheDocument()
    expect(screen.getByText('scenario.two')).toBeInTheDocument()
  })

  it('shows a no-results state when the search matches nothing', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockResolvedValueOnce({
      items: [makeScenario({ scenario_name: 'foundry.red_team_agent' })],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)
    await screen.findByText('foundry.red_team_agent')

    await user.type(screen.getByLabelText('Search scenarios'), 'no-such-scenario')

    expect(await screen.findByTestId('no-results-state')).toBeInTheDocument()
  })

  it('links each card to its encoded scenario detail route', async () => {
    mockListCatalog.mockResolvedValueOnce({
      items: [makeScenario({ scenario_name: 'foundry/red_team_agent' })],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    const card = await screen.findByRole('link', { name: /foundry\/red_team_agent/i })
    expect(card).toHaveAttribute('href', '/scanner/foundry%2Fred_team_agent')
  })

  it('shows the total default objectives followed by the dataset names', async () => {
    mockListCatalog.mockResolvedValueOnce({
      items: [
        makeScenario({
          scenario_name: 'scenario.compound',
          default_datasets: ['population-a', 'population-b'],
          dataset_size_limit: {
            default_scope: 'none',
            default_count: null,
            override_scope: 'per_dataset',
          },
          default_dataset_summaries: [
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
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    const row = await screen.findByTestId('scenario-card-scenario.compound')
    expect(within(row).getByText('6 objectives')).toBeInTheDocument()
    expect(within(row).getByText('population-a · population-b')).toBeInTheDocument()
  })

  it('shows an adaptive estimate as a plain attack range', async () => {
    mockListCatalog.mockResolvedValueOnce({
      items: [
        makeScenario({
          scenario_name: 'adaptive.text_adaptive',
          default_run_size: {
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

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    const row = await screen.findByTestId('scenario-card-adaptive.text_adaptive')
    expect(within(row).getByText('21-42 attacks')).toBeInTheDocument()
    expect(within(row).queryByText(/progress units|attack attempts/i)).not.toBeInTheDocument()
  })

  it('keeps declared datasets visible when backend population summaries are unavailable', async () => {
    mockListCatalog.mockResolvedValueOnce({
      items: [
        makeScenario({
          scenario_name: 'scenario.unsized',
          default_datasets: ['harmbench'],
          dataset_size_limit: {
            default_scope: 'none',
            default_count: null,
            override_scope: 'per_dataset',
          },
          default_dataset_summaries: [],
        }),
      ],
      pagination: { limit: 200, has_more: false },
    })

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)
    const row = await screen.findByTestId('scenario-card-scenario.unsized')
    expect(within(row).getByText('Population counts unavailable')).toBeInTheDocument()
    expect(within(row).getByText('harmbench')).toBeInTheDocument()
  })

  it('keeps the authoritative default comparison values in the launch row', async () => {
    mockListCatalog.mockResolvedValueOnce({
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
          default_dataset_summaries: [
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
          default_run_size: {
            estimated_attack_count: null,
            minimum_attack_count: 12,
            maximum_attack_count: 20,
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

    render(<TestWrapper><ScenarioCatalog /></TestWrapper>)

    const row = await screen.findByTestId('scenario-card-airt.jailbreak')
    expect(within(row).getByText('4 objectives')).toBeInTheDocument()
    expect(within(row).getByText('harmbench')).toBeInTheDocument()
    expect(within(row).getByText('2 techniques')).toBeInTheDocument()
    expect(within(row).getByText('12-20 attacks')).toBeInTheDocument()
    expect(within(row).queryByText('default')).not.toBeInTheDocument()
    expect(within(row).queryByText(/aggregate presets|compatible concrete/i)).not.toBeInTheDocument()
    expect(within(row).queryByText(REMOVED_NORMAL_ESTIMATE_LABELS)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /show details|hide details/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /details/i })).not.toBeInTheDocument()
  })
})
