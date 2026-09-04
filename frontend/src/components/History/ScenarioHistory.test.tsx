import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

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

const mockedScenariosApi = scenariosApi as jest.Mocked<typeof scenariosApi>
const mockedLabelsApi = labelsApi as jest.Mocked<typeof labelsApi>

const RUN: ScenarioRunListItem = {
  scenario_result_id: 'run-1',
  scenario_name: 'RedTeamScenario',
  scenario_registry_name: 'foundry.red_team',
  scenario_version: 3,
  status: 'COMPLETED',
  created_at: '2026-01-01T00:00:00Z',
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
      <ScenarioHistory {...props} />
    </FluentProvider>,
  )
}

describe('ScenarioHistory', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedScenariosApi.listCatalog.mockResolvedValue({
      items: [{ scenario_name: 'foundry.red_team' }] as Awaited<ReturnType<typeof scenariosApi.listCatalog>>['items'],
      pagination: { limit: 100, has_more: false },
    })
    mockedLabelsApi.getLabels.mockResolvedValue({
      source: 'scenarios',
      labels: { operator: ['alice'], operation: ['nightly'], team: ['safety'] },
    })
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
    expect(screen.getByText('foundry.red_team')).toBeInTheDocument()
    expect(screen.getByText('RedTeamScenario · v3')).toBeInTheDocument()
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('2/2')).toBeInTheDocument()
    expect(screen.getByText('1/2 (50%)')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Runtime' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Attacks Complete' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Attack Success' })).toBeInTheDocument()
    expect(screen.getByText('1m (completed)')).toBeInTheDocument()
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
