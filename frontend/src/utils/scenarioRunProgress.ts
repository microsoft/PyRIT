import type {
  ScenarioProgressCounts,
  ScenarioProgressHeader,
  ScenarioProgressResult,
  ScenarioProgressSummary,
  ScenarioRunPlan,
  ScenarioRunState,
  ScenarioRunSummary,
} from '@/types'

export type ScenarioRunLoadStatus = 'loading' | 'ready' | 'not-found' | 'error'

export interface ScenarioRunProgressState {
  readonly loadStatus: ScenarioRunLoadStatus
  readonly run: ScenarioProgressHeader | null
  readonly plan: ScenarioRunPlan | null
  readonly summary: ScenarioProgressSummary | null
  readonly planComplete: boolean
  readonly results: ScenarioProgressResult[]
  readonly error: string | null
  readonly stale: boolean
}

export type ScenarioRunProgressAction =
  | { readonly type: 'apply-page'; readonly page: import('@/types').ScenarioRunProgress; readonly fresh: boolean }
  | { readonly type: 'request-failed'; readonly message: string; readonly notFound: boolean }
  | { readonly type: 'retry' }
  | { readonly type: 'apply-run-summary'; readonly run: ScenarioRunSummary }

const TERMINAL_STATES: ReadonlySet<ScenarioRunState> = new Set(['COMPLETED', 'FAILED', 'CANCELLED'])

export const INITIAL_SCENARIO_RUN_PROGRESS_STATE: ScenarioRunProgressState = {
  loadStatus: 'loading',
  run: null,
  plan: null,
  summary: null,
  planComplete: false,
  results: [],
  error: null,
  stale: false,
}

export function isTerminalRunState(status: ScenarioRunState): boolean {
  return TERMINAL_STATES.has(status)
}

export function scenarioRunProgressReducer(
  state: ScenarioRunProgressState,
  action: ScenarioRunProgressAction,
): ScenarioRunProgressState {
  if (action.type === 'request-failed') {
    const hasGoodData = state.run !== null
    return {
      ...state,
      loadStatus: action.notFound && !hasGoodData ? 'not-found' : hasGoodData ? 'ready' : 'error',
      error: action.message,
      stale: hasGoodData,
    }
  }

  if (action.type === 'retry') {
    return {
      ...state,
      loadStatus: state.run ? 'ready' : 'loading',
      error: null,
      stale: false,
    }
  }

  if (action.type === 'apply-run-summary') {
    return {
      ...state,
      loadStatus: 'ready',
      run: {
        scenario_result_id: action.run.scenario_result_id,
        scenario_name: action.run.scenario_name,
        scenario_registry_name: action.run.scenario_registry_name,
        scenario_version: action.run.scenario_version,
        status: action.run.status,
        created_at: action.run.created_at,
        completed_at: action.run.completed_at,
        pyrit_version: action.run.pyrit_version,
        target: action.run.target,
        techniques_used: action.run.techniques_used,
        datasets_used: action.run.datasets_used ?? [],
        scenario_parameters: action.run.scenario_parameters ?? {},
        labels: action.run.labels,
      },
      error: null,
      stale: false,
    }
  }

  const shouldReset = action.fresh
  const resultsById = new Map<string, ScenarioProgressResult>()
  if (!shouldReset) {
    for (const result of state.results) {
      resultsById.set(result.attack_result_id, result)
    }
  }
  for (const result of action.page.results) {
    resultsById.set(result.attack_result_id, result)
  }

  const results = [...resultsById.values()].sort(compareAttempts)
  return {
    loadStatus: 'ready',
    run: action.page.run,
    plan: action.page.plan ?? (shouldReset ? null : state.plan),
    summary: action.page.summary,
    planComplete: action.page.plan_complete,
    results,
    error: null,
    stale: false,
  }
}

export function getElapsedMilliseconds(
  run: ScenarioProgressHeader,
  nowMilliseconds: number,
): number {
  const created = Date.parse(run.created_at)
  const terminalEnd = run.completed_at ? Date.parse(run.completed_at) : Number.NaN
  const end = isTerminalRunState(run.status) && Number.isFinite(terminalEnd)
    ? terminalEnd
    : nowMilliseconds
  if (!Number.isFinite(created) || !Number.isFinite(end)) {
    return 0
  }
  return Math.max(0, end - created)
}

export function getEtaMilliseconds(
  run: ScenarioProgressHeader,
  overall: ScenarioProgressCounts,
  nowMilliseconds: number,
): number | null {
  if (overall.planned === null || isTerminalRunState(run.status)) {
    return null
  }
  if (overall.planned <= 0 || overall.completed <= 0) {
    return null
  }
  const remaining = Math.max(0, overall.planned - overall.completed)
  if (remaining === 0) {
    return 0
  }
  const elapsed = getElapsedMilliseconds(run, nowMilliseconds)
  if (elapsed <= 0) {
    return null
  }
  const estimate = (elapsed / overall.completed) * remaining
  return Number.isFinite(estimate) && estimate >= 0 ? estimate : null
}

function compareAttempts(left: ScenarioProgressResult, right: ScenarioProgressResult): number {
  const timestampDifference = Date.parse(left.timestamp) - Date.parse(right.timestamp)
  if (Number.isFinite(timestampDifference) && timestampDifference !== 0) {
    return timestampDifference
  }
  return left.attack_result_id.localeCompare(right.attack_result_id)
}
