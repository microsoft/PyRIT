import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { useScenarioQueue } from '@/hooks/useScenarioQueue'
import { labelsApi, scenariosApi } from '@/services/api'
import type { ScenarioRunListItem, ScenarioRunListResponse, ScenarioRunState, ScenarioRunSummary } from '@/types'

import ScenarioHistory from './ScenarioHistory'
import { DEFAULT_SCENARIO_HISTORY_FILTERS } from './scenarioHistoryFilters'

jest.mock('@/services/api', () => ({
  scenariosApi: {
    listCatalog: jest.fn(),
    listRuns: jest.fn(),
    resumeRun: jest.fn(),
    getResumeRequirements: jest.fn(),
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
const mockQueueRetry = jest.fn()

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
  labels: { operator: 'alice' },
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
    mockedScenariosApi.getResumeRequirements.mockResolvedValue({ requires_execution_options: false })
    mockUseScenarioQueue.mockReturnValue({
      snapshot: { revision: 0, snapshot_at: '2026-01-01T00:00:00Z', active: null, queued: [] },
      loading: false,
      stale: false,
      error: null,
      retry: mockQueueRetry,
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

  it('resumes a failed history run by keyboard without navigating or replacing its ID and completed counts', async () => {
    const user = userEvent.setup()
    const resumedRun: ScenarioRunSummary = {
      ...RUN,
      total_attacks: 2,
      status: 'IN_PROGRESS',
      completed_attacks: 1,
      completed_at: null,
      failed_attacks: [],
      attack_retries: [],
    }
    let resolveResume: ((summary: ScenarioRunSummary) => void) | undefined
    mockedScenariosApi.resumeRun.mockImplementationOnce(() => new Promise<ScenarioRunSummary>((resolve) => {
      resolveResume = resolve
    }))
    mockedScenariosApi.listRuns
      .mockResolvedValueOnce({
        items: [{ ...RUN, status: 'FAILED', completed_attacks: 1 }],
        pagination: { limit: 25, has_more: false },
      })
      .mockResolvedValueOnce({
        items: [{ ...RUN, status: 'IN_PROGRESS', completed_attacks: 1, completed_at: null }],
        pagination: { limit: 25, has_more: false },
      })
    renderHistory()
    const resumeButton = await screen.findByRole('button', { name: 'Resume foundry.red_team run run-1' })
    expect(mockedScenariosApi.resumeRun).not.toHaveBeenCalled()
    resumeButton.focus()
    await user.keyboard('{Enter}')
    expect(resumeButton).toBeDisabled()
    expect(resumeButton).toHaveTextContent('Resuming...')
    await user.dblClick(resumeButton)
    expect(mockedScenariosApi.resumeRun).toHaveBeenCalledTimes(1)
    expect(mockedScenariosApi.resumeRun).toHaveBeenCalledWith('run-1')
    expect(defaultProps.onOpenRun).not.toHaveBeenCalled()
    expect(screen.getByText('1/2')).toBeInTheDocument()

    await act(async () => { resolveResume?.(resumedRun) })

    const row = await screen.findByRole('row', { name: /foundry.red_team.*In progress/ })
    expect(within(row).getByText('1/2')).toBeInTheDocument()
    expect(within(row).getByRole('link')).toHaveAttribute('href', '/scanner-history/run-1')
    expect(screen.queryByRole('button', { name: /Resume foundry/ })).not.toBeInTheDocument()
    expect(mockedScenariosApi.listRuns).toHaveBeenCalledTimes(2)
    expect(mockQueueRetry).toHaveBeenCalledTimes(1)
    expect(defaultProps.onOpenRun).not.toHaveBeenCalled()
  })

  it('guards history resume clicks synchronously before the disabled render', async () => {
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [{ ...RUN, status: 'FAILED' }],
      pagination: { limit: 25, has_more: false },
    })
    let rejectResume: ((error: Error) => void) | undefined
    mockedScenariosApi.resumeRun.mockImplementationOnce(() => new Promise<ScenarioRunSummary>((_resolve, reject) => {
      rejectResume = reject
    }))
    renderHistory()
    const resumeButton = await screen.findByRole('button', { name: /Resume foundry/ })

    act(() => {
      resumeButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      resumeButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(mockedScenariosApi.getResumeRequirements).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(mockedScenariosApi.resumeRun).toHaveBeenCalledTimes(1))
    expect(mockedScenariosApi.resumeRun).toHaveBeenCalledTimes(1)
    expect(defaultProps.onOpenRun).not.toHaveBeenCalled()
    await act(async () => { rejectResume?.(new Error('Network unavailable')) })
    expect(await screen.findByText('Network unavailable')).toBeInTheDocument()
    expect(mockQueueRetry).toHaveBeenCalledTimes(1)
  })

  it.each<string | null>(['The saved target rejected execution.', null])(
    'keeps immediate execution failure feedback in history after refresh: %s',
    async (error: string | null) => {
      const user = userEvent.setup()
      const failedRun: ScenarioRunSummary = {
        ...RUN,
        status: 'FAILED',
        completed_attacks: 1,
        failed_attacks: [],
        attack_retries: [],
        error,
      }
      mockedScenariosApi.listRuns.mockResolvedValue({
        items: [{ ...RUN, status: 'FAILED', completed_attacks: 1 }],
        pagination: { limit: 25, has_more: false },
      })
      mockedScenariosApi.resumeRun.mockResolvedValueOnce(failedRun)
      renderHistory()
      await user.click(await screen.findByRole('button', { name: /Resume foundry/ }))

      const message = error || 'The resumed run failed. Finished results remain available.'
      expect(await screen.findByText(message)).toBeInTheDocument()
      expect(await screen.findByRole('row', { name: /foundry.red_team.*Failed/ })).toHaveTextContent('1/2')
      expect(mockedScenariosApi.listRuns).toHaveBeenCalledTimes(2)
      expect(mockQueueRetry).toHaveBeenCalledTimes(1)
      expect(mockedScenariosApi.resumeRun).toHaveBeenCalledTimes(1)
      expect(defaultProps.onOpenRun).not.toHaveBeenCalled()

      mockedScenariosApi.resumeRun.mockResolvedValueOnce({ ...failedRun, status: 'QUEUED', error: null })
      mockedScenariosApi.listRuns.mockResolvedValueOnce({
        items: [{ ...RUN, status: 'QUEUED', completed_attacks: 1 }],
        pagination: { limit: 25, has_more: false },
      })
      await user.click(screen.getByRole('button', { name: /Resume foundry/ }))
      await screen.findByRole('row', { name: /foundry.red_team.*Queued/ })

      expect(screen.queryByText(message)).not.toBeInTheDocument()
      expect(mockedScenariosApi.resumeRun).toHaveBeenCalledTimes(2)
    },
  )

  it.each<[number, string]>([
    [404, 'Scenario run not found.'],
    [409, 'This run is already active or lacks safe saved configuration.'],
    [400, 'Saved configuration no longer matches the registered scenario.'],
    [500, 'Unable to resume this run.'],
  ])('keeps HTTP %s resume feedback visible during and after state refresh', async (status: number, detail: string) => {
    const user = userEvent.setup()
    let resolveRefresh: ((response: ScenarioRunListResponse) => void) | undefined
    mockedScenariosApi.listRuns
      .mockResolvedValueOnce({
        items: [{ ...RUN, status: 'FAILED' }],
        pagination: { limit: 25, has_more: false },
      })
      .mockImplementationOnce(() => new Promise<ScenarioRunListResponse>((resolve) => {
        resolveRefresh = resolve
      }))
    mockedScenariosApi.resumeRun.mockRejectedValueOnce({
      isAxiosError: true, response: { status, data: { detail } },
    })
    renderHistory()
    await user.click(await screen.findByRole('button', { name: /Resume foundry/ }))

    expect(await screen.findByText(detail)).toBeInTheDocument()
    expect(mockedScenariosApi.listRuns).toHaveBeenCalledTimes(2)
    expect(mockQueueRetry).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveRefresh?.({
        items: status === 404 ? [] : [{ ...RUN, status: 'IN_PROGRESS' }],
        pagination: { limit: 25, has_more: false },
      })
    })

    expect(screen.getByText(detail)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Resume foundry/ })).not.toBeInTheDocument()
    expect(mockedScenariosApi.resumeRun).toHaveBeenCalledTimes(1)
    expect(defaultProps.onOpenRun).not.toHaveBeenCalled()
  })

  it.each<ScenarioRunState>(['CREATED', 'QUEUED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'])(
    'does not offer resume for %s history runs',
    async (status: ScenarioRunState) => {
      mockedScenariosApi.listRuns.mockResolvedValue({
        items: [{ ...RUN, status }],
        pagination: { limit: 25, has_more: false },
      })
      renderHistory()
      await screen.findByRole('table', { name: 'Scanner history' })
      expect(screen.queryByRole('button', { name: /Resume/ })).not.toBeInTheDocument()
      expect(mockedScenariosApi.resumeRun).not.toHaveBeenCalled()
      expect(mockedScenariosApi.getResumeRequirements).not.toHaveBeenCalled()
    },
  )

  it('uses the shared legacy dialog from history and keeps submit conflicts visible after refresh', async () => {
    const user = userEvent.setup()
    mockedScenariosApi.listRuns.mockResolvedValue({
      items: [{ ...RUN, status: 'FAILED' }],
      pagination: { limit: 25, has_more: false },
    })
    mockedScenariosApi.getResumeRequirements.mockResolvedValueOnce({ requires_execution_options: true })
    mockedScenariosApi.resumeRun.mockRejectedValueOnce({
      isAxiosError: true, response: { status: 409, data: { detail: 'This run is already queued.' } },
    })
    renderHistory()
    await user.click(await screen.findByRole('button', { name: /Resume foundry/ }))

    const dialog = await screen.findByRole('dialog', { name: 'Resume run with execution options' })
    expect(mockedScenariosApi.resumeRun).not.toHaveBeenCalled()
    expect(defaultProps.onOpenRun).not.toHaveBeenCalled()
    expect(within(dialog).getByRole('spinbutton', { name: 'Max concurrency' })).toHaveValue('1')
    expect(within(dialog).getByRole('spinbutton', { name: 'Max retries' })).toHaveValue('0')
    await user.click(within(dialog).getByRole('button', { name: 'Resume', exact: true }))

    expect(mockedScenariosApi.resumeRun).toHaveBeenCalledWith('run-1', { max_concurrency: 1, max_retries: 0 })
    expect(await within(dialog).findByText('This run is already queued.')).toBeInTheDocument()
    expect(mockedScenariosApi.listRuns).toHaveBeenCalledTimes(2)
    expect(mockQueueRetry).toHaveBeenCalledTimes(1)
    expect(defaultProps.onOpenRun).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByText('This run is already queued.')).toBeInTheDocument()
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
    expect(screen.getByText('2/2')).toBeInTheDocument()
    expect(screen.getByText('1/2 (50%)')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Runtime' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Attacks Complete' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Attack Success' })).toBeInTheDocument()
    expect(screen.getByText('55s (completed)')).toBeInTheDocument()
    expect(screen.getByText('operator: alice')).toBeInTheDocument()

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

  it('renders honest legacy totals without a misleading percentage', async () => {
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

    expect(await screen.findByText('1 known / total unknown')).toBeInTheDocument()
    expect(screen.getByText('1/1 known results')).toBeInTheDocument()
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
    expect(screen.getByText('10s (in progress)')).toBeInTheDocument()
    expect(screen.getAllByText('0/0')).toHaveLength(2)
    expect(screen.getByText('1 / 0')).toBeInTheDocument()
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
    expect(screen.queryByText(/\d+(?:s|m|h).*(?:elapsed|in progress)$/)).not.toBeInTheDocument()
  })

  it('shows the active run and queue order in the State column while preserving terminal states', async () => {
    const activeRun = {
      ...RUN,
      scenario_result_id: 'active-run',
      status: 'IN_PROGRESS' as const,
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

    expect(within(await screen.findByTestId('scenario-history-row-active-run')).getByText('In progress'))
      .toBeInTheDocument()
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
