import { act, renderHook, waitFor } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import type {
  ScenarioProgressResult,
  ScenarioRunProgress,
  ScenarioRunSummary,
} from '@/types'

import {
  SCENARIO_RUN_POLL_INTERVAL_MS,
  useScenarioRunProgress,
} from './useScenarioRunProgress'

jest.mock('@/services/api', () => ({
  scenariosApi: {
    getRunProgress: jest.fn(),
  },
}))

const mockGetRunProgress = scenariosApi.getRunProgress as jest.Mock

function makeResult(id: string): ScenarioProgressResult {
  return {
    attack_result_id: id,
    conversation_id: 'conversation-1',
    atomic_group_id: 'group-1',
    atomic_attack_name: 'attack-1',
    seed_group_id: 'seed-1',
    outcome: 'success',
    execution_time_ms: 1_000,
    timestamp: '2026-01-01T00:00:01Z',
    total_retries: 0,
    retries: [],
  }
}

function makePage(overrides: Partial<ScenarioRunProgress> = {}): ScenarioRunProgress {
  return {
    run: {
      scenario_result_id: 'run-1',
      scenario_name: 'TestScenario',
      scenario_registry_name: 'test.scenario',
      scenario_version: 1,
      status: 'IN_PROGRESS',
      created_at: '2026-01-01T00:00:00Z',
    },
    plan: {
      version: 1,
      scenario_registry_name: 'test.scenario',
      atomic_groups: [],
      seed_groups: [],
    },
    results: [],
    summary: {
      overall: {
        completed: 0,
        planned: 0,
        succeeded: 0,
        success_percentage: null,
        errors: 0,
        retries: 0,
      },
      objective_scorer: null,
      techniques: [],
      seed_groups: [],
      atomic_groups: [],
    },
    next_cursor: null,
    has_more: false,
    plan_complete: true,
    ...overrides,
  }
}

function makeSummary(overrides: Partial<ScenarioRunSummary> = {}): ScenarioRunSummary {
  return {
    scenario_result_id: 'run-1',
    scenario_name: 'TestScenario',
    scenario_version: 1,
    status: 'IN_PROGRESS',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:01Z',
    techniques_used: [],
    total_attacks: 1,
    completed_attacks: 0,
    objective_achieved_rate: 0,
    failed_attacks: [],
    attack_retries: [],
    total_retries: 0,
    labels: {},
    ...overrides,
  }
}

