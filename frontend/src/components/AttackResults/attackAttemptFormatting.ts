import type { ScenarioProgressResult } from '@/types'

const OBJECTIVE_PREVIEW_LENGTH = 96

/**
 * Formatting for a single attack attempt.
 *
 * These live beside the attempt components rather than in a page so that every
 * surface showing an attempt renders it identically.
 */

export function formatAttackSuccess(outcome: ScenarioProgressResult['outcome']): string {
  if (outcome === 'success') {
    return 'Yes'
  }
  if (outcome === 'failure') {
    return 'No'
  }
  return outcome.replace(/^\w/, (letter) => letter.toUpperCase())
}

export function formatScore(score: ScenarioProgressResult['score']): string {
  if (!score || score.status === 'undetermined') {
    return 'Undetermined'
  }
  return score.score_value ?? 'Unavailable'
}

export function formatScoreRationale(rationale: string | null | undefined): string {
  return rationale?.trim() || 'No score rationale was persisted.'
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

export function objectivePreview(objective: string | null, fallbackId: string): string {
  if (!objective) {
    return `Objective unavailable (${fallbackId})`
  }
  if (objective.length <= OBJECTIVE_PREVIEW_LENGTH) {
    return objective
  }
  return `${objective.slice(0, OBJECTIVE_PREVIEW_LENGTH - 1)}…`
}
