/**
 * Helpers for validating per-target round-robin weight inputs.
 *
 * Lives in its own module (rather than inline in CreateTargetDialog) so the
 * pure-function parser can be exported and unit-tested without breaking React
 * Fast Refresh on the dialog component.
 */

// Cap for per-target round-robin weights. RoundRobinTarget itself accepts any
// positive int, but in the UI a typo like 99999999999 is almost certainly not
// what the user meant — and the backend round-robin scheduler allocates work
// roughly in proportion to weights, so values above a few hundred have no
// practical effect anyway. Tune up if a real use case needs it.
export const MAX_WEIGHT = 1000

/** Result of parsing a weight input string. */
export type WeightParse = { ok: true; value: number } | { ok: false; error: string }

/**
 * Strict weight parser used for both inline validation and submit-time validation.
 *
 * Rejects empty input, non-integers (including ``2.5`` and scientific notation
 * like ``1e10`` that ``parseInt`` would otherwise silently truncate), values
 * below 1, and values above {@link MAX_WEIGHT}. The returned ``error`` is
 * shown to the user verbatim.
 */
export function parseWeight(raw: string): WeightParse {
  if (raw === '') return { ok: false, error: 'Weight is required' }
  // Anchored digits-only regex rejects '2.5', '1e10', '-3', whitespace, etc.
  if (!/^\d+$/.test(raw)) return { ok: false, error: 'Weight must be a whole number' }
  const value = parseInt(raw, 10)
  if (value < 1) return { ok: false, error: 'Weight must be at least 1' }
  if (value > MAX_WEIGHT) return { ok: false, error: `Weight must be at most ${MAX_WEIGHT}` }
  return { ok: true, value }
}
