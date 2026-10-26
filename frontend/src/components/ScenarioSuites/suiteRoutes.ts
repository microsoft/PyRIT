export const SUITES_ROUTE = '/registry/scenario-suites'
export const NEW_SUITE_ROUTE = `${SUITES_ROUTE}/new`

export function suiteEditorRoutePath(name: string): string {
  return `${SUITES_ROUTE}/${encodeURIComponent(name)}/edit`
}
