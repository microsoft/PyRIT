/**
 * Presets live under the registry because a preset is a stored, reusable
 * configuration alongside targets and converters, not a property of one scan.
 *
 * Editing uses a trailing `/edit` segment rather than bare
 * `/registry/scenario-presets/:presetName` so the static create route cannot
 * shadow a preset whose name happens to be `new` — preset names are
 * user-authored and `new` matches the server's name pattern.
 */

export const PRESETS_ROUTE = '/registry/scenario-presets'
export const NEW_PRESET_ROUTE = `${PRESETS_ROUTE}/new`

/** Where presets used to live. Kept as a redirect so bookmarked links still resolve. */
export const LEGACY_PRESETS_ROUTE = '/scanner/presets'

export function presetEditorRoutePath(name: string): string {
  return `${PRESETS_ROUTE}/${encodeURIComponent(name)}/edit`
}

/** Names the preset that pre-fills the Scanner launch form for its scenario. */
export const PRESET_QUERY_PARAM = 'preset'

/**
 * Opens the Scanner launch form for the preset's scenario, pre-filled from the preset.
 * The preset rides in the query string rather than the path so no preset name can
 * collide with another `/scanner/...` route.
 */
export function presetScannerRoutePath(scenarioName: string, presetName: string): string {
  return `/scanner/${encodeURIComponent(scenarioName)}?${PRESET_QUERY_PARAM}=${encodeURIComponent(presetName)}`
}
