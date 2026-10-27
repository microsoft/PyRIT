import type { RegisteredComponentReference, ScorerInstance } from '@/types'

/** Match full configuration identity; only inherited references can use a different registry alias. */
export function findRegisteredScorer(
  reference: RegisteredComponentReference | null, scorers: ScorerInstance[], inherited = false,
): ScorerInstance | undefined {
  if (!reference) return undefined
  return scorers.find((scorer: ScorerInstance) => scorer.scorer_registry_name === reference.registry_name
    && scorer.identifier.hash === reference.identifier_hash)
    ?? (inherited ? scorers.find((scorer: ScorerInstance) => scorer.identifier.hash === reference.identifier_hash) : undefined)
}

const METRIC_LABELS: Record<string, string> = {
  accuracy: 'Accuracy',
  accuracy_standard_error: 'Accuracy std error',
  f1_score: 'F1 score',
  precision: 'Precision',
  recall: 'Recall',
  mean_absolute_error: 'Mean absolute error',
  mae_standard_error: 'MAE std error',
  krippendorff_alpha_combined: 'Agreement (Krippendorff alpha)',
  average_score_time_seconds: 'Average score time',
}

const RATIO_METRICS = new Set(['precision', 'recall', 'accuracy', 'f1_score'])
const METRIC_DESCRIPTIONS: Record<string, string> = {
  precision: 'The share of positive predictions that are correct.',
  recall: 'The share of actual positives detected by the scorer.',
  accuracy: 'The share of evaluated cases scored correctly.',
  f1_score: 'The harmonic mean of precision and recall.',
  mean_absolute_error: 'The average absolute difference from human ratings. Lower is better.',
  krippendorff_alpha_combined: "Krippendorff's alpha for agreement between human and model ratings.",
  average_score_time_seconds: 'The average seconds per scored item during the recorded evaluation.',
}

export function scorerMetricLabel(name: string): string {
  return METRIC_LABELS[name] ?? name.replace(/_/g, ' ').replace(/^./, (letter: string) => letter.toUpperCase())
}

export function formatScorerMetric(name: string, value: unknown, fallback = 'n/a'): string {
  if (typeof value === 'string') return value
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback
  if (name === 'average_score_time_seconds') {
    // Older evaluation records default an unmeasured duration to zero.
    return value > 0 ? `${value.toFixed(2)}s` : fallback
  }
  if (name === 'accuracy') return `${(value * 100).toFixed(2)}%`
  if (name.startsWith('num_')) return String(value)
  return value.toFixed(4)
}

/** Compact comparison values without changing the detailed metric views. */
export function formatScorerComparisonMetric(name: string, value: unknown): string {
  const numeric = scorerComparisonValue(name, value)
  if (numeric === undefined) return 'n/a'
  return RATIO_METRICS.has(name) ? `${(numeric * 100).toFixed(1)}%` : formatScorerMetric(name, numeric)
}

/** Keep display and sorting consistent about missing and unmeasured values. */
export function scorerComparisonValue(name: string, value: unknown): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  return name === 'average_score_time_seconds' && value <= 0 ? undefined : value
}

export function scorerMetricDescription(name: string): string {
  return METRIC_DESCRIPTIONS[name] ?? scorerMetricLabel(name)
}

/** Describe the evidence, not the scorer's transport or the type of its target. */
export function scorerEvidenceLabel(scorer: ScorerInstance): string {
  const types = new Set(scorer.scorable_types ?? [])
  const labels: string[] = []
  if (types.has('conversation')) labels.push('Conversation')
  else if (types.has('message')) labels.push('Message')
  else if (types.has('content') || types.has('content_entry')) labels.push('Content')
  if (types.has('trace')) labels.push('Trace')
  if (types.has('surface')) labels.push('Surface')
  const label = labels.join(' / ') || 'n/a'
  return scorer.condition_types?.includes('ToolsCalled') ? `${label} (Tool calls)` : label
}
