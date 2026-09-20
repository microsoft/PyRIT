import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { useScenarioQueue } from '@/hooks/useScenarioQueue'
import { labelsApi, scenariosApi } from '@/services/api'
import type { ScenarioRunListItem } from '@/types'

import ScenarioHistory from './ScenarioHistory'
import { DEFAULT_SCENARIO_HISTORY_FILTERS } from './scenarioHistoryFilters'

jest.mock('@/services/api', () => ({
  scenariosApi: {
    listCatalog: jest.fn(),
    listRuns: jest.fn(),
  },
  labelsApi: {
    getLabels: jest.fn(),
  },
}))

jest.mock('@/hooks/useScenarioQueue', () => ({
  useScenarioQueue: jest.fn(),
}))

const mockedScenariosApi = scenariosApi as jest.Mocked<typeof scenariosApi>
const mockedLabelsApi = labelsApi as jest.Mocked<typeof labelsApi>
const mockUseScenarioQueue = useScenarioQueue as jest.Mock

const RUN: ScenarioRunListItem = {
  scenario_result_id: 'run-1',
  scenario_name: 'RedTeamScenario',
  scenario_registry_name: 'foundry.red_team',
  scenario_version: 3,
  status: 'COMPLETED',
  created_at: '2026-01-01T00:00:00Z',
  started_at: '2026-01-01T00:00:05Z',
  updated_at: '2026-01-01T00:01:00Z',
  completed_at: '2026-01-01T00:01:00Z',
  techniques_used: ['prompt injection'],
  total_attacks: 2,
  completed_attacks: 2,
  successful_attacks: 1,
  objective_achieved_rate: 50,
  error_attacks: 1,
  total_retries: 2,
  labels: { operator: 'alice', operation: 'nightly', team: 'safety' },
  planned_total_available: true,
  attack_details_available: false,
  datasets_used: ['harmbench'],
  scenario_parameters: {},
  target: {
    target_type: 'OpenAIChatTarget',
    model_name: 'gpt-4o',
    endpoint: 'https://example.test/v1',
    identifier_hash: 'safe-hash',
  },
}

const defaultProps = {
  filters: { ...DEFAULT_SCENARIO_HISTORY_FILTERS },
  onFiltersChange: jest.fn(),
  onOpenRun: jest.fn(),
  onNavigate: jest.fn(),
}

function renderHistory(props = defaultProps) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <main>
        <ScenarioHistory {...props} />
      </main>
    </FluentProvider>,
  )
}

