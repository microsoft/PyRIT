import {
  formatAdaptiveCapAccessibleRule,
  formatAdaptiveCapFeedback,
  formatAdaptiveCapMetadata,
} from './scenarioAdaptiveCap'

describe('scenarioAdaptiveCap', () => {
  it.each([
    [1, 1, '1 selected candidate · limit 3'],
    [4, 4, '4 selected candidates · limit 3'],
    [2, 1, '1 compatible candidate from 2 selected · limit 3'],
    [4, 2, '2 compatible candidates from 4 selected · limit 3'],
  ])(
    'formats metadata for %i selected and %i compatible candidates',
    (selectedCandidateCount, compatibleCandidateCount, expected) => {
      expect(formatAdaptiveCapMetadata({
        selectedCandidateCount,
        compatibleCandidateCount,
        limit: 3,
        effectiveMaximum: 2,
      })).toBe(expected)
    },
  )

  it('formats feedback with the effective maximum', () => {
    expect(formatAdaptiveCapFeedback({
      selectedCandidateCount: 4,
      compatibleCandidateCount: 2,
      limit: 3,
      effectiveMaximum: 2,
    })).toBe('2 compatible candidates from 4 selected · limit 3 · effective maximum 2.')
  })

  it('formats the accessible minimum rule', () => {
    expect(formatAdaptiveCapAccessibleRule({
      selectedCandidateCount: 1,
      compatibleCandidateCount: 1,
      limit: 3,
      effectiveMaximum: 1,
    })).toBe('the smaller of 1 selected candidate and limit 3')
  })
})
