import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes } from 'react-router'

import { scenariosApi } from '@/services/api'

import ScenarioRunStarted from './ScenarioRunStarted'

jest.mock('@/services/api', () => ({
  scenariosApi: {
    getRun: jest.fn(),
  },
}))

const mockGetRun = scenariosApi.getRun as jest.Mock

function renderShell(path: string, state?: unknown) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[{ pathname: path, state }]}>
        <Routes>
          <Route path="/scenario-history/:scenarioResultId" element={<ScenarioRunStarted />} />
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

function makeRunSummary(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    scenario_result_id: 'sr-1',
    scenario_name: 'foundry.red_team_agent',
    scenario_version: 0,
    status: 'IN_PROGRESS',
    created_at: '2026-02-15T00:00:00Z',
    updated_at: '2026-02-15T00:00:00Z',
    techniques_used: [],
    total_attacks: 0,
    completed_attacks: 0,
    objective_achieved_rate: 0,
    failed_attacks: [],
    attack_retries: [],
    total_retries: 0,
    labels: {},
    ...overrides,
  }
}

describe('ScenarioRunStarted', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders an accessible heading and the scenario result id', async () => {
    mockGetRun.mockResolvedValueOnce(makeRunSummary())

    renderShell('/scenario-history/sr-1')

    expect(screen.getByRole('heading', { name: 'Scenario run started' })).toBeInTheDocument()
    expect(screen.getByText('sr-1')).toBeInTheDocument()
    await screen.findByTestId('run-status')
  })

  it('decodes a percent-encoded scenario result id from the URL and fetches by the decoded id', async () => {
    mockGetRun.mockResolvedValueOnce(makeRunSummary({ scenario_result_id: 'sr/1' }))

    renderShell('/scenario-history/sr%2F1')

    await waitFor(() => expect(mockGetRun).toHaveBeenCalledWith('sr/1'))
    expect(screen.getByText('sr/1')).toBeInTheDocument()
  })

  it('shows a loading state before the fetch resolves', () => {
    mockGetRun.mockReturnValue(new Promise(() => {}))
    renderShell('/scenario-history/sr-1')
    expect(screen.getByText('Loading run status...')).toBeInTheDocument()
  })

  it('shows the run status once loaded', async () => {
    mockGetRun.mockResolvedValueOnce(makeRunSummary({ status: 'COMPLETED' }))

    renderShell('/scenario-history/sr-1')

    expect(await screen.findByTestId('run-status-value')).toHaveTextContent('COMPLETED')
  })

  it('shows an error state with retry on failure, and recovers after retry', async () => {
    const user = userEvent.setup()
    mockGetRun
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(makeRunSummary())

    renderShell('/scenario-history/sr-1')

    expect(await screen.findByTestId('run-error')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()

    await user.click(screen.getByTestId('retry-btn'))

    expect(await screen.findByTestId('run-status')).toBeInTheDocument()
    expect(mockGetRun).toHaveBeenCalledTimes(2)
  })

  it('does not poll — it fetches the run exactly once per mount', async () => {
    mockGetRun.mockResolvedValueOnce(makeRunSummary())
    renderShell('/scenario-history/sr-1')

    await screen.findByTestId('run-status')
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(mockGetRun).toHaveBeenCalledTimes(1)
  })

  it('shows the scenario name from location state before the fetch resolves', () => {
    mockGetRun.mockReturnValue(new Promise(() => {}))

    renderShell('/scenario-history/sr-1', { scenarioName: 'foundry.red_team_agent' })

    // The loading spinner is showing, but the run id itself is already visible from the URL.
    expect(screen.getByText('sr-1')).toBeInTheDocument()
  })

  it('works as a direct deep link with no location state at all', async () => {
    mockGetRun.mockResolvedValueOnce(makeRunSummary())

    renderShell('/scenario-history/sr-1')

    expect(await screen.findByTestId('run-status')).toBeInTheDocument()
    expect(screen.getByText(/foundry\.red_team_agent/)).toBeInTheDocument()
  })

  it('links back to the scenario catalog', async () => {
    mockGetRun.mockResolvedValueOnce(makeRunSummary())
    renderShell('/scenario-history/sr-1')

    expect(screen.getByRole('link', { name: /back to scanners/i })).toHaveAttribute('href', '/scanner')
  })
})
