import type { OutcomeStatistics, ScenarioSeedGroupProgress } from '@/types'

import { formatSeedGroupSuccess, objectivePreview } from './attackAttemptFormatting'

const LONE_SURROGATE_REGEX = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/

describe('formatSeedGroupSuccess', () => {
  const seed: ScenarioSeedGroupProgress = {
    id: 'fixture', completed: 1, planned: 1, succeeded: 1, success_percentage: 100, errors: 0, retries: 0,
  }
  const trials: OutcomeStatistics = {
    successes: 3, failures: 2, errors: 0, undetermined: 0,
    total_results: 5, total_decided: 5, decided_share: 1,
    success_rate: 0.6, success_rate_decided: 0.6, success_rate_all: 0.6,
    outcome_shares: { success: 0.6, failure: 0.4, error: 0, undetermined: 0 },
  }

  it('uses the trial fraction instead of the successful parent outcome', () => {
    expect(formatSeedGroupSuccess({ ...seed, trial_outcomes: trials })).toBe('60% (3/5 trials)')
  })

  it.each(['errors', 'undetermined'] as const)('does not present partial trial ASR with %s', (outcome: 'errors' | 'undetermined') => {
    expect(formatSeedGroupSuccess({
      ...seed, trial_outcomes: {
        ...trials, [outcome]: 1, failures: 1, total_decided: 4, decided_share: 0.8,
        success_rate: 0.75, success_rate_decided: 0.75,
        outcome_shares: { success: 0.6, failure: 0.2, error: 0, undetermined: 0, [outcome]: 0.2 },
      },
    })).toBe('Undetermined')
  })

  it('preserves ordinary and older result formatting without independent trials', () => {
    expect(formatSeedGroupSuccess(seed)).toBe('100%')
  })
})

describe('objectivePreview', () => {
  it('truncates supplementary Unicode without splitting surrogate pairs (issue #3870 reproduction)', () => {
    const objective = `${'a'.repeat(94)}😀 trailing text`
    const preview = objectivePreview(objective, 'seed-boundary')

    expect(preview).toBe(`${'a'.repeat(94)}…`)
    expect(preview.charCodeAt(94)).toBe(0x2026)
    expect(LONE_SURROGATE_REGEX.test(preview)).toBe(false)
  })

  it('preserves complete supplementary Unicode character when surrogate pair fits before boundary', () => {
    const objective = `${'a'.repeat(93)}😀 trailing text`
    const preview = objectivePreview(objective, 'seed-boundary')

    expect(preview).toBe(`${'a'.repeat(93)}😀…`)
    expect(preview.charCodeAt(93)).toBe(0xd83d)
    expect(preview.charCodeAt(94)).toBe(0xde00)
    expect(preview.charCodeAt(95)).toBe(0x2026)
    expect(LONE_SURROGATE_REGEX.test(preview)).toBe(false)
  })

  it('truncates ordinary ASCII string longer than preview limit at standard boundary', () => {
    const objective = 'a'.repeat(100)
    const preview = objectivePreview(objective, 'seed-fallback')

    expect(preview).toBe(`${'a'.repeat(95)}…`)
    expect(preview.length).toBe(96)
  })

  it('returns strings at or below the preview limit unchanged', () => {
    expect(objectivePreview('Short objective', 'fallback')).toBe('Short objective')
    expect(objectivePreview('a'.repeat(96), 'fallback')).toBe('a'.repeat(96))

    const emojiAtLimit = `${'a'.repeat(94)}😀`
    expect(emojiAtLimit.length).toBe(96)
    expect(objectivePreview(emojiAtLimit, 'fallback')).toBe(emojiAtLimit)
  })

  it('returns formatted fallback when objective is null or empty', () => {
    expect(objectivePreview(null, 'seed-123')).toBe('Objective unavailable (seed-123)')
    expect(objectivePreview('', 'seed-456')).toBe('Objective unavailable (seed-456)')
  })
})
