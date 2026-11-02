import { PRESETS_ROUTE } from '@/components/ScenarioPresets/presetRoutes'

import {
  NEW_SUITE_ROUTE,
  SUITE_EXECUTIONS_ROUTE,
  SUITE_LIBRARY_ROUTE,
  SUITES_ROUTE,
  suiteEditorRoutePath,
  suiteExecutionRoutePath,
} from './suiteRoutes'

describe('SUITE_LIBRARY_ROUTE', () => {
  it('lists suites on the scenario presets registry page', () => {
    expect(SUITE_LIBRARY_ROUTE).toBe(PRESETS_ROUTE)
    expect(SUITE_LIBRARY_ROUTE).toBe('/registry/scenario-presets')
  })

  it('keeps the old suite library path distinct so it can redirect', () => {
    expect(SUITES_ROUTE).toBe('/registry/scenario-suites')
    expect(SUITES_ROUTE).not.toBe(SUITE_LIBRARY_ROUTE)
  })
})

describe('suiteEditorRoutePath', () => {
  it('builds an edit route under the suite editor prefix', () => {
    expect(suiteEditorRoutePath('nightly_suite')).toBe('/registry/scenario-suites/nightly_suite/edit')
  })

  it('escapes a name so it stays inside one path segment', () => {
    expect(suiteEditorRoutePath('a/b')).toBe('/registry/scenario-suites/a%2Fb/edit')
  })

  it('does not collide with the create route for a suite named "new"', () => {
    expect(NEW_SUITE_ROUTE).toBe('/registry/scenario-suites/new')
    expect(suiteEditorRoutePath('new')).not.toBe(NEW_SUITE_ROUTE)
    expect(suiteEditorRoutePath('new').startsWith(`${NEW_SUITE_ROUTE}/`)).toBe(true)
  })
})

describe('suiteExecutionRoutePath', () => {
  it('opens suite progress under the Scanner, where suites launch', () => {
    expect(SUITE_EXECUTIONS_ROUTE).toBe('/scanner/suites/executions')
    expect(suiteExecutionRoutePath('execution-1')).toBe('/scanner/suites/executions/execution-1')
  })

  it('escapes an execution ID so it stays inside one path segment', () => {
    expect(suiteExecutionRoutePath('a/b?c')).toBe('/scanner/suites/executions/a%2Fb%3Fc')
  })
})
