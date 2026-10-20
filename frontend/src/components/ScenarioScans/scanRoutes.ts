export const SCANS_ROUTE = '/registry/scenario-scans'
export const NEW_SCAN_ROUTE = `${SCANS_ROUTE}/new`

export function scanEditorRoutePath(name: string): string {
  return `${SCANS_ROUTE}/${encodeURIComponent(name)}/edit`
}
