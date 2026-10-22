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
