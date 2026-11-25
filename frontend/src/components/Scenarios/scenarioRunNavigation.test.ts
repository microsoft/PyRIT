import { launchBackPath, scenarioRunBackLink } from './scenarioRunNavigation'

describe('scenarioRunBackLink', () => {
  it('returns to the preset run that opened it', () => {
    expect(scenarioRunBackLink({ suiteExecutionId: 'suite-1', suiteName: 'Nightly', fromScenarioHistory: true }))
      .toEqual({ path: '/scanner/history/suites/executions/suite-1', label: 'Back to Nightly' })
    expect(scenarioRunBackLink({ suiteExecutionId: 'suite-1' }).label).toBe('Back to preset run')
  })

  it('returns to scan history with its filters', () => {
    expect(scenarioRunBackLink({ scenarioHistorySearch: '?status=FAILED' }))
      .toEqual({ path: '/scanner/history?status=FAILED', label: 'Back to scans' })
    expect(scenarioRunBackLink({ fromScenarioHistory: true }))
      .toEqual({ path: '/scanner/history', label: 'Back to scans' })
  })

  it('returns to the launched scenario, or the Launch tab', () => {
    expect(scenarioRunBackLink({ scenarioName: 'garak/encoding' }))
      .toEqual({ path: '/scanner/launch/garak%2Fencoding', label: 'Back to scenario' })
    expect(scenarioRunBackLink(null)).toEqual({ path: '/scanner/launch', label: 'Back to Launch' })
  })
})

describe('launchBackPath', () => {
  it('returns to the Launch tab with the modules that were open', () => {
    expect(launchBackPath({ launchSearch: '?open=airt&open=garak' })).toBe('/scanner/launch?open=airt&open=garak')
  })

  it.each([null, {}, { launchSearch: '' }, { launchSearch: '/elsewhere' }])(
    'returns to the plain Launch tab without a query from %p',
    (state) => {
      expect(launchBackPath(state)).toBe('/scanner/launch')
    },
  )
})
