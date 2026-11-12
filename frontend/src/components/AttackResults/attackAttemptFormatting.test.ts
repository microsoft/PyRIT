import { formatAttemptTiming, formatRetries, formatTimestamp, objectivePreview } from './attackAttemptFormatting'

const LONE_SURROGATE_REGEX = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/

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

describe('formatRetries', () => {
  it('says when there were no retries and pluralizes the rest', () => {
    expect(formatRetries(0)).toBe('No retries')
    expect(formatRetries(1)).toBe('1 retry')
    expect(formatRetries(3)).toBe('3 retries')
  })
})

describe('formatAttemptTiming', () => {
  it('joins duration, retries, and timestamp on one line', () => {
    const timestamp = '2026-01-01T00:04:30Z'
    expect(formatAttemptTiming({ execution_time_ms: 270_000, total_retries: 0, timestamp }))
      .toBe(`4m 30s · No retries · ${formatTimestamp(timestamp)}`)
    expect(formatAttemptTiming({ execution_time_ms: 5_000, total_retries: 2, timestamp }))
      .toBe(`5s · 2 retries · ${formatTimestamp(timestamp)}`)
  })
})
