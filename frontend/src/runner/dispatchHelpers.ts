// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Pure dispatch helpers: label construction and API-error classification.
 *
 * Kept in a separate module from the dispatcher itself so the runner's
 * orchestrator (PR4c2) is small and the helpers can be unit-tested without
 * any API-client mocking.
 */

import type { ApiError } from '../services/errors'
import type {
  ApiErrorReason,
  ConversationTreeId,
  WaveTriggerKind,
} from './treeTypes'

// ============================================================================
// buildLabels — the labels-divergence invariant SOURCE
// ============================================================================

export interface BuildLabelsArgs {
  /** The operator tag the wave runs under. Must be non-empty (tag-hygiene gate). */
  operator: string
  /** Operator-selected operation label; empty string permitted (matches existing chat). */
  operation: string
  treeId: ConversationTreeId
  waveId: string
  waveTriggerKind: WaveTriggerKind
  /** (axis, slotIndex) tuples for every Fan ancestor on the leaf's path. */
  treePathSegments: ReadonlyArray<readonly [string, number]>
  /** Source tree id for cloned trees; null for fresh / openTree trees. */
  parentConversationTreeId: ConversationTreeId | null
}

/**
 * Build the labels dict that will be attached to every `create_attack` and
 * `add_message` request in one leaf's dispatch sequence. The dispatcher
 * calls this once per dispatch and reuses the result — that's what enforces
 * the labels-divergence invariant (identical labels across all N+1 calls).
 *
 * Hard-asserts on missing operator. The runner's entry-point shim's
 * tag-hygiene gate is the load-bearing check; this assertion is the
 * defense-in-depth fail-loud for the case where a future refactor or a
 * test fixture bypasses the gate.
 */
export function buildLabels(args: BuildLabelsArgs): Record<string, string> {
  if (!args.operator) {
    throw new Error(
      'buildLabels: operator is required; the tag-hygiene gate must run before dispatch',
    )
  }
  const labels: Record<string, string> = {
    operator: args.operator,
    operation: args.operation,
    conversation_tree_id: String(args.treeId),
    wave_id: args.waveId,
    wave_trigger_kind: args.waveTriggerKind,
    tree_path: JSON.stringify(args.treePathSegments),
  }
  if (args.parentConversationTreeId !== null) {
    labels.parent_conversation_tree_id = String(args.parentConversationTreeId)
  }
  return labels
}

/**
 * Parse the `tree_path` label back into its (axis, slotIndex) tuple list.
 * Fail-soft: absent / empty / malformed input returns `[]` so older clients
 * encountering a future encoding don't hard-crash.
 */
export function parseTreePathLabel(label: string | undefined): Array<[string, number]> {
  if (label === undefined || label === '') return []
  let parsed: unknown
  try {
    parsed = JSON.parse(label)
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []
  const out: Array<[string, number]> = []
  for (const item of parsed) {
    if (!Array.isArray(item) || item.length !== 2) return []
    const [axis, slot] = item
    if (typeof axis !== 'string' || typeof slot !== 'number') return []
    out.push([axis, slot])
  }
  return out
}

/** True iff `label` parses to a well-formed `tree_path` array. */
export function isTreePathLabelValid(label: string | undefined): boolean {
  if (label === undefined || label === '') return false
  try {
    const parsed: unknown = JSON.parse(label)
    if (!Array.isArray(parsed)) return false
    for (const item of parsed) {
      if (!Array.isArray(item) || item.length !== 2) return false
      const [axis, slot] = item
      if (typeof axis !== 'string' || typeof slot !== 'number') return false
    }
    return true
  } catch {
    return false
  }
}

// ============================================================================
// formatApiError — failure-class classification
// ============================================================================

export type DispatchCallName = 'create_attack' | 'add_message'

/**
 * Classify an `ApiError` into a {@link NodeFailureClass} for retry UX.
 *
 * - `transient`: 5xx, network errors, timeouts. The wave-complete toast's
 *   [Retry failed] retries these automatically.
 * - `rate_limited`: HTTP 429 + provider-specific shapes (Anthropic 529
 *   overloaded_error, OpenAI rate_limit_exceeded). The [Retry failed]
 *   button excludes these from the retry set; operator manually re-triggers
 *   after waiting.
 * - `permanent`: 4xx other than 429 (validation, operator-lock mismatch,
 *   target-not-found). Requires operator action; not retry-eligible.
 *
 * Returns `'transient'` as the safe default for unclassifiable shapes:
 * a wrongly-classified transient triggers an unhelpful but harmless retry,
 * whereas a wrongly-classified permanent silently locks the operator out
 * of recovery.
 */
export function formatApiError(error: ApiError, callName: DispatchCallName): ApiErrorReason {
  const provider = detectProviderRateLimit(error)
  if (provider) {
    return {
      message: rateLimitedMessage(error, callName),
      failure_class: 'rate_limited',
    }
  }

  if (error.isNetworkError) {
    return {
      message: `${callName} failed (network): ${error.detail} — likely transient, retry`,
      failure_class: 'transient',
    }
  }
  if (error.isTimeout) {
    return {
      message: `${callName} timed out: ${error.detail} — likely transient, retry`,
      failure_class: 'transient',
    }
  }

  const status = error.status
  if (status === null) {
    return {
      message: `${callName} failed: ${error.detail}`,
      failure_class: 'transient',
    }
  }

  if (status === 429) {
    return {
      message: rateLimitedMessage(error, callName),
      failure_class: 'rate_limited',
    }
  }

  if (status >= 500 && status < 600) {
    return {
      message: `${callName} failed (${status}): ${error.detail} — transient, retry`,
      failure_class: 'transient',
    }
  }

  if (status === 400 && isOperatorMismatch(error.detail)) {
    return {
      message: `${callName} blocked by operator lock — branch from this node to take ownership`,
      failure_class: 'permanent',
    }
  }

  if (status >= 400 && status < 500) {
    return {
      message: `${callName} failed (${status}): ${error.detail}`,
      failure_class: 'permanent',
    }
  }

  // Anything else (status > 599 or weird).
  return {
    message: `${callName} failed (${status}): ${error.detail}`,
    failure_class: 'transient',
  }
}

/**
 * Provider-specific rate-limit detection registry. Returns true for shapes
 * we know to be rate-limit signals despite a non-429 status code (Anthropic
 * uses 529; some providers return 200 / 400 with `rate_limit_exceeded` or
 * `overloaded_error` in the body).
 *
 * Promoting this to a backend-driven mapping is the right V1.x evolution
 * once token-bucket throttling lands and the backend knows each target's
 * provider; until then, client-side detection keeps the runner self-contained.
 */
function detectProviderRateLimit(error: ApiError): boolean {
  if (error.status === 529) return true
  const detail = (error.detail || '').toLowerCase()
  if (detail.includes('rate_limit_exceeded')) return true
  if (detail.includes('overloaded_error')) return true
  return false
}

function rateLimitedMessage(error: ApiError, callName: DispatchCallName): string {
  const statusFragment = error.status === null ? '' : ` (${error.status})`
  return `${callName} rate-limited${statusFragment}: ${error.detail} — wait for the target's rate-limit window, then retry`
}

function isOperatorMismatch(detail: string): boolean {
  const lower = (detail || '').toLowerCase()
  return lower.includes('operator')
}
