/**
 * The Registry's Scenario presets page lists presets (backend scenario suites); a
 * stored scenario preset is edited as one scenario of a preset.
 *
 * Editing uses a trailing `/edit` segment rather than bare
 * `/registry/scenario-presets/:presetName` so the static `/new` route, which opens the
 * New preset dialog, cannot shadow a scenario preset whose name happens to be `new`.
 */

export const PRESETS_ROUTE = '/registry/scenario-presets'
export const NEW_PRESET_ROUTE = `${PRESETS_ROUTE}/new`

/** Edits one stored scenario preset, which every preset that uses it shares. */
export function presetEditorRoutePath(name: string): string {
  return `${PRESETS_ROUTE}/${encodeURIComponent(name)}/edit`
}

/** Names the preset that pre-fills the Scanner launch form for its scenario. */
export const PRESET_QUERY_PARAM = 'preset'

/**
 * Opens the Scanner launch form for the preset's scenario, pre-filled from the preset.
 * The preset rides in the query string rather than the path so no preset name can
 * collide with another `/scanner/launch/...` route.
 */
export function presetScannerRoutePath(scenarioName: string, presetName: string): string {
  return `/scanner/launch/${encodeURIComponent(scenarioName)}?${PRESET_QUERY_PARAM}=${encodeURIComponent(presetName)}`
}
