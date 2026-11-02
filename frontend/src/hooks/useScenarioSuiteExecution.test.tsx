import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { scenarioSuitesApi } from '@/services/api'
import type {
  ScenarioSuiteExecutionStatus,
  ScenarioSuiteExecutionSummary,
} from '@/types'

import {
  SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS,
  useScenarioSuiteExecution,
} from './useScenarioSuiteExecution'

jest.mock('@/services/api', () => ({
  scenarioSuitesApi: {
    getExecution: jest.fn(),
  },
}))

const mockGetExecution = scenarioSuitesApi.getExecution as jest.Mock

function makeExecution(
  status: ScenarioSuiteExecutionStatus = 'IN_PROGRESS',
): ScenarioSuiteExecutionSummary {
  return {
    suite_execution_id: 'execution-1',
    suite_name: 'nightly_suite',
    suite_origin: 'stored',
    suite_version: 'suite-v1',
    status,
    created_at: '2026-10-23T12:00:00Z',
    updated_at: '2026-10-23T12:01:00Z',
    entries: [],
  }
}

function Harness({ executionId = 'execution-1' }: { readonly executionId?: string }) {
  const { state, retry, applyExecution } = useScenarioSuiteExecution(executionId)
  return (
    <>
      <span data-testid="load-status">{state.loadStatus}</span>
      <span data-testid="execution-status">{state.execution?.status ?? 'none'}</span>
      <span data-testid="error">{state.error ?? ''}</span>
      <span data-testid="stale">{String(state.stale)}</span>
      <button type="button" onClick={retry}>Retry</button>
      <button type="button" onClick={() => applyExecution(makeExecution('CANCELLED'))}>
        Apply cancelled
      </button>
    </>
  )
}

describe('useScenarioSuiteExecution', () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.clearAllMocks()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('polls until the execution reaches a terminal state', async () => {
    mockGetExecution
      .mockResolvedValueOnce(makeExecution('IN_PROGRESS'))
      .mockResolvedValueOnce(makeExecution('COMPLETED'))

    render(<Harness />)

    await waitFor(() => expect(screen.getByTestId('execution-status')).toHaveTextContent('IN_PROGRESS'))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS)
    })
    expect(screen.getByTestId('execution-status')).toHaveTextContent('COMPLETED')

    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS * 2)
    })
    expect(mockGetExecution).toHaveBeenCalledTimes(2)
  })

  it('retries one transient polling failure before surfacing an error', async () => {
    mockGetExecution
      .mockRejectedValueOnce(new Error('temporary outage'))
      .mockResolvedValueOnce(makeExecution('IN_PROGRESS'))

    render(<Harness />)

    await waitFor(() => expect(mockGetExecution).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId('error')).toHaveTextContent('')

    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS)
    })

    expect(screen.getByTestId('execution-status')).toHaveTextContent('IN_PROGRESS')
    expect(screen.getByTestId('load-status')).toHaveTextContent('ready')
  })

  it('reports an expired process-local execution as not found', async () => {
    mockGetExecution.mockRejectedValue({
      isAxiosError: true,
      response: { status: 404, data: { detail: 'Execution is no longer available.' } },
    })

    render(<Harness />)

    await waitFor(() => expect(screen.getByTestId('load-status')).toHaveTextContent('not-found'))
    expect(screen.getByTestId('error')).toHaveTextContent('Execution is no longer available.')
  })

  it('keeps stale progress visible and resumes polling after consecutive refresh failures', async () => {
    mockGetExecution
      .mockResolvedValueOnce(makeExecution('IN_PROGRESS'))
      .mockRejectedValueOnce(new Error('first refresh failed'))
      .mockRejectedValueOnce(new Error('second refresh failed'))
      .mockResolvedValueOnce(makeExecution('COMPLETED'))

    render(<Harness />)

    await waitFor(() => expect(screen.getByTestId('execution-status')).toHaveTextContent('IN_PROGRESS'))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS)
    })
    expect(screen.getByTestId('stale')).toHaveTextContent('false')

    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS)
    })

    expect(screen.getByTestId('execution-status')).toHaveTextContent('IN_PROGRESS')
    expect(screen.getByTestId('stale')).toHaveTextContent('true')
    expect(screen.getByTestId('error')).toHaveTextContent('second refresh failed')

    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS)
    })

    expect(screen.getByTestId('execution-status')).toHaveTextContent('COMPLETED')
    expect(screen.getByTestId('stale')).toHaveTextContent('false')
    expect(screen.getByTestId('error')).toHaveTextContent('')
    expect(mockGetExecution).toHaveBeenCalledTimes(4)
  })

  it('stops polling when cancellation applies a terminal response', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    mockGetExecution.mockResolvedValue(makeExecution('IN_PROGRESS'))

    render(<Harness />)

    await waitFor(() => expect(screen.getByTestId('execution-status')).toHaveTextContent('IN_PROGRESS'))
    await user.click(screen.getByRole('button', { name: 'Apply cancelled' }))
    expect(screen.getByTestId('execution-status')).toHaveTextContent('CANCELLED')

    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_SUITE_EXECUTION_POLL_INTERVAL_MS * 2)
    })
    expect(mockGetExecution).toHaveBeenCalledTimes(1)
  })
})
