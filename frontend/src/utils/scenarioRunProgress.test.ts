import type {
  ScenarioProgressResult,
  ScenarioRunPlan,
  ScenarioRunProgress,
} from '@/types'

import {
  INITIAL_SCENARIO_RUN_PROGRESS_STATE,
  getElapsedMilliseconds,
  getEtaMilliseconds,
  scenarioRunProgressReducer,
} from './scenarioRunProgress'

const PLAN: ScenarioRunPlan = {
  version: 1,
  scenario_registry_name: 'test.scenario',
  atomic_groups: [],
  seed_groups: [],
}

const SUMMARY = {
  overall: {
    completed: 1,
    planned: 3,
    succeeded: 1,
    success_percentage: 100,
    errors: 0,
    retries: 0,
  },
  techniques: [],
  seed_groups: [],
  atomic_groups: [],
}

function makeResult(id: string, minute: number): ScenarioProgressResult {
  return {
    attack_result_id: id,
    atomic_group_id: 'group-a',
    atomic_attack_name: 'attack-a',
    seed_group_id: 'seed-1',
    outcome: 'success',
    execution_time_ms: 1_000,
    timestamp: `2026-01-01T00:${String(minute).padStart(2, '0')}:00Z`,
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
      started_at: '2026-01-01T00:00:00Z',
    },
    plan: PLAN,
    results: [],
    summary: SUMMARY,
    next_cursor: 'cursor-1',
    has_more: false,
    plan_complete: true,
    ...overrides,
  }
}

describe('scenarioRunProgressReducer', () => {
  it('merges duplicated pages idempotently and uses the latest backend summary', () => {
    const result = makeResult('attempt-1', 1)
    const first = scenarioRunProgressReducer(INITIAL_SCENARIO_RUN_PROGRESS_STATE, {
      type: 'apply-page',
      page: makePage({ results: [result] }),
      fresh: true,
    })
    const updatedSummary = {
      ...SUMMARY,
      overall: { ...SUMMARY.overall, completed: 2 },
    }
    const duplicate = scenarioRunProgressReducer(first, {
      type: 'apply-page',
      page: makePage({ plan: null, results: [result], summary: updatedSummary }),
      fresh: false,
    })

    expect(duplicate.results).toEqual([result])
    expect(duplicate.summary).toEqual(updatedSummary)
  })

  it('does not double-count overload evidence during cancellation catch-up', () => {
    const overload = {
      component_role: 'objective_target',
      count: 1,
      rate_limit_count: 1,
      server_error_count: 0,
      status_codes: [429],
      latest_timestamp: '2026-01-01T00:01:00Z',
    }
    const first = scenarioRunProgressReducer(INITIAL_SCENARIO_RUN_PROGRESS_STATE, {
      type: 'apply-page',
      page: makePage({ run: { ...makePage().run, overload_summaries: [overload] } }),
      fresh: true,
    })
    const cancelled = scenarioRunProgressReducer(first, {
      type: 'apply-run-summary',
      run: {
        scenario_result_id: 'run-1',
        scenario_name: 'TestScenario',
        scenario_registry_name: 'test.scenario',
        scenario_version: 1,
        status: 'CANCELLED',
        created_at: '2026-01-01T00:00:00Z',
        started_at: '2026-01-01T00:00:30Z',
        updated_at: '2026-01-01T00:02:00Z',
        techniques_used: [],
        total_attacks: 2,
        completed_attacks: 2,
        objective_achieved_rate: 0,
        failed_attacks: [],
        attack_retries: [],
        total_retries: 2,
        labels: {},
        overload_summaries: [{ ...overload, count: 2, rate_limit_count: 2 }],
      },
    })
    const caughtUp = scenarioRunProgressReducer(cancelled, {
      type: 'apply-page',
      page: makePage({
        plan: null,
        run: {
          ...makePage().run,
          status: 'CANCELLED',
          overload_summaries: [{
            ...overload,
            latest_timestamp: '2026-01-01T00:02:00Z',
          }],
        },
      }),
      fresh: false,
    })

    expect(cancelled.overloadSummaries[0].count).toBe(1)
    expect(cancelled.run?.started_at).toBe('2026-01-01T00:00:30Z')
    expect(caughtUp.overloadSummaries[0].count).toBe(2)
  })

  it('retains last-good data and marks it stale after a transient failure', () => {
    const first = scenarioRunProgressReducer(INITIAL_SCENARIO_RUN_PROGRESS_STATE, {
      type: 'apply-page',
      page: makePage({ results: [makeResult('attempt-1', 1)] }),
      fresh: true,
    })
    const failed = scenarioRunProgressReducer(first, {
      type: 'request-failed',
      message: 'Network unavailable',
      notFound: false,
    })

    expect(failed.results).toHaveLength(1)
    expect(failed.loadStatus).toBe('ready')
    expect(failed.stale).toBe(true)
    expect(failed.error).toBe('Network unavailable')
  })
})

describe('scenario run timing', () => {
  it('uses now for active elapsed time and completed_at for terminal elapsed time', () => {
    const active = {
      ...makePage().run,
      created_at: '2026-01-01T00:00:00Z',
      started_at: '2026-01-01T01:00:00Z',
    }
    expect(getElapsedMilliseconds(active, Date.parse('2026-01-01T01:05:00Z'))).toBe(300_000)

    const terminal = {
      ...active,
      status: 'COMPLETED' as const,
      completed_at: '2026-01-01T01:03:00Z',
    }
    expect(getElapsedMilliseconds(terminal, Date.parse('2026-01-01T01:05:00Z'))).toBe(180_000)
    expect(getEtaMilliseconds(active, SUMMARY.overall, Date.parse('2026-01-01T01:02:00Z'))).toBe(240_000)
  })

  it('does not count queue wait as elapsed time or fabricate a queued ETA', () => {
    const queued = {
      ...makePage().run,
      status: 'QUEUED' as const,
      created_at: '2026-01-01T00:00:00Z',
      started_at: null,
    }
    const now = Date.parse('2026-01-01T01:00:00Z')

    expect(getElapsedMilliseconds(queued, now)).toBe(0)

    expect(getEtaMilliseconds(queued, SUMMARY.overall, now)).toBeNull()

    expect(getElapsedMilliseconds(
      { ...queued, status: 'CANCELLED', completed_at: '2026-01-01T00:30:00Z' },
      now,
    )).toBe(0)
  })

  it('calculates ETA from backend counts and hides unsafe estimates', () => {
    const active = makePage().run
    expect(getEtaMilliseconds(active, SUMMARY.overall, Date.parse('2026-01-01T00:02:00Z'))).toBe(240_000)
    expect(getEtaMilliseconds(
      active,
      { ...SUMMARY.overall, completed: 0 },
      Date.parse('2026-01-01T00:02:00Z'),
    )).toBeNull()
    expect(getEtaMilliseconds(
      active,
      { ...SUMMARY.overall, planned: null },
      Date.parse('2026-01-01T00:02:00Z'),
    )).toBeNull()
  })
})
