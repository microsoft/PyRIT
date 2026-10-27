import { PRESETS_ROUTE } from '@/components/ScenarioPresets/presetRoutes'

/** Suites are listed with the presets they group, on the Scenario presets registry page. */
export const SUITE_LIBRARY_ROUTE = PRESETS_ROUTE

/**
 * Prefix of the suite editor routes. The bare path was the suite library before suites
 * joined the preset page, so it redirects there to keep bookmarked links working.
 */
export const SUITES_ROUTE = '/registry/scenario-suites'
export const NEW_SUITE_ROUTE = `${SUITES_ROUTE}/new`

export function suiteEditorRoutePath(name: string): string {
  return `${SUITES_ROUTE}/${encodeURIComponent(name)}/edit`
}

/** Suites launch from the Scanner, so their progress pages live under it. */
export const SUITE_EXECUTIONS_ROUTE = '/scanner/suites/executions'

export function suiteExecutionRoutePath(executionId: string): string {
  return `${SUITE_EXECUTIONS_ROUTE}/${encodeURIComponent(executionId)}`
}
