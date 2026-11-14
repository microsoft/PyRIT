import { scenarioRunBackLink } from './scenarioRunNavigation'

describe('scenarioRunBackLink', () => {
  it('returns to the suite execution that opened the run', () => {
    expect(scenarioRunBackLink({ suiteExecutionId: 'suite-1', suiteName: 'Nightly', fromScenarioHistory: true }))
      .toEqual({ path: '/scanner/history/suites/executions/suite-1', label: 'Back to Nightly' })
    expect(scenarioRunBackLink({ suiteExecutionId: 'suite-1' }).label).toBe('Back to suite execution')
  })

  it('returns to scan history with its filters', () => {
    expect(scenarioRunBackLink({ scenarioHistorySearch: '?status=FAILED' }))
      .toEqual({ path: '/scanner/history?status=FAILED', label: 'Back to scans' })
    expect(scenarioRunBackLink({ fromScenarioHistory: true }))
      .toEqual({ path: '/scanner/history', label: 'Back to scans' })
  })

  it('returns to the launched scenario, or the scanner catalog', () => {
    expect(scenarioRunBackLink({ scenarioName: 'garak/encoding' }))
      .toEqual({ path: '/scanner/launch/garak%2Fencoding', label: 'Back to scenario' })
    expect(scenarioRunBackLink(null)).toEqual({ path: '/scanner/launch', label: 'Back to scanners' })
  })
})
