import { NEW_SUITE_ROUTE, SUITES_ROUTE, suiteEditorRoutePath } from './suiteRoutes'

describe('suiteEditorRoutePath', () => {
  it('builds an edit route under the suite library', () => {
    expect(suiteEditorRoutePath('nightly_suite')).toBe('/registry/scenario-suites/nightly_suite/edit')
  })

  it('escapes a name so it stays inside one path segment', () => {
    expect(suiteEditorRoutePath('a/b')).toBe('/registry/scenario-suites/a%2Fb/edit')
  })

  it('does not collide with the create route for a suite named "new"', () => {
    expect(suiteEditorRoutePath('new')).not.toBe(NEW_SUITE_ROUTE)
    expect(suiteEditorRoutePath('new').startsWith(`${NEW_SUITE_ROUTE}/`)).toBe(true)
  })

  it('nests both routes under the registry', () => {
    expect(SUITES_ROUTE.startsWith('/registry/')).toBe(true)
    expect(NEW_SUITE_ROUTE.startsWith('/registry/')).toBe(true)
  })
})
