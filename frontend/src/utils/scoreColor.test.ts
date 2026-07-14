import { getScoreColor, normalizeScoreValue } from './scoreColor'

const WHITE = '#ffffff'
const GREY = 'rgb(97, 97, 97)'
const RED = 'rgb(197, 15, 31)'
const GREEN = 'rgb(16, 124, 16)'

describe('normalizeScoreValue', () => {
  it('maps true/false verdicts to the [0, 1] extremes', () => {
    expect(normalizeScoreValue('true_false', 'true')).toBe(1)
    expect(normalizeScoreValue('true_false', 'false')).toBe(0)
  })

  it('is case- and whitespace-insensitive for true/false', () => {
    expect(normalizeScoreValue('true_false', '  TRUE ')).toBe(1)
    expect(normalizeScoreValue('true_false', 'False')).toBe(0)
  })

  it('returns null for an uninterpretable true/false value', () => {
    expect(normalizeScoreValue('true_false', 'maybe')).toBeNull()
  })

  it('parses float_scale values and clamps them to [0, 1]', () => {
    expect(normalizeScoreValue('float_scale', '0')).toBe(0)
    expect(normalizeScoreValue('float_scale', '0.75')).toBe(0.75)
    expect(normalizeScoreValue('float_scale', '1')).toBe(1)
    expect(normalizeScoreValue('float_scale', '1.5')).toBe(1)
    expect(normalizeScoreValue('float_scale', '-0.5')).toBe(0)
  })

  it('returns null for non-numeric float_scale values and unknown types', () => {
    expect(normalizeScoreValue('float_scale', 'abc')).toBeNull()
    expect(normalizeScoreValue('unknown', '0.5')).toBeNull()
  })
})

describe('getScoreColor', () => {
  it('renders a true verdict as the most saturated green', () => {
    expect(getScoreColor('true_false', 'true')).toEqual({ background: GREEN, foreground: WHITE })
  })

  it('renders a false verdict as the most saturated red', () => {
    expect(getScoreColor('true_false', 'false')).toEqual({ background: RED, foreground: WHITE })
  })

  it('renders the float midpoint (0.5) as neutral grey', () => {
    expect(getScoreColor('float_scale', '0.5')).toEqual({ background: GREY, foreground: WHITE })
  })

  it('renders a float above the midpoint as a muted green', () => {
    expect(getScoreColor('float_scale', '0.75')).toEqual({ background: 'rgb(57, 111, 57)', foreground: WHITE })
  })

  it('renders a float below the midpoint as a muted red', () => {
    expect(getScoreColor('float_scale', '0.25')).toEqual({ background: 'rgb(147, 56, 64)', foreground: WHITE })
  })

  it('falls back to grey for missing or uninterpretable scores', () => {
    expect(getScoreColor(null, null)).toEqual({ background: GREY, foreground: WHITE })
    expect(getScoreColor('true_false', null)).toEqual({ background: GREY, foreground: WHITE })
    expect(getScoreColor('unknown', '0.5')).toEqual({ background: GREY, foreground: WHITE })
    expect(getScoreColor('float_scale', 'not-a-number')).toEqual({ background: GREY, foreground: WHITE })
  })
})
