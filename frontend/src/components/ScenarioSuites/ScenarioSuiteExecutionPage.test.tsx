import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes } from 'react-router'

import {
  useScenarioSuiteExecution,
  type UseScenarioSuiteExecutionResult,
} from '@/hooks/useScenarioSuiteExecution'
import { scenarioSuitesApi } from '@/services/api'
import type {
  ScenarioRunSummary,
  ScenarioSuiteExecutionEntrySummary,
  ScenarioSuiteExecutionSummary,
} from '@/types'

import ScenarioSuiteExecutionPage from './ScenarioSuiteExecutionPage'

jest.mock('@/hooks/useScenarioSuiteExecution', () => ({
  useScenarioSuiteExecution: jest.fn(),
  isTerminalSuiteExecutionStatus: (status: string) => (
    status === 'COMPLETED' || status === 'FAILED' || status === 'CANCELLED'
  ),
}))

jest.mock('@/services/api', () => ({
  scenarioSuitesApi: {
    cancelExecution: jest.fn(),
  },
}))

const mockUseScenarioSuiteExecution = useScenarioSuiteExecution as jest.Mock
const mockCancelExecution = scenarioSuitesApi.cancelExecution as jest.Mock
const mockRetry = jest.fn()
const mockApplyExecution = jest.fn()

const FIRST_ENTRY_ID = '11111111-1111-4111-8111-111111111111'
const SECOND_ENTRY_ID = '22222222-2222-4222-8222-222222222222'

function makeRun({
  runId,
  status = 'IN_PROGRESS',
  completedAttacks = 2,
}: {
  runId: string
  status?: ScenarioRunSummary['status']
  completedAttacks?: number
}): ScenarioRunSummary {
  return {
    scenario_result_id: runId,
    scenario_name: 'scenario.example',
    scenario_version: 1,
    status,
    created_at: '2026-10-23T12:00:00Z',
    updated_at: '2026-10-23T12:01:00Z',
    techniques_used: ['crescendo'],
    total_attacks: 10,
    completed_attacks: completedAttacks,
    objective_achieved_rate: 0,
    failed_attacks: [],
    attack_retries: [],
    total_retries: 0,
    labels: {},
  }
}

function makeEntry({
  entryId,
  position,
  presetName,
  state = 'IN_PROGRESS',
  run = null,
  error = null,
}: {
  entryId: string
  position: number
  presetName: string
  state?: ScenarioSuiteExecutionEntrySummary['state']
  run?: ScenarioRunSummary | null
  error?: string | null
}): ScenarioSuiteExecutionEntrySummary {
  return {
    entry_id: entryId,
    entry_position: position,
    preset_name: presetName,
    preset_origin: 'stored',
    preset_version: 'preset-v1',
    state,
    run,
    error,
    error_type: error ? 'ValueError' : null,
  }
}

function makeExecution(
  overrides: Partial<ScenarioSuiteExecutionSummary> = {},
): ScenarioSuiteExecutionSummary {
  return {
    suite_execution_id: 'execution-1',
    suite_name: 'nightly_suite',
    suite_origin: 'stored',
    suite_version: 'suite-v1',
    status: 'IN_PROGRESS',
    created_at: '2026-10-23T12:00:00Z',
    updated_at: '2026-10-23T12:01:00Z',
    entries: [
      makeEntry({
        entryId: FIRST_ENTRY_ID,
        position: 1,
        presetName: 'first_preset',
        run: makeRun({ runId: 'run-1' }),
      }),
      makeEntry({
        entryId: SECOND_ENTRY_ID,
        position: 2,
        presetName: 'second_preset',
        state: 'QUEUED',
        run: makeRun({ runId: 'run-2', status: 'QUEUED', completedAttacks: 0 }),
      }),
    ],
    ...overrides,
  }
}

function setHookResult(
  state: UseScenarioSuiteExecutionResult['state'],
): void {
  mockUseScenarioSuiteExecution.mockReturnValue({
    state,
    retry: mockRetry,
    applyExecution: mockApplyExecution,
  })
}

