// Maps an attack score onto a red -> grey -> green spectrum so its harm level is
// readable at a glance. The score sits on a bipolar harm axis: a "false"/harmless
// verdict is red, a "true"/harmful verdict is green, and the neutral midpoint --
// or a missing/unparseable score -- is grey. Normalized [0, 1] score values are
// remapped to [-1, 1] so the 0.5 threshold lands exactly on grey and the color
// grows more vivid as the score moves toward either extreme.
//
// A continuous spectrum can't be expressed with the discrete Fluent token
// palette, so the endpoints are fixed RGB values chosen to match Fluent's
// neutral/red/green foreground colors, interpolated at render time.

export interface ScoreColor {
  background: string
  foreground: string
}

type Rgb = readonly [number, number, number]

const GREY: Rgb = [97, 97, 97]
const RED: Rgb = [197, 15, 31]
const GREEN: Rgb = [16, 124, 16]
const FOREGROUND = '#ffffff'

function rgb(r: number, g: number, b: number): string {
  return `rgb(${r}, ${g}, ${b})`
}

function lerp(from: number, to: number, t: number): number {
  return Math.round(from + (to - from) * t)
}

const NEUTRAL: ScoreColor = { background: rgb(GREY[0], GREY[1], GREY[2]), foreground: FOREGROUND }

// Resolves a score's position on a normalized [0, 1] scale, or null when the
// value can't be interpreted (unknown scorer type or a non-numeric value).
export function normalizeScoreValue(scoreType: string, scoreValue: string): number | null {
  if (scoreType === 'true_false') {
    const normalized = scoreValue.trim().toLowerCase()
    if (normalized === 'true') return 1
    if (normalized === 'false') return 0
    return null
  }
  if (scoreType === 'float_scale') {
    const parsed = Number.parseFloat(scoreValue)
    if (Number.isNaN(parsed)) return null
    return Math.min(1, Math.max(0, parsed))
  }
  return null
}

// Resolves the spectrum color for a score. Absent or unparseable scores render grey.
export function getScoreColor(scoreType?: string | null, scoreValue?: string | null): ScoreColor {
  if (!scoreType || scoreValue == null) return NEUTRAL

  const normalized = normalizeScoreValue(scoreType, scoreValue)
  if (normalized === null) return NEUTRAL

  const bipolar = normalized * 2 - 1
  const magnitude = Math.abs(bipolar)
  const target = bipolar < 0 ? RED : GREEN

  return {
    background: rgb(
      lerp(GREY[0], target[0], magnitude),
      lerp(GREY[1], target[1], magnitude),
      lerp(GREY[2], target[2], magnitude)
    ),
    foreground: FOREGROUND,
  }
}
