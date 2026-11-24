/** The Scanner tab that lists presets (backend scenario suites) ready to launch. */
export const SCANNER_PRESETS_ROUTE = '/scanner/presets'

/** Presets launch from the Scanner, so their progress pages live under it. */
const SUITE_EXECUTIONS_ROUTE = '/scanner/history/suites/executions'

export function suiteExecutionRoutePath(executionId: string): string {
  return `${SUITE_EXECUTIONS_ROUTE}/${encodeURIComponent(executionId)}`
}