function renderPage() {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={['/scanner/suites/executions/execution-1']}>
        <Routes>
          <Route
            path="/scanner/suites/executions/:executionId"
            element={<ScenarioSuiteExecutionPage />}
          />
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('ScenarioSuiteExecutionPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    setHookResult({
      loadStatus: 'ready',
      execution: makeExecution(),
      error: null,
      stale: false,
    })
  })

  it('shows ordered child progress and links to ordinary scenario runs', () => {
    renderPage()

    expect(screen.getByRole('heading', { name: 'nightly_suite' })).toBeInTheDocument()
    const rows = screen.getAllByTestId(/^suite-execution-entry-/)
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('first_preset')).toBeInTheDocument()
    expect(within(rows[0]).getByText('2 of 10 attacks')).toBeInTheDocument()
    expect(within(rows[0]).getByRole('link', { name: 'View run' }))
      .toHaveAttribute('href', '/scanner-history/run-1')
    expect(within(rows[1]).getByText('second_preset')).toBeInTheDocument()
    expect(within(rows[1]).getAllByText('Queued')).toHaveLength(2)
    expect(screen.getByRole('link', { name: 'Back to scanners' })).toHaveAttribute('href', '/scanner')
  })

  it('shows safe entry failures without rendering backend exception details', () => {
    const rawError = (
      "(sqlite3.OperationalError) database is locked "
      + "[SQL: INSERT INTO ScenarioResultEntries ...] "
      + "[parameters: ('secret-token',)]"
    )
    setHookResult({
      loadStatus: 'ready',
      execution: makeExecution({
        status: 'FAILED',
        entries: [
          makeEntry({
            entryId: FIRST_ENTRY_ID,
            position: 1,
            presetName: 'first_preset',
            state: 'SUBMISSION_FAILED',
            error: rawError,
          }),
          makeEntry({
            entryId: SECOND_ENTRY_ID,
            position: 2,
            presetName: 'second_preset',
            state: 'FAILED',
            error: rawError,
          }),
        ],
      }),
      error: null,
      stale: false,
    })

    renderPage()

    expect(screen.getByText(
      'The scenario run could not be created. Check server logs before retrying.',
    )).toBeInTheDocument()
    expect(screen.getByText(
      'The scenario run status could not be loaded. Refresh the page or check server logs.',
    )).toBeInTheDocument()
    expect(screen.queryByText(/database is locked/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/secret-token/i)).not.toBeInTheDocument()
    expect(screen.getAllByText('Not created')).toHaveLength(2)
    expect(screen.getAllByText('Submission failed')).toHaveLength(2)
  })

  it('cancels pending submissions and child runs after confirmation', async () => {
    const user = userEvent.setup()
    const cancelled = makeExecution({
      status: 'CANCELLED',
      entries: makeExecution().entries.map((entry) => ({ ...entry, state: 'CANCELLED' })),
    })
    mockCancelExecution.mockResolvedValue(cancelled)

    renderPage()

    await user.click(screen.getByRole('button', { name: 'Cancel suite' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByTestId('confirm-cancel-suite'))

    await waitFor(() => expect(mockCancelExecution).toHaveBeenCalledWith('execution-1'))
    expect(mockApplyExecution).toHaveBeenCalledWith(cancelled)
  })

  it('keeps the cancellation dialog open when cancellation fails', async () => {
    const user = userEvent.setup()
    mockCancelExecution.mockRejectedValue(new Error('Cancellation unavailable.'))

    renderPage()

    await user.click(screen.getByRole('button', { name: 'Cancel suite' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByTestId('confirm-cancel-suite'))

    expect(await within(dialog).findByText('Cancellation unavailable.')).toBeInTheDocument()
    expect(mockApplyExecution).not.toHaveBeenCalled()
  })

  it('explains when transient suite progress is no longer available', () => {
    setHookResult({
      loadStatus: 'not-found',
      execution: null,
      error: 'Execution is no longer available.',
      stale: false,
    })

    renderPage()

    expect(screen.getByRole('heading', { name: 'Suite execution not found' })).toBeInTheDocument()
    expect(screen.getByText(/successfully created scenario runs remain/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to scanners' })).toHaveAttribute('href', '/scanner')
  })
})