describe('ScenarioHistory', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUseScenarioQueue.mockReturnValue({
      snapshot: { revision: 0, snapshot_at: '2026-01-01T00:00:00Z', active: null, queued: [] },
      loading: false,
      stale: false,
      error: null,
      retry: jest.fn(),
    })
    mockedScenariosApi.listCatalog.mockResolvedValue({
      items: [{ scenario_name: 'foundry.red_team' }] as Awaited<ReturnType<typeof scenariosApi.listCatalog>>['items'],
      pagination: { limit: 100, has_more: false },
    })
    mockedLabelsApi.getLabels.mockResolvedValue({
      source: 'scenarios',
      labels: { operator: ['alice'], operation: ['nightly'], team: ['safety'] },
    })
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('renders safe run metadata and opens rows by click or keyboard', async () => {
    const user = userEvent.setup()
    const onOpenRun = jest.fn()
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [RUN],
      pagination: { limit: 25, has_more: false },
    })
    renderHistory({ ...defaultProps, onOpenRun })

    const row = await screen.findByTestId('scenario-history-row-run-1')
    expect(screen.getAllByRole('main')).toHaveLength(1)
    expect(screen.getByText('foundry.red_team')).toBeInTheDocument()
    expect(screen.getByText('RedTeamScenario · v3')).toBeInTheDocument()
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('1/2 (50%)')).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Scenario',
      'State',
      'Operator',
      'Operation',
      'Target',
      'Timing',
      'Attack Success',
      'Errors / retries',
      'Labels',
    ])
    expect(screen.getByText('55s')).toBeInTheDocument()
    expect(within(row).getByText('alice')).toBeInTheDocument()
    expect(within(row).getByText('nightly')).toBeInTheDocument()
    expect(within(row).getByText('team: safety')).toBeInTheDocument()
    expect(within(row).queryByText('operator: alice')).not.toBeInTheDocument()
    expect(within(row).queryByText('operation: nightly')).not.toBeInTheDocument()

    await user.click(row)
    expect(onOpenRun).toHaveBeenLastCalledWith('run-1')
    const link = screen.getByRole('link', { name: 'Open foundry.red_team scenario run' })
    expect(link).toHaveAttribute('href', '/scanner-history/run-1')
    link.focus()
    await user.keyboard('{Enter}')
    expect(onOpenRun).toHaveBeenCalledTimes(2)

    const modifiedClick = new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true })
    expect(link.dispatchEvent(modifiedClick)).toBe(true)
    expect(onOpenRun).toHaveBeenCalledTimes(2)
  })

  it('renders honest terminal legacy totals without redundant progress', async () => {
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [{
        ...RUN,
        planned_total_available: false,
        total_attacks: 1,
        completed_attacks: 1,
        successful_attacks: 1,
        objective_achieved_rate: 100,
      }],
      pagination: { limit: 25, has_more: false },
    })
    renderHistory()

    expect(await screen.findByText('Completed')).toBeInTheDocument()
    expect(screen.getByText('1/1 known results')).toBeInTheDocument()
    expect(screen.queryByText(/total unknown/i)).not.toBeInTheDocument()
    expect(screen.queryByText('1/1 (100%)')).not.toBeInTheDocument()
  })

  it('renders safe fallbacks when optional run metadata is unavailable', async () => {
    jest.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-01-01T00:00:30Z'))
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [{
        ...RUN,
        scenario_name: 'LegacyScenario',
        scenario_registry_name: null,
        scenario_version: 1,
        status: 'IN_PROGRESS',
        started_at: '2026-01-01T00:00:20Z',
        completed_at: null,
        total_attacks: 0,
        completed_attacks: 0,
        successful_attacks: 0,
        objective_achieved_rate: 0,
        error_attacks: 1,
        total_retries: 0,
        labels: {},
        target: {
          target_type: 'TextTarget',
          endpoint: null,
          model_name: null,
        },
      }],
      pagination: { limit: 25, has_more: false },
    })

    renderHistory()

    expect(await screen.findByRole('link', {
      name: 'Open LegacyScenario scenario run',
    })).toBeInTheDocument()
    expect(screen.getByText('v1')).toBeInTheDocument()
    expect(screen.getAllByText('TextTarget')).toHaveLength(2)
    expect(screen.getByText('10s')).toBeInTheDocument()
    expect(screen.getByText('0/0 attacks complete')).toBeInTheDocument()
    expect(screen.getByText('0/0')).toBeInTheDocument()
    expect(screen.getByText('1 / 0')).toBeInTheDocument()
    expect(screen.getAllByText('Unavailable')).toHaveLength(2)
  })

  it('does not display queue wait as execution elapsed time', async () => {
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [{
        ...RUN,
        status: 'QUEUED',
        started_at: null,
        completed_at: null,
      }],
      pagination: { limit: 25, has_more: false },
    })

    renderHistory()

    expect(await screen.findByText('Not started')).toBeInTheDocument()
    expect(screen.getByText('2 attacks planned')).toBeInTheDocument()
    expect(screen.queryByText(/\d+(?:s|m|h).*(?:elapsed|in progress)$/)).not.toBeInTheDocument()
  })

  it('shows truthful in-progress attack counts when the planned total is unknown', async () => {
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [{
        ...RUN,
        status: 'IN_PROGRESS',
        completed_at: null,
        planned_total_available: false,
        total_attacks: null,
        completed_attacks: 3,
      }],
      pagination: { limit: 25, has_more: false },
    })

    renderHistory()

    expect(await screen.findByText('3 complete / total unknown')).toBeInTheDocument()
  })

  it('shows the active run and queue order in the State column while preserving terminal states', async () => {
    const activeRun = {
      ...RUN,
      scenario_result_id: 'active-run',
      status: 'QUEUED' as const,
      completed_attacks: 1,
      completed_at: null,
    }
    const firstQueuedRun = {
      ...RUN,
      scenario_result_id: 'queued-run-1',
      status: 'QUEUED' as const,
      started_at: null,
      completed_at: null,
    }
    const secondQueuedRun = {
      ...firstQueuedRun,
      scenario_result_id: 'queued-run-2',
    }
    const terminalRun = {
      ...RUN,
      scenario_result_id: 'failed-run',
      status: 'FAILED' as const,
    }
    mockUseScenarioQueue.mockReturnValue({
      snapshot: {
        revision: 4,
        snapshot_at: '2026-01-01T00:00:00Z',
        active: {
          scenario_result_id: 'active-run',
          scenario_name: 'RedTeamScenario',
          scenario_registry_name: 'foundry.red_team',
          created_at: RUN.created_at,
          enqueued_at: RUN.created_at,
          started_at: RUN.started_at,
          state: 'IN_PROGRESS',
        },
        queued: [
          {
            scenario_result_id: 'queued-run-1',
            scenario_name: 'RedTeamScenario',
            scenario_registry_name: 'foundry.red_team',
            created_at: RUN.created_at,
            enqueued_at: RUN.created_at,
            state: 'QUEUED',
            position: 1,
          },
          {
            scenario_result_id: 'queued-run-2',
            scenario_name: 'RedTeamScenario',
            scenario_registry_name: 'foundry.red_team',
            created_at: RUN.created_at,
            enqueued_at: RUN.created_at,
            state: 'QUEUED',
            position: 2,
          },
          {
            scenario_result_id: 'failed-run',
            scenario_name: 'RedTeamScenario',
            scenario_registry_name: 'foundry.red_team',
            created_at: RUN.created_at,
            enqueued_at: RUN.created_at,
            state: 'QUEUED',
            position: 3,
          },
        ],
      },
      loading: false,
      stale: false,
      error: null,
      retry: jest.fn(),
    })
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [activeRun, firstQueuedRun, secondQueuedRun, terminalRun],
      pagination: { limit: 25, has_more: false },
    })

    renderHistory()

    const activeRow = await screen.findByTestId('scenario-history-row-active-run')
    expect(within(activeRow).getByText('In progress')).toBeInTheDocument()
    expect(within(activeRow).getByText('1/2 attacks complete')).toBeInTheDocument()
    expect(within(screen.getByTestId('scenario-history-row-queued-run-1')).getByText('Queued 1st'))
      .toBeInTheDocument()
    expect(within(screen.getByTestId('scenario-history-row-queued-run-2')).getByText('Queued 2nd'))
      .toBeInTheDocument()
    expect(within(screen.getByTestId('scenario-history-row-failed-run')).getByText('Failed'))
      .toBeInTheDocument()
    expect(screen.queryByText('Queued 3rd')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scenario-queue')).not.toBeInTheDocument()
  })

  it('refreshes persisted run state when the queue revision changes', async () => {
    const inProgressRun = {
      ...RUN,
      status: 'IN_PROGRESS' as const,
      completed_at: null,
    }
    mockUseScenarioQueue.mockReturnValue({
      snapshot: {
        revision: 4,
        snapshot_at: '2026-01-01T00:00:00Z',
        active: {
          scenario_result_id: RUN.scenario_result_id,
          scenario_name: RUN.scenario_name,
          scenario_registry_name: RUN.scenario_registry_name,
          created_at: RUN.created_at,
          enqueued_at: RUN.created_at,
          started_at: RUN.started_at,
          state: 'IN_PROGRESS',
        },
        queued: [],
      },
      loading: false,
      stale: false,
      error: null,
      retry: jest.fn(),
    })
    mockedScenariosApi.listRuns
      .mockResolvedValueOnce({
        items: [inProgressRun],
        pagination: { limit: 25, has_more: false },
      })
      .mockResolvedValueOnce({
        items: [RUN],
        pagination: { limit: 25, has_more: false },
      })

    const history = renderHistory()
    expect(await screen.findByText('In progress')).toBeInTheDocument()

    mockUseScenarioQueue.mockReturnValue({
      snapshot: {
        revision: 5,
        snapshot_at: '2026-01-01T00:01:00Z',
        active: null,
        queued: [],
      },
      loading: false,
      stale: false,
      error: null,
      retry: jest.fn(),
    })
    history.rerender(
      <FluentProvider theme={webLightTheme}>
        <main>
          <ScenarioHistory {...defaultProps} />
        </main>
      </FluentProvider>,
    )

    expect(await screen.findByText('Completed')).toBeInTheDocument()
    expect(mockedScenariosApi.listRuns).toHaveBeenCalledTimes(2)
  })

  it('does not display queue wait for a terminal run that never started', async () => {
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [{
        ...RUN,
        status: 'CANCELLED',
        started_at: null,
      }],
      pagination: { limit: 25, has_more: false },
    })

    renderHistory()

    expect(await screen.findByText('Execution time unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/\d+(?:s|m|h).*(?:elapsed|in progress)$/)).not.toBeInTheDocument()
  })

  it('isolates option-loading failures from the primary history request', async () => {
    mockedScenariosApi.listCatalog.mockRejectedValueOnce(new Error('catalog unavailable'))
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [RUN],
      pagination: { limit: 25, has_more: false },
    })
    renderHistory()

    expect(await screen.findByTestId('scenario-history-table')).toBeInTheDocument()
    expect(screen.getByText(/filter options could not be loaded: scenario names/i)).toBeInTheDocument()
  })

  it('shows request errors and retries without swallowing the failure', async () => {
    const user = userEvent.setup()
    mockedScenariosApi.listRuns
      .mockRejectedValueOnce(new Error('history unavailable'))
      .mockResolvedValueOnce({
        items: [RUN],
        pagination: { limit: 25, has_more: false },
      })
    renderHistory()

    expect(await screen.findByTestId('scenario-history-error')).toHaveTextContent('history unavailable')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByTestId('scenario-history-table')).toBeInTheDocument()
    expect(mockedScenariosApi.listRuns).toHaveBeenCalledTimes(2)
  })

  it('distinguishes unfiltered and filtered empty states', async () => {
    const user = userEvent.setup()
    const onNavigate = jest.fn()
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [],
      pagination: { limit: 25, has_more: false },
    })
    const first = renderHistory({ ...defaultProps, onNavigate })

    expect(await screen.findByText(/launch a scenario/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Browse scenarios' }))
    expect(onNavigate).toHaveBeenCalledWith('scenarios')
    first.unmount()

    renderHistory({
      ...defaultProps,
      filters: { ...DEFAULT_SCENARIO_HISTORY_FILTERS, statuses: ['FAILED'] },
    })
    expect(await screen.findByText('Try adjusting your filters.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Browse scenarios' })).not.toBeInTheDocument()
  })

  it('enables the single reset icon only when filters are active', () => {
    const history = renderHistory()
    expect(screen.getByRole('button', { name: 'Reset all filters' })).toBeDisabled()

    history.rerender(
      <FluentProvider theme={webLightTheme}>
        <ScenarioHistory
          {...defaultProps}
          filters={{ ...DEFAULT_SCENARIO_HISTORY_FILTERS, statuses: ['FAILED'] }}
        />
      </FluentProvider>,
    )

    expect(screen.getByRole('button', { name: 'Reset all filters' })).toBeEnabled()
  })

  it('serializes filters, paginates by cursor, and refreshes from the first page', async () => {
    const user = userEvent.setup()
    mockedScenariosApi.listRuns
      .mockResolvedValueOnce({
        items: [RUN],
        pagination: { limit: 25, has_more: true, next_cursor: 'next-page' },
      })
      .mockResolvedValue({
        items: [RUN],
        pagination: { limit: 25, has_more: false },
      })
    const history = renderHistory({
      ...defaultProps,
      filters: {
        ...DEFAULT_SCENARIO_HISTORY_FILTERS,
        scenarioNames: ['foundry.red_team'],
        statuses: ['IN_PROGRESS', 'FAILED'],
        operator: ['alice'],
        operation: ['nightly'],
        otherLabels: ['team:safety'],
      },
    })

    await screen.findByTestId('scenario-history-table')
    expect(mockedScenariosApi.listRuns).toHaveBeenNthCalledWith(1, {
      limit: 25,
      cursor: undefined,
      scenario_names: ['foundry.red_team'],
      run_statuses: ['IN_PROGRESS', 'FAILED'],
      label: ['operator:alice', 'operation:nightly', 'team:safety'],
    })

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(mockedScenariosApi.listRuns).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ cursor: 'next-page' }),
    ))
    expect(screen.getByText('Page 2')).toBeInTheDocument()

    history.rerender(
      <FluentProvider theme={webLightTheme}>
        <ScenarioHistory
          {...defaultProps}
          filters={{
            ...DEFAULT_SCENARIO_HISTORY_FILTERS,
            statuses: ['COMPLETED'],
          }}
        />
      </FluentProvider>,
    )
    await waitFor(() => expect(mockedScenariosApi.listRuns).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({ cursor: undefined, run_statuses: ['COMPLETED'] }),
    ))
    expect(await screen.findByText('Page 1')).toBeInTheDocument()

    await user.click(screen.getByTestId('scenario-history-refresh'))
    await waitFor(() => expect(mockedScenariosApi.listRuns).toHaveBeenNthCalledWith(
      4,
      expect.objectContaining({ cursor: undefined }),
    ))
  })

  it('hides stale pagination while changed filters are loading', async () => {
    let resolveFilteredRequest: ((value: Awaited<ReturnType<typeof scenariosApi.listRuns>>) => void) | undefined
    mockedScenariosApi.listRuns
      .mockResolvedValueOnce({
        items: [RUN],
        pagination: { limit: 25, has_more: true, next_cursor: 'stale-cursor' },
      })
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveFilteredRequest = resolve
      }))

    const history = renderHistory()
    expect(await screen.findByRole('button', { name: 'Next' })).toBeEnabled()

    history.rerender(
      <FluentProvider theme={webLightTheme}>
        <ScenarioHistory
          {...defaultProps}
          filters={{ ...DEFAULT_SCENARIO_HISTORY_FILTERS, statuses: ['FAILED'] }}
        />
      </FluentProvider>,
    )

    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
    expect(screen.getByText('Loading scanner history...')).toBeInTheDocument()
    await waitFor(() => expect(mockedScenariosApi.listRuns).toHaveBeenCalledTimes(2))
    expect(mockedScenariosApi.listRuns).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: undefined, run_statuses: ['FAILED'] }),
    )

    resolveFilteredRequest?.({
      items: [RUN],
      pagination: { limit: 25, has_more: false },
    })
    expect(await screen.findByTestId('scenario-history-table')).toBeInTheDocument()
  })
})
