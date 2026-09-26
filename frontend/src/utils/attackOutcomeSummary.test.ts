import { summarizeAttackOutcomes } from './attackOutcomeSummary'

describe('summarizeAttackOutcomes', () => {
  it('counts each outcome and totals them', () => {
    const summary = summarizeAttackOutcomes([
      { outcome: 'success' },
      { outcome: 'success' },
      { outcome: 'failure' },
      { outcome: 'undetermined' },
      { outcome: 'error' },
    ])

    expect(summary.counts).toEqual({ success: 2, failure: 1, undetermined: 1, error: 1 })
    expect(summary.total).toBe(5)
  })

  it('computes percentages of the total', () => {
    const summary = summarizeAttackOutcomes([
      { outcome: 'success' },
      { outcome: 'success' },
      { outcome: 'failure' },
      { outcome: 'error' },
    ])

    expect(summary.percentages.success).toBeCloseTo(50)
    expect(summary.percentages.failure).toBeCloseTo(25)
    expect(summary.percentages.error).toBeCloseTo(25)
    expect(summary.percentages.undetermined).toBe(0)
  })

  it('returns zeroed counts and percentages for an empty list', () => {
    const summary = summarizeAttackOutcomes([])

    expect(summary.total).toBe(0)
    expect(summary.counts).toEqual({ success: 0, failure: 0, undetermined: 0, error: 0 })
    expect(summary.percentages).toEqual({ success: 0, failure: 0, undetermined: 0, error: 0 })
  })

  it('ignores unrecognised or missing outcomes rather than throwing', () => {
    const summary = summarizeAttackOutcomes([
      { outcome: 'success' },
      { outcome: 'not-a-real-outcome' as never },
      { outcome: undefined as never },
    ])

    expect(summary.counts.success).toBe(1)
    expect(summary.total).toBe(1)
  })
})
