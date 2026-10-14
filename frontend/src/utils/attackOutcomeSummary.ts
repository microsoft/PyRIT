import type { AttackOutcome, ScenarioProgressResult } from '@/types'

/**
 * Fixed presentation order for attack outcomes: most-to-least actionable, with
 * the two "no verdict" states (undetermined, error) last. Every consumer of a
 * summary iterates in this order so bars and legends stay stable across runs.
 */
export const OUTCOME_ORDER: readonly AttackOutcome[] = ['success', 'failure', 'undetermined', 'error']

/** Human-readable labels for each outcome, used in legends and screen-reader text. */
export const OUTCOME_LABELS: Record<AttackOutcome, string> = {
  success: 'Success',
  failure: 'Failure',
  undetermined: 'Undetermined',
  error: 'Error',
}

export interface AttackOutcomeSummary {
  /** Count of executions for each outcome. */
  readonly counts: Record<AttackOutcome, number>
  /** Total number of executions counted (sum of all outcome counts). */
  readonly total: number
  /** Share of the total for each outcome, in the range 0-100. Zero when total is 0. */
  readonly percentages: Record<AttackOutcome, number>
}

function emptyCounts(): Record<AttackOutcome, number> {
  return { success: 0, failure: 0, undetermined: 0, error: 0 }
}

function isAttackOutcome(value: unknown): value is AttackOutcome {
  return typeof value === 'string' && (OUTCOME_ORDER as readonly string[]).includes(value)
}

/**
 * Aggregate a list of attempt results into per-outcome counts and percentages.
 *
 * Pure and defensive: results whose `outcome` is missing or unrecognised are
 * ignored rather than throwing, so a malformed backend payload degrades to a
 * smaller total instead of breaking the run page.
 */
export function summarizeAttackOutcomes(
  results: readonly Pick<ScenarioProgressResult, 'outcome'>[],
): AttackOutcomeSummary {
  const counts = emptyCounts()
  for (const result of results) {
    if (isAttackOutcome(result?.outcome)) {
      counts[result.outcome] += 1
    }
  }

  let total = 0
  for (const outcome of OUTCOME_ORDER) {
    total += counts[outcome]
  }

  const percentages = emptyCounts()
  if (total > 0) {
    for (const outcome of OUTCOME_ORDER) {
      percentages[outcome] = (counts[outcome] / total) * 100
    }
  }

  return { counts, total, percentages }
}
