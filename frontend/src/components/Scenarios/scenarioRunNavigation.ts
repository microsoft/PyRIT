import { suiteExecutionRoutePath } from '@/components/ScenarioSuites/suiteRoutes'

/** Where the run page was opened from, carried in router location state. */
export interface ScenarioRunNavigationState {
  fromScenarioHistory?: boolean
  scenarioHistorySearch?: string
  scenarioName?: string
  suiteExecutionId?: string
  suiteName?: string
}

interface ScenarioRunBackLink {
  readonly path: string
  readonly label: string
}

/** Return to the suite, history filters, or scanner that opened the run. */
export function scenarioRunBackLink(state: ScenarioRunNavigationState | null): ScenarioRunBackLink {
  if (state?.suiteExecutionId) {
    return {
      path: suiteExecutionRoutePath(state.suiteExecutionId),
      label: `Back to ${state.suiteName ?? 'suite execution'}`,
    }
  }
  if (state?.fromScenarioHistory || state?.scenarioHistorySearch) {
    return { path: `/scanner/history${state.scenarioHistorySearch ?? ''}`, label: 'Back to scans' }
  }
  if (state?.scenarioName) {
    return { path: `/scanner/launch/${encodeURIComponent(state.scenarioName)}`, label: 'Back to scenario' }
  }
  return { path: '/scanner/launch', label: 'Back to scanners' }
}
