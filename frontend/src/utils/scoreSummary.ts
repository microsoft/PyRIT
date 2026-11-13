import type { BackendScore, ScenarioProgressScore, ScoreSummary } from '@/types'

export function scoreSummaryFromBackendScore(score: BackendScore): ScoreSummary {
  return {
    scorer: score.scorer_type,
    value: score.score_value,
    status: score.status,
    rationale: score.score_rationale,
    categories: score.score_category,
    isObjective: score.is_objective_score,
  }
}

/** A scan result's score is the attack's objective score. */
export function scoreSummaryFromProgressScore(score: ScenarioProgressScore): ScoreSummary {
  return {
    scorer: score.scorer_name,
    value: score.score_value,
    status: score.status,
    rationale: score.score_rationale,
    categories: score.score_category,
    isObjective: true,
  }
}

/** An undetermined score has no value, so name its status instead. */
export function scoreSummaryValue(score: ScoreSummary): string {
  return score.value ?? score.status ?? ''
}
