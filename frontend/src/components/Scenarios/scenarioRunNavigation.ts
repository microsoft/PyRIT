import { suiteExecutionRoutePath } from '@/components/ScenarioSuites/suiteRoutes'

const LAUNCH_PATH = '/scanner/launch'

/** The configure page's way back to the Launch tab. */
export const BACK_TO_LAUNCH_LABEL = 'Back to Launch'

/** Where the run page was opened from, carried in router location state. */
export interface ScenarioRunNavigationState {
  fromScenarioHistory?: boolean
  scenarioHistorySearch?: string
  scenarioName?: string
  suiteExecutionId?: string
  suiteName?: string
}

/** The Launch tab's query, such as its open modules, carried in router location state to the configure page. */
export interface ScenarioLaunchNavigationState {
  launchSearch?: string
}

interface ScenarioRunBackLink {
  readonly path: string
  readonly label: string
}

/** Return to the Launch tab as it was when a scenario was opened from it. */
export function launchBackPath(state: ScenarioLaunchNavigationState | null): string {
  const search = state?.launchSearch
  return search?.startsWith('?') ? `${LAUNCH_PATH}${search}` : LAUNCH_PATH
}

/** Return to the preset run, history filters, or scanner that opened the run. */
export function scenarioRunBackLink(state: ScenarioRunNavigationState | null): ScenarioRunBackLink {
  if (state?.suiteExecutionId) {
    return {
      path: suiteExecutionRoutePath(state.suiteExecutionId),
      label: `Back to ${state.suiteName ?? 'preset run'}`,
    }
  }
  if (state?.fromScenarioHistory || state?.scenarioHistorySearch) {
    return { path: `/scanner/history${state.scenarioHistorySearch ?? ''}`, label: 'Back to scans' }
  }
  if (state?.scenarioName) {
    return { path: `${LAUNCH_PATH}/${encodeURIComponent(state.scenarioName)}`, label: 'Back to scenario' }
  }
  return { path: LAUNCH_PATH, label: BACK_TO_LAUNCH_LABEL }
}