describe('useScenarioRunProgress', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('loads the plan and immediately drains all available delta pages', async () => {
    mockGetRunProgress
      .mockResolvedValueOnce(makePage({
        results: [makeResult('attempt-1')],
        next_cursor: 'cursor-1',
        has_more: true,
      }))
      .mockResolvedValueOnce(makePage({
        plan: null,
        results: [makeResult('attempt-2')],
        next_cursor: 'cursor-2',
        has_more: false,
      }))

    const { result, unmount } = renderHook(() => useScenarioRunProgress('run-1'))

    await waitFor(() => expect(result.current.state.results).toHaveLength(2))
    expect(mockGetRunProgress).toHaveBeenNthCalledWith(
      1,
      'run-1',
      { since: undefined, limit: 500 },
      expect.any(AbortSignal),
    )
    expect(mockGetRunProgress).toHaveBeenNthCalledWith(
      2,
      'run-1',
      { since: 'cursor-1', limit: 500 },
      expect.any(AbortSignal),
    )
    unmount()
  })

  it('polls after 2.5 seconds from the last successfully applied cursor', async () => {
    jest.useFakeTimers()
    mockGetRunProgress
      .mockResolvedValueOnce(makePage({ next_cursor: 'cursor-1' }))
      .mockResolvedValueOnce(makePage({ plan: null, next_cursor: 'cursor-2' }))

    const { unmount } = renderHook(() => useScenarioRunProgress('run-1'))
    await act(async () => Promise.resolve())

    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_RUN_POLL_INTERVAL_MS)
    })

    expect(mockGetRunProgress).toHaveBeenNthCalledWith(
      2,
      'run-1',
      { since: 'cursor-1', limit: 500 },
      expect.any(AbortSignal),
    )
    unmount()
  })

  it('isolates cursors when the run ID changes while preserving same-run polling', async () => {
    jest.useFakeTimers()
    mockGetRunProgress
      .mockResolvedValueOnce(makePage({ next_cursor: 'run-a-cursor' }))
      .mockResolvedValueOnce(makePage({
        run: { ...makePage().run, scenario_result_id: 'run-b' },
        next_cursor: 'run-b-cursor',
      }))
      .mockResolvedValueOnce(makePage({
        run: { ...makePage().run, scenario_result_id: 'run-b' },
        plan: null,
        next_cursor: 'run-b-next-cursor',
      }))

    const { rerender, unmount } = renderHook(
      ({ runId }) => useScenarioRunProgress(runId),
      { initialProps: { runId: 'run-a' } },
    )
    await act(async () => Promise.resolve())

    rerender({ runId: 'run-b' })
    await act(async () => Promise.resolve())

    expect(mockGetRunProgress).toHaveBeenNthCalledWith(
      2,
      'run-b',
      { since: undefined, limit: 500 },
      expect.any(AbortSignal),
    )

    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_RUN_POLL_INTERVAL_MS)
    })
    expect(mockGetRunProgress).toHaveBeenNthCalledWith(
      3,
      'run-b',
      { since: 'run-b-cursor', limit: 500 },
      expect.any(AbortSignal),
    )
    unmount()
  })

  it('transitions a created run to active progress on a later poll', async () => {
    jest.useFakeTimers()
    mockGetRunProgress
      .mockResolvedValueOnce(makePage({
        run: { ...makePage().run, status: 'CREATED' },
        next_cursor: 'cursor-1',
      }))
      .mockResolvedValueOnce(makePage({
        run: { ...makePage().run, status: 'IN_PROGRESS' },
        plan: null,
        next_cursor: 'cursor-1',
      }))

    const { result, unmount } = renderHook(() => useScenarioRunProgress('run-1'))
    await waitFor(() => expect(result.current.state.run?.status).toBe('CREATED'))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_RUN_POLL_INTERVAL_MS)
    })

    expect(result.current.state.run?.status).toBe('IN_PROGRESS')
    unmount()
  })

  it('does not overlap polls while a request remains in flight', async () => {
    jest.useFakeTimers()
    let resolvePoll: ((page: ScenarioRunProgress) => void) | undefined
    mockGetRunProgress
      .mockResolvedValueOnce(makePage({ next_cursor: 'cursor-1' }))
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolvePoll = resolve
      }))

    const { unmount } = renderHook(() => useScenarioRunProgress('run-1'))
    await act(async () => Promise.resolve())
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_RUN_POLL_INTERVAL_MS * 4)
    })

    expect(mockGetRunProgress).toHaveBeenCalledTimes(2)
    await act(async () => {
      resolvePoll?.(makePage({ plan: null, next_cursor: 'cursor-2' }))
    })
    unmount()
  })

  it('stops permanently when a terminal page is received', async () => {
    jest.useFakeTimers()
    mockGetRunProgress.mockResolvedValueOnce(makePage({
      run: { ...makePage().run, status: 'COMPLETED', completed_at: '2026-01-01T00:01:00Z' },
    }))

    const { unmount } = renderHook(() => useScenarioRunProgress('run-1'))
    await act(async () => Promise.resolve())
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_RUN_POLL_INTERVAL_MS * 3)
    })

    expect(mockGetRunProgress).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('aborts a stale request when the route ID changes', async () => {
    const signals: AbortSignal[] = []
    mockGetRunProgress.mockImplementation(
      (_runId: string, _params: unknown, signal: AbortSignal) => {
        signals.push(signal)
        return new Promise(() => {})
      },
    )

    const { rerender, unmount } = renderHook(
      ({ runId }) => useScenarioRunProgress(runId),
      { initialProps: { runId: 'run-1' } },
    )
    await waitFor(() => expect(signals).toHaveLength(1))

    rerender({ runId: 'run-2' })

    expect(signals[0].aborted).toBe(true)
    await waitFor(() => expect(signals).toHaveLength(2))
    unmount()
    expect(signals[1].aborted).toBe(true)
  })

  it('treats a blank run ID as not found without issuing a request', async () => {
    const { result } = renderHook(() => useScenarioRunProgress('   '))

    await waitFor(() => expect(result.current.state.loadStatus).toBe('not-found'))
    expect(mockGetRunProgress).not.toHaveBeenCalled()
  })

  it('treats an HTTP 404 as not found', async () => {
    mockGetRunProgress.mockRejectedValueOnce({
      isAxiosError: true,
      response: {
        status: 404,
        data: { detail: 'Scenario run not found.' },
      },
    })

    const { result } = renderHook(() => useScenarioRunProgress('missing-run'))

    await waitFor(() => expect(result.current.state.loadStatus).toBe('not-found'))
    expect(result.current.state.error).toBe('Scenario run not found.')
  })

  it('ignores a stale page that resolves after the run ID changes', async () => {
    let resolveOldRequest: ((page: ScenarioRunProgress) => void) | undefined
    mockGetRunProgress.mockImplementation((runId: string) => {
      if (runId === 'run-1') {
        return new Promise((resolve) => {
          resolveOldRequest = resolve
        })
      }
      return Promise.resolve(makePage({
        run: {
          ...makePage().run,
          scenario_result_id: 'run-2',
        },
      }))
    })

    const { result, rerender, unmount } = renderHook(
      ({ runId }) => useScenarioRunProgress(runId),
      { initialProps: { runId: 'run-1' } },
    )
    await waitFor(() => expect(mockGetRunProgress).toHaveBeenCalledTimes(1))
    rerender({ runId: 'run-2' })
    await waitFor(() => expect(result.current.state.run?.scenario_result_id).toBe('run-2'))

    await act(async () => {
      resolveOldRequest?.(makePage())
    })
    expect(result.current.state.run?.scenario_result_id).toBe('run-2')
    unmount()
  })

  it('ignores a stale failure after the run ID changes', async () => {
    let rejectOldRequest: ((reason?: unknown) => void) | undefined
    mockGetRunProgress.mockImplementation((runId: string) => {
      if (runId === 'run-1') {
        return new Promise((_resolve, reject) => {
          rejectOldRequest = reject
        })
      }
      return Promise.resolve(makePage({
        run: {
          ...makePage().run,
          scenario_result_id: 'run-2',
        },
      }))
    })

    const { result, rerender, unmount } = renderHook(
      ({ runId }) => useScenarioRunProgress(runId),
      { initialProps: { runId: 'run-1' } },
    )
    await waitFor(() => expect(mockGetRunProgress).toHaveBeenCalledTimes(1))
    rerender({ runId: 'run-2' })
    await waitFor(() => expect(result.current.state.run?.scenario_result_id).toBe('run-2'))

    await act(async () => {
      rejectOldRequest?.(new Error('late failure'))
    })
    expect(result.current.state.error).toBeNull()
    unmount()
  })

  it('auto-retries once from the last good cursor before pausing live updates', async () => {
    jest.useFakeTimers()
    mockGetRunProgress
      .mockResolvedValueOnce(makePage({ next_cursor: 'cursor-1' }))
      .mockRejectedValueOnce(new Error('temporary failure'))
      .mockResolvedValueOnce(makePage({ plan: null, next_cursor: 'cursor-2' }))

    const { result, unmount } = renderHook(() => useScenarioRunProgress('run-1'))
    await act(async () => Promise.resolve())
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_RUN_POLL_INTERVAL_MS)
    })
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_RUN_POLL_INTERVAL_MS)
    })

    expect(mockGetRunProgress).toHaveBeenNthCalledWith(
      3,
      'run-1',
      { since: 'cursor-1', limit: 500 },
      expect.any(AbortSignal),
    )
    expect(result.current.state.stale).toBe(false)
    unmount()
  })

  it('pauses live updates when the automatic retry also fails', async () => {
    jest.useFakeTimers()
    mockGetRunProgress
      .mockResolvedValueOnce(makePage({ next_cursor: 'cursor-1' }))
      .mockRejectedValueOnce(new Error('temporary failure'))
      .mockRejectedValueOnce(new Error('still failing'))
      .mockResolvedValueOnce(makePage({ plan: null, next_cursor: 'cursor-2' }))

    const { result, unmount } = renderHook(() => useScenarioRunProgress('run-1'))
    await act(async () => Promise.resolve())
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_RUN_POLL_INTERVAL_MS)
    })
    await act(async () => {
      await jest.advanceTimersByTimeAsync(SCENARIO_RUN_POLL_INTERVAL_MS)
    })
    expect(result.current.state.stale).toBe(true)

    act(() => result.current.retry())
    await act(async () => Promise.resolve())

    expect(mockGetRunProgress).toHaveBeenNthCalledWith(
      4,
      'run-1',
      { since: 'cursor-1', limit: 500 },
      expect.any(AbortSignal),
    )
    unmount()
  })

  it('fetches final persisted deltas after applying a cancellation summary', async () => {
    mockGetRunProgress
      .mockResolvedValueOnce(makePage({ next_cursor: 'cursor-1' }))
      .mockResolvedValueOnce(makePage({
        run: {
          ...makePage().run,
          status: 'CANCELLED',
          completed_at: '2026-01-01T00:00:02Z',
        },
        plan: null,
        results: [makeResult('final-attempt')],
        next_cursor: 'cursor-2',
      }))

    const { result, unmount } = renderHook(() => useScenarioRunProgress('run-1'))
    await waitFor(() => expect(mockGetRunProgress).toHaveBeenCalledTimes(1))

    act(() => {
      result.current.applyRunSummary(makeSummary({
        status: 'CANCELLED',
        updated_at: '2026-01-01T00:00:02Z',
        completed_attacks: 1,
        objective_achieved_rate: 100,
      }))
    })

    await waitFor(() => expect(result.current.state.results).toEqual([makeResult('final-attempt')]))
    expect(mockGetRunProgress).toHaveBeenLastCalledWith(
      'run-1',
      { since: 'cursor-1', limit: 500 },
      expect.any(AbortSignal),
    )
    unmount()
  })

  it('applies a nonterminal run summary without forcing a catch-up request', async () => {
    mockGetRunProgress.mockResolvedValueOnce(makePage({ next_cursor: 'cursor-1' }))
    const { result, unmount } = renderHook(() => useScenarioRunProgress('run-1'))
    await waitFor(() => expect(mockGetRunProgress).toHaveBeenCalledTimes(1))
    mockGetRunProgress.mockClear()

    act(() => {
      result.current.applyRunSummary(makeSummary({
        status: 'IN_PROGRESS',
        updated_at: '2026-01-01T00:00:02Z',
      }))
    })

    expect(result.current.state.run?.status).toBe('IN_PROGRESS')
    expect(mockGetRunProgress).not.toHaveBeenCalled()
    unmount()
  })
})
