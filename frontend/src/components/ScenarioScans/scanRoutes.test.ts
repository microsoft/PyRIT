import { NEW_SCAN_ROUTE, SCANS_ROUTE, scanEditorRoutePath } from './scanRoutes'

describe('scanEditorRoutePath', () => {
  it('builds an edit route under the scan library', () => {
    expect(scanEditorRoutePath('nightly_scan')).toBe('/registry/scenario-scans/nightly_scan/edit')
  })

  it('escapes a name so it stays inside one path segment', () => {
    expect(scanEditorRoutePath('a/b')).toBe('/registry/scenario-scans/a%2Fb/edit')
  })

  it('does not collide with the create route for a scan named "new"', () => {
    expect(scanEditorRoutePath('new')).not.toBe(NEW_SCAN_ROUTE)
    expect(scanEditorRoutePath('new').startsWith(`${NEW_SCAN_ROUTE}/`)).toBe(true)
  })

  it('nests both routes under the registry', () => {
    expect(SCANS_ROUTE.startsWith('/registry/')).toBe(true)
    expect(NEW_SCAN_ROUTE.startsWith('/registry/')).toBe(true)
  })
})
