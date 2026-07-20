// Colors an attack score badge so its verdict is readable at a glance.
//
// The hue comes from the attack OUTCOME (success -> green, failure -> red,
// error -> amber). The intensity grades by score value: a higher value is more
// vivid, a lower value a lighter tint (e.g. a success at 0.75 is lighter than a
// success at 1.0).
// Intensity is taken from, in order:
//   1. `underlyingFloat` -- the raw 0-1 scale score a FloatScaleThresholdScorer
//      keeps in metadata, so a thresholded true/false verdict still grades its
//      hue by how close the score was.
//   2. a float_scale score's own value.
//   3. otherwise full strength -- a pure true/false verdict is definitive.
//
// The hue endpoints match the Fluent light-theme palette foreground tokens the
// rest of the UI uses (e.g. colorPaletteRedForeground1 = #bc2f32, the same red
// as the "No target selected" banner), so a full-strength badge lines up with
// them. Values are hardcoded because the gradient is interpolated numerically.
//
// Neutral grey is reserved for the *unscored* case: no score, a value we can't
// interpret (unknown scorer type / non-numeric), or an `undetermined` outcome.

import type { AttackOutcome } from '../types'
import { resolveOutcome } from './attackOutcome'

export interface ScoreColor {
  background: string
  foreground: string
}

type Rgb = readonly [number, number, number]

const GREY: Rgb = [97, 97, 97]
const FOREGROUND = '#ffffff'

// Full-saturation hue per outcome, matching Fluent's light-theme palette
// foreground tokens. `undetermined` has no hue (renders grey).
const OUTCOME_HUE: Record<AttackOutcome, Rgb | null> = {
  success: [14, 112, 14], // #0e700e - colorPaletteGreenForeground1
  failure: [188, 47, 50], // #bc2f32 - colorPaletteRedForeground1
  error: [196, 53, 1], // #c43501 - colorPaletteDarkOrangeForeground1
  undetermined: null,
}

const INTENSITY_FLOOR = 0.4

function rgb([r, g, b]: Rgb): string {
  return `rgb(${r}, ${g}, ${b})`
}

// Picks the shade between two colors
function shadeBetween(from: Rgb, to: Rgb, t: number): Rgb {
  return [
    Math.round(from[0] + (to[0] - from[0]) * t),
    Math.round(from[1] + (to[1] - from[1]) * t),
    Math.round(from[2] + (to[2] - from[2]) * t),
  ]
}

const NEUTRAL: ScoreColor = { background: rgb(GREY), foreground: FOREGROUND }

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

// Resolves the badge color for a score.
export function getScoreColor(
  outcome?: AttackOutcome | null,
  scoreType?: string | null,
  scoreValue?: string | null,
  underlyingFloat?: number | null
): ScoreColor {
  // Unscored or uninterpretable value -> neutral.
  if (!scoreType || scoreValue == null) return NEUTRAL
  const normalized = normalizeScoreValue(scoreType, scoreValue)
  if (normalized === null) return NEUTRAL

  const hue = OUTCOME_HUE[resolveOutcome(outcome)]
  if (!hue) return NEUTRAL

  // Intensity: prefer the wrapped scale score (thresholded true/false verdict),
  // then a float_scale value, else full strength for a definitive true/false.
  const intensity =
    underlyingFloat != null
      ? Math.min(1, Math.max(0, underlyingFloat))
      : scoreType === 'true_false'
        ? 1
        : normalized
  const t = INTENSITY_FLOOR + (1 - INTENSITY_FLOOR) * intensity
  return {
    background: rgb(shadeBetween(GREY, hue, t)),
    foreground: FOREGROUND,
  }
}
