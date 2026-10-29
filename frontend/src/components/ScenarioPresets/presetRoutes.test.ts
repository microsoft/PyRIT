import {
  LEGACY_PRESETS_ROUTE,
  NEW_PRESET_ROUTE,
  PRESET_QUERY_PARAM,
  PRESETS_ROUTE,
  presetEditorRoutePath,
  presetScannerRoutePath,
} from './presetRoutes'

describe('presetEditorRoutePath', () => {
  it('builds an edit route under the preset library', () => {
    expect(presetEditorRoutePath('nightly_probe')).toBe('/registry/scenario-presets/nightly_probe/edit')
  })

  it('escapes a name so it stays inside one path segment', () => {
    expect(presetEditorRoutePath('a/b')).toBe('/registry/scenario-presets/a%2Fb/edit')
  })

  it('does not collide with the create route for a preset named "new"', () => {
    expect(presetEditorRoutePath('new')).not.toBe(NEW_PRESET_ROUTE)
    expect(presetEditorRoutePath('new').startsWith(`${NEW_PRESET_ROUTE}/`)).toBe(true)
  })

  it('nests both routes under the registry so the registry nav item stays selected', () => {
    expect(PRESETS_ROUTE.startsWith('/registry/')).toBe(true)
    expect(NEW_PRESET_ROUTE.startsWith('/registry/')).toBe(true)
  })

  it('keeps the old scanner location distinct so it can redirect', () => {
    expect(LEGACY_PRESETS_ROUTE).toBe('/scanner/presets')
    expect(LEGACY_PRESETS_ROUTE).not.toBe(PRESETS_ROUTE)
  })
})

describe('presetScannerRoutePath', () => {
  it('opens the scenario launch form with the preset named in the query string', () => {
    expect(presetScannerRoutePath('garak.encoding', 'nightly_probe')).toBe(
      '/scanner/garak.encoding?preset=nightly_probe',
    )
  })

  it('escapes both names', () => {
    const path = presetScannerRoutePath('a/b', 'c&d')
    const url = new URL(path, 'http://localhost')

    expect(url.pathname).toBe('/scanner/a%2Fb')
    expect(url.searchParams.get(PRESET_QUERY_PARAM)).toBe('c&d')
  })

  it('keeps a preset named like a legacy scanner route out of the path', () => {
    expect(presetScannerRoutePath('foundry', 'presets').startsWith(`${LEGACY_PRESETS_ROUTE}`)).toBe(false)
  })
})
