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
    const active = makePage().run
    expect(getElapsedMilliseconds(active, Date.parse('2026-01-01T00:05:00Z'))).toBe(300_000)

    const terminal = {
      ...active,
      status: 'COMPLETED' as const,
      completed_at: '2026-01-01T00:03:00Z',
    }
    expect(getElapsedMilliseconds(terminal, Date.parse('2026-01-01T00:05:00Z'))).toBe(180_000)
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
