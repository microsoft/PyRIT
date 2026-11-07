import type { ScenarioProgressCounts, ScenarioProgressResult } from '@/types'
import { formatAnalyticsPercent } from '@/utils/attackAnalytics'

const OBJECTIVE_PREVIEW_LENGTH = 96

export function formatProgressSuccess(counts: ScenarioProgressCounts): string {
  if (counts.outcomes) {
    return counts.outcomes.success_rate_decided === null ? 'Undetermined'
      : formatAnalyticsPercent(counts.outcomes.success_rate_decided)
  }
  return counts.success_percentage === null ? 'Undetermined' : `${counts.success_percentage}%`
}

/**
 * Formatting for a single attack attempt.
 *
 * These live beside the attempt components rather than in a page so that every
 * surface showing an attempt renders it identically.
 */

export function formatAttackSuccess(outcome: ScenarioProgressResult['outcome']): string {
  if (outcome === 'success') {
    return 'True'
  }
  if (outcome === 'failure') {
    return 'False'
  }
  return outcome.replace(/^\w/, (letter) => letter.toUpperCase())
}

/** The attack's outcome as a word, for places headed "Attack outcome" rather than "Attack success". */
export function formatAttackOutcome(outcome: ScenarioProgressResult['outcome']): string {
  return outcome.replace(/^\w/, (letter) => letter.toUpperCase())
}

export function formatScore(score: ScenarioProgressResult['score']): string {
  if (!score || score.status === 'undetermined') {
    return 'Undetermined'
  }
  return score.score_value ?? 'Unavailable'
}

export function formatScoreRationale(rationale: string | null | undefined): string {
  return rationale?.trim() || 'No rationale is available.'
}

export function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) {
    return 'Unavailable'
  }
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatDuration(milliseconds: number): string {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) {
    return 'Unavailable'
  }
  const totalSeconds = Math.floor(milliseconds / 1_000)
  const hours = Math.floor(totalSeconds / 3_600)
  const minutes = Math.floor((totalSeconds % 3_600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) {
    return `${hours}h ${minutes}m`
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`
  }
  return `${seconds}s`
}

export function formatRetries(retries: number): string {
  if (retries === 0) return 'No retries'
  return `${retries} ${retries === 1 ? 'retry' : 'retries'}`
}

/** One line of attempt timing, such as "4m 30s · No retries · Jan 1, 2026, 12:00:05 AM". */
export function formatAttemptTiming(
  attempt: Pick<ScenarioProgressResult, 'execution_time_ms' | 'total_retries' | 'timestamp'>,
): string {
  return [
    formatDuration(attempt.execution_time_ms),
    formatRetries(attempt.total_retries),
    formatTimestamp(attempt.timestamp),
  ].join(' · ')
}

export function objectivePreview(objective: string | null, fallbackId: string): string {
  if (!objective) {
    return `Objective unavailable (${fallbackId})`
  }
  if (objective.length <= OBJECTIVE_PREVIEW_LENGTH) {
    return objective
  }
  let sliceEnd = OBJECTIVE_PREVIEW_LENGTH - 1
  const lead = objective.charCodeAt(sliceEnd - 1)
  const trail = objective.charCodeAt(sliceEnd)
  if (lead >= 0xd800 && lead <= 0xdbff && trail >= 0xdc00 && trail <= 0xdfff) {
    sliceEnd -= 1
  }
  return `${objective.slice(0, sliceEnd)}…`
}
