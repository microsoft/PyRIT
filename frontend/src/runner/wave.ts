// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Wave dispatch loop. Orchestrates per-leaf `dispatchLeaf` calls across a
 * tree's in-need-of-dispatch set with:
 *   - a hard concurrency cap (`maxParallel`, default 4)
 *   - in-flight cascade: when a leaf's dispatch fails on a Send shared
 *     by other siblings still in the ready queue, the siblings transition
 *     to a `blocked` failure-class rather than independently retrying the
 *     shared failure
 *   - operator-cancel via a `WaveDispatchController` flag checked at each
 *     ready-pop boundary; in-flight HTTP completes (the V1.0 UI-level
 *     cancel contract), not-yet-dispatched leaves transition to `cancelled`
 *   - wave-event emission (`start`, `node_complete` per leaf, `complete`)
 */

import { dispatchLeaf } from './dispatch'
import type { LeafDispatchOutcome, RunnerAttacksApi } from './dispatch'
import { resolvePathPartition, rootToLeafPath } from './partition'
import { computeReady } from './readiness'
import type {
  ConversationTree,
  ConversationTreeId,
  ConversationTreeNodeId,
  NodeFailureClass,
  RunnerStateSink,
  WaveTriggerKind,
} from './treeTypes'

// ============================================================================
// Public types
// ============================================================================

/**
 * Per-wave handle used to signal operator cancellation. Created externally
 * by the entry-point shim (PR4e) so the same controller can be looked up
 * by `runner.cancelWave(treeId)`; tests can also instantiate one directly.
 */
export interface WaveDispatchController {
  cancel(): void
  isCancelled(): boolean
}

export function createWaveController(): WaveDispatchController {
  let cancelled = false
  return {
    cancel: () => {
      cancelled = true
    },
    isCancelled: () => cancelled,
  }
}

/**
 * Shape of the wave's terminal-tally summary. Mirrors the `complete` variant
 * of `WaveEvent.summary` — exposed as a return value so callers (the entry-
 * point shim, tests) can read the tally without subscribing to the event
 * stream.
 */
export interface WaveSummary {
  succeeded: number
  failed: { transient: number; rate_limited: number; permanent: number }
  blocked: number
  cancelled: number
  reflog_evicted: number
}

export interface RunWaveArgs {
  treeId: ConversationTreeId
  tree: ConversationTree
  /** The in-need-of-dispatch set produced by `buildSFor*`. */
  S: Set<ConversationTreeNodeId>
  sink: RunnerStateSink
  api: RunnerAttacksApi
  operator: string
  operation: string
  waveId: string
  waveTriggerKind: WaveTriggerKind
  parentConversationTreeId: ConversationTreeId | null
  maxParallel?: number
  controller?: WaveDispatchController
}

const DEFAULT_MAX_PARALLEL = 4

// ============================================================================
// Entry point
// ============================================================================

