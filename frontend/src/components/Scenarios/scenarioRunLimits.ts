/**
 * Launch-owned execution bounds, mirroring the server-side `RunScenarioRequest`
 * field constraints. Shared by every surface that starts a scenario run so the
 * client never offers a value the API will reject.
 */

export const MIN_MAX_CONCURRENCY = 1
export const MAX_MAX_CONCURRENCY = 100
export const MIN_MAX_RETRIES = 0
export const MAX_MAX_RETRIES = 20
export const DEFAULT_MAX_CONCURRENCY = 10
export const DEFAULT_MAX_RETRIES = 0

/** Resolves a Fluent `SpinButton` change event to a numeric value, preferring the parsed `value` over the raw `displayValue`. */
export function resolveSpinButtonValue(
  data: { value?: number | null; displayValue?: string },
  previous: number,
): number {
  if (typeof data.value === 'number') {
    return data.value
  }
  const parsed = data.displayValue !== undefined ? Number(data.displayValue) : NaN
  return Number.isFinite(parsed) ? parsed : previous
}
