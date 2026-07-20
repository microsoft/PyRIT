import { getScoreColor, normalizeScoreValue } from './scoreColor'

const WHITE = '#ffffff'
const GREY = 'rgb(97, 97, 97)'
const RED = 'rgb(188, 47, 50)'
const GREEN = 'rgb(14, 112, 14)'
const AMBER = 'rgb(196, 53, 1)'

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
  it('drives the hue from the outcome, not the score polarity', () => {
    // A refusal scorer reports "true" on a *failed* attack; the badge must stay
    // red (failure) rather than turning success-green from the raw polarity.
    expect(getScoreColor('failure', 'true_false', 'true')).toEqual({ background: RED, foreground: WHITE })
    expect(getScoreColor('success', 'true_false', 'true')).toEqual({ background: GREEN, foreground: WHITE })
  })

  it('renders a boolean verdict at full, brightest strength for either value', () => {
    // A true/false verdict is definitive, so both true and false render the
    // full hue -- e.g. a false failure is the brightest red, not a light tint.
    expect(getScoreColor('failure', 'true_false', 'false')).toEqual({ background: RED, foreground: WHITE })
    expect(getScoreColor('failure', 'true_false', 'true')).toEqual({ background: RED, foreground: WHITE })
    expect(getScoreColor('success', 'true_false', 'false')).toEqual({ background: GREEN, foreground: WHITE })
  })

  it('grades a thresholded true/false verdict by its underlying scale score', () => {
    // A FloatScaleThresholdScorer keeps the raw 0-1 score in metadata; a false
    // failure at 0.5 is a medium red, matching a plain float_scale 0.5.
    expect(getScoreColor('failure', 'true_false', 'false', 0.5)).toEqual({ background: 'rgb(161, 62, 64)', foreground: WHITE })
    // The underlying float takes precedence over the boolean full-strength rule.
    expect(getScoreColor('failure', 'true_false', 'false', 0.5)).not.toEqual({ background: RED, foreground: WHITE })
    // Clamped to [0, 1].
    expect(getScoreColor('success', 'true_false', 'true', 1.5)).toEqual({ background: GREEN, foreground: WHITE })
  })

  it('tints a float by its value: a lower value is lighter', () => {
    // A full-value success is the vivid hue; a lower value is a lighter tint of
    // the same hue (never a different color, never grey).
    expect(getScoreColor('success', 'float_scale', '1')).toEqual({ background: GREEN, foreground: WHITE })
    expect(getScoreColor('success', 'float_scale', '0.75')).toEqual({ background: 'rgb(26, 110, 26)', foreground: WHITE })
    expect(getScoreColor('failure', 'float_scale', '0.5')).toEqual({ background: 'rgb(161, 62, 64)', foreground: WHITE })
  })

  it('floors the tint so a scored float is never grey', () => {
    const lowest = getScoreColor('success', 'float_scale', '0')
    expect(lowest).toEqual({ background: 'rgb(64, 103, 64)', foreground: WHITE })
    expect(lowest.background).not.toBe(GREY)
  })

  it('renders an error outcome as amber', () => {
    expect(getScoreColor('error', 'true_false', 'true')).toEqual({ background: AMBER, foreground: WHITE })
  })

  it('renders grey for an undetermined outcome', () => {
    expect(getScoreColor('undetermined', 'true_false', 'true')).toEqual({ background: GREY, foreground: WHITE })
    expect(getScoreColor(null, 'float_scale', '0.9')).toEqual({ background: GREY, foreground: WHITE })
  })

  it('renders grey for an unscored or uninterpretable value', () => {
    expect(getScoreColor('success', null, null)).toEqual({ background: GREY, foreground: WHITE })
    expect(getScoreColor('success', 'true_false', null)).toEqual({ background: GREY, foreground: WHITE })
    expect(getScoreColor('success', 'unknown', '0.5')).toEqual({ background: GREY, foreground: WHITE })
    expect(getScoreColor('success', 'float_scale', 'not-a-number')).toEqual({ background: GREY, foreground: WHITE })
  })
})
