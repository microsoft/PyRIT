import { SCANNER_PRESETS_ROUTE, suiteExecutionRoutePath } from './suiteRoutes'

describe('suite routes', () => {
  it('lists launchable presets on the Scanner Presets tab', () => {
    expect(SCANNER_PRESETS_ROUTE).toBe('/scanner/presets')
  })
})

describe('suiteExecutionRoutePath', () => {
  it('opens preset run progress under Scanner history', () => {
    expect(suiteExecutionRoutePath('execution-1')).toBe('/scanner/history/suites/executions/execution-1')
  })

  it('escapes an execution ID so it stays inside one path segment', () => {
    expect(suiteExecutionRoutePath('a/b?c')).toBe('/scanner/history/suites/executions/a%2Fb%3Fc')
  })
})