export async function runWave(args: RunWaveArgs): Promise<WaveSummary> {
  const ctrl = args.controller ?? createWaveController()
  const maxParallel = args.maxParallel ?? DEFAULT_MAX_PARALLEL

  // Initial ready set: leaves in S whose Send ancestors are all admissible.
  // V1.0 does not re-compute ready post-dispatch — every leaf's dispatchability
  // is determined at wave start (siblings don't unblock each other; the only
  // mid-wave change is cascade-driven removals).
  const initialReady = computeReady(args.tree, args.S)
  const ready: ConversationTreeNodeId[] = initialReady.map((l) => l.id)
  const remaining = new Set<ConversationTreeNodeId>(ready)

  // Estimate calls upfront: 1 create_attack + N add_messages per leaf, where
  // N is the leaf's fresh-suffix length. The cost-guardrail layer (PR4e) will
  // consume this; here it's surfaced on the `start` event.
  let estimatedCalls = 0
  for (const leaf of initialReady) {
    const partition = resolvePathPartition(args.tree, leaf.id)
    estimatedCalls += 1 + partition.freshSuffix.length
  }

  args.sink.emitWaveEvent({
    kind: 'start',
    waveId: args.waveId,
    triggerKind: args.waveTriggerKind,
    estimatedCalls,
    treeId: args.treeId,
    emittedAt: nowIso(),
  })

  // Per-leaf outcome tracking. The terminal bucket is what the summary tallies.
  type OutcomeBucket = 'succeeded' | NodeFailureClass | 'cancelled'
  const outcomes = new Map<ConversationTreeNodeId, OutcomeBucket>()

  // Wrap each dispatch so Promise.race yields the leaf id alongside the outcome.
  const inflight = new Map<
    ConversationTreeNodeId,
    Promise<{ leafId: ConversationTreeNodeId; outcome: LeafDispatchOutcome }>
  >()

  const dispatch = (leafId: ConversationTreeNodeId) =>
    dispatchLeaf({
      treeId: args.treeId,
      tree: args.tree,
      leafId,
      sink: args.sink,
      api: args.api,
      operator: args.operator,
      operation: args.operation,
      waveId: args.waveId,
      waveTriggerKind: args.waveTriggerKind,
      parentConversationTreeId: args.parentConversationTreeId,
    }).then((outcome) => ({ leafId, outcome }))

  while (ready.length > 0 || inflight.size > 0) {
    // Drain ready into inflight up to the cap. The cancellation check here
    // is the gate: when cancelled, no further leaves are picked, but
    // already-in-flight ones run to completion (V1.0 contract).
    while (inflight.size < maxParallel && ready.length > 0 && !ctrl.isCancelled()) {
      const leafId = ready.shift() as ConversationTreeNodeId
      remaining.delete(leafId)
      inflight.set(leafId, dispatch(leafId))
    }

    // If nothing is in flight, the loop has no progress to wait for. This
    // happens either when S is exhausted normally OR when cancellation
    // skipped the inner pick loop and there were no in-flight dispatches
    // to await.
    if (inflight.size === 0) break

    const { leafId, outcome } = await Promise.race(inflight.values())
    inflight.delete(leafId)

    if (outcome.kind === 'success') {
      outcomes.set(leafId, 'succeeded')
    } else {
      outcomes.set(leafId, outcome.failureClass)
      // Cascade: drop any remaining (not-yet-dispatched) leaf whose path
      // includes the failed Send. In-flight leaves are NOT clobbered — they
      // complete and report their own outcomes (which may also be failure
      // on the same shared ancestor, counted as independent failures rather
      // than cascade-blocked).
      cascadeBlocked({
        tree: args.tree,
        treeId: args.treeId,
        failedSendId: outcome.failedNodeId,
        waveId: args.waveId,
        remaining,
        ready,
        sink: args.sink,
        outcomes,
      })
    }

    args.sink.emitWaveEvent({
      kind: 'node_complete',
      waveId: args.waveId,
      nodeId: leafId,
      outcome: outcome.kind === 'success' ? 'success' : 'failure',
      emittedAt: nowIso(),
    })
  }

  // Cancel-tally: any leaf still in `remaining` after the loop exited via
  // cancellation gets marked cancelled. (Leaves that completed mid-cancel
  // were already tallied with their natural outcome — the execution-clobber
  // gate.)
  if (ctrl.isCancelled()) {
    for (const leafId of remaining) {
      args.sink.setNodeState(args.treeId, leafId, 'cancelled', {
        reason: { message: 'wave cancelled by operator', failure_class: 'transient' },
      })
      args.sink.clearExecution(args.treeId, leafId)
      outcomes.set(leafId, 'cancelled')
    }
    remaining.clear()
    ready.length = 0
  }

  const summary = buildSummary(outcomes)

  args.sink.emitWaveEvent({
    kind: 'complete',
    waveId: args.waveId,
    emittedAt: nowIso(),
    summary,
  })

  return summary
}

// ============================================================================
// Private helpers
// ============================================================================

function cascadeBlocked(args: {
  tree: ConversationTree
  treeId: ConversationTreeId
  failedSendId: ConversationTreeNodeId
  waveId: string
  remaining: Set<ConversationTreeNodeId>
  ready: ConversationTreeNodeId[]
  sink: RunnerStateSink
  outcomes: Map<ConversationTreeNodeId, 'succeeded' | NodeFailureClass | 'cancelled'>
}): void {
  const blocked: ConversationTreeNodeId[] = []
  for (const leafId of args.remaining) {
    const path = rootToLeafPath(args.tree, leafId)
    if (path.some((n) => n.id === args.failedSendId)) {
      blocked.push(leafId)
    }
  }
  if (blocked.length === 0) return
  const blockedSet = new Set(blocked)
  for (const id of blocked) {
    args.remaining.delete(id)
    args.sink.setNodeState(args.treeId, id, 'stale', {
      reason: {
        message: `blocked by ancestor failure in wave ${args.waveId}`,
        failure_class: 'blocked',
      },
    })
    args.outcomes.set(id, 'blocked')
  }
  // Strip from the ready queue too. Iterate in reverse so splice indices stay valid.
  for (let i = args.ready.length - 1; i >= 0; i--) {
    if (blockedSet.has(args.ready[i])) args.ready.splice(i, 1)
  }
}

function buildSummary(
  outcomes: Map<ConversationTreeNodeId, 'succeeded' | NodeFailureClass | 'cancelled'>,
): WaveSummary {
  const summary: WaveSummary = {
    succeeded: 0,
    failed: { transient: 0, rate_limited: 0, permanent: 0 },
    blocked: 0,
    cancelled: 0,
    reflog_evicted: 0,
  }
  for (const bucket of outcomes.values()) {
    switch (bucket) {
      case 'succeeded':
        summary.succeeded++
        break
      case 'cancelled':
        summary.cancelled++
        break
      case 'blocked':
        summary.blocked++
        break
      case 'transient':
      case 'rate_limited':
      case 'permanent':
        summary.failed[bucket]++
        break
    }
  }
  return summary
}

function nowIso(): string {
  return new Date().toISOString()
}
