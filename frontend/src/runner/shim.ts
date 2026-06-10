// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Entry-point shim for the tree-UI runner (03 §2.1).
 *
 * Wraps `runWave` with the canonical 5-step ordering:
 *   1. Tag-hygiene gate    — operator non-empty, else emit operator_tag_required
 *   2. Cross-tab lock      — busy emits `busy` event with no release pending
 *   3. Cost guardrail      — operator confirm/cancel
 *   4. Per-tree wave queue — enqueue if another wave active on the tree
 *   5. Wave start          — runWave then reconcileAllTransforms then drain
 *
 * Steps 2-5 are wrapped in try/finally so the lock releases on every exit
 * path. Drain runs OUTSIDE the lock so re-entered drained waves can acquire
 * their own. Per-tree active-wave + queue maps live in the closure so
 * `cancelWave` / `cancelQueued` can look up controllers and dropped requests.
 */

import { buildSForNode, buildSForRetry, buildSForSubtree, buildSForTree, computeReady, demoteRetryFailedNodes } from './readiness'
import { resolvePathPartition } from './partition'
import { reconcileAllTransforms } from './reconcile'
import { createWaveController } from './wave'
import type { WaveDispatchController, WaveSummary } from './wave'
import type {
  ConversationTree,
  ConversationTreeId,
  ConversationTreeNodeId,
  CostGuardrail,
  CrossTabLockManager,
  RunnerStateSink,
  WaveTriggerKind,
} from './treeTypes'

// ============================================================================
// Dependency types
// ============================================================================

/** Returns the operator tag for the current session; '' or null aborts the wave. */
export type OperatorProvider = () => string | null

/** Returns the live ConversationTree for the id, or undefined if the tree is missing. */
export type TreeProvider = (treeId: ConversationTreeId) => ConversationTree | undefined

export interface RunWaveStarterArgs {
  treeId: ConversationTreeId
  tree: ConversationTree
  S: Set<ConversationTreeNodeId>
  waveId: string
  waveTriggerKind: WaveTriggerKind
  operator: string
  parentConversationTreeId: ConversationTreeId | null
  controller: WaveDispatchController
}

/**
 * The shim's only contact with the wave-dispatch layer. Injected so the shim
 * is testable without runWave's machinery; production wires this to a thin
 * adapter that calls `runWave` with the operation label + sink + api.
 */
export type RunWaveStarter = (args: RunWaveStarterArgs) => Promise<WaveSummary>

export interface ShimDependencies {
  operatorProvider: OperatorProvider
  treeProvider: TreeProvider
  sink: RunnerStateSink
  lockManager: CrossTabLockManager
  costGuardrail: CostGuardrail
  runWaveStarter: RunWaveStarter
  uuid: () => string
  /** Optional clock for emittedAt; defaults to `() => new Date()`. */
  now?: () => Date
}

// ============================================================================
// Public interface
// ============================================================================

export interface RunnerShim {
  refreshNode(treeId: ConversationTreeId, nodeId: ConversationTreeNodeId): Promise<void>
  refreshSubtree(treeId: ConversationTreeId, rootNodeId: ConversationTreeNodeId): Promise<void>
  refreshTree(treeId: ConversationTreeId): Promise<void>
  retryFailedNodes(treeId: ConversationTreeId, nodeIds: ConversationTreeNodeId[]): Promise<void>
  cancelWave(treeId: ConversationTreeId): Promise<void>
  cancelQueued(treeId: ConversationTreeId): Promise<void>
}

// ============================================================================
// Internal types
// ============================================================================

type ShimScope =
  | { kind: 'node'; nodeId: ConversationTreeNodeId }
  | { kind: 'subtree'; rootNodeId: ConversationTreeNodeId }
  | { kind: 'tree' }
  | { kind: 'retry'; nodeIds: ConversationTreeNodeId[] }

interface ActiveWave {
  waveId: string
  controller: WaveDispatchController
  settled: Promise<WaveSummary>
}

interface QueuedWave {
  waveId: string
  triggerKind: WaveTriggerKind
  scope: ShimScope
  /** Leaf count at enqueue-time; used by cancelQueued's synthetic complete event. */
  leafCount: number
}

// ============================================================================
// Factory
// ============================================================================

export function createRunnerShim(deps: ShimDependencies): RunnerShim {
  const currentWaveByTree = new Map<ConversationTreeId, ActiveWave>()
  const queueByTree = new Map<ConversationTreeId, QueuedWave[]>()
  const nowIso = () => (deps.now ? deps.now() : new Date()).toISOString()

  async function runShim(
    treeId: ConversationTreeId,
    scope: ShimScope,
    triggerKind: WaveTriggerKind,
  ): Promise<void> {
    // 1. Tag-hygiene gate. Runs BEFORE lock acquire so a tag-missing operator
    //    sees the modal without leaking a cross-tab lock on every retry.
    const operator = deps.operatorProvider()
    if (!operator) {
      deps.sink.emitWaveEvent({
        kind: 'operator_tag_required',
        treeId,
        emittedAt: nowIso(),
      })
      return
    }

    // 2. Cross-tab lock acquire. 'busy' returns BEFORE the try block so no
    //    release fires (we don't hold the lock).
    const lockResult = await deps.lockManager.acquire(treeId)
    if (!lockResult.acquired) {
      deps.sink.emitWaveEvent({
        kind: 'busy',
        treeId,
        holderTabId: lockResult.holderTabId,
        emittedAt: nowIso(),
      })
      return
    }

    try {
      const baseTree = deps.treeProvider(treeId)
      if (baseTree === undefined) return // silent no-op for missing tree

      // S construction + retry-failed demotion. Retry rewrites the tree to its
      // post-demotion shape so the dispatcher's computeReady sees demoted state.
      let S: Set<ConversationTreeNodeId>
      let tree = baseTree
      if (scope.kind === 'retry') {
        S = buildSForRetry(baseTree, scope.nodeIds)
        tree = demoteRetryFailedNodes(baseTree, S, deps.sink)
      } else if (scope.kind === 'node') {
        S = buildSForNode(baseTree, scope.nodeId)
      } else if (scope.kind === 'subtree') {
        S = buildSForSubtree(baseTree, scope.rootNodeId)
      } else {
        S = buildSForTree(baseTree)
      }

      // 3. Cost guardrail.
      const estimatedCalls = estimateCalls(tree, S)
      const approved = await deps.costGuardrail.approve(estimatedCalls, triggerKind)
      if (!approved) return

      // 4. Wave-queue check. waveId is minted ONCE per shim entry; if enqueued,
      //    the queued event carries it. The drained re-entry mints its own.
      const waveId = deps.uuid()
      if (currentWaveByTree.has(treeId)) {
        const req: QueuedWave = {
          waveId,
          triggerKind,
          scope,
          leafCount: computeReady(tree, S).length,
        }
        const q = queueByTree.get(treeId) ?? []
        q.push(req)
        queueByTree.set(treeId, q)
        deps.sink.emitWaveEvent({
          kind: 'queued',
          waveId,
          treeId,
          queueDepth: q.length,
          emittedAt: nowIso(),
        })
        return
      }

      // 5. Wave start. The controller is per-wave so cancelWave can find it.
      const controller = createWaveController()
      const settled = deps.runWaveStarter({
        treeId,
        tree,
        S,
        waveId,
        waveTriggerKind: triggerKind,
        operator,
        parentConversationTreeId: tree.parentConversationTreeId,
        controller,
      })
      currentWaveByTree.set(treeId, { waveId, controller, settled })
      try {
        await settled
        // Wave-end transform reconcile (step 6). Re-snapshot the tree so the
        // walk reads post-wave state (the dispatcher may have flipped Sends
        // to clean via the sink during dispatch).
        const postTree = deps.treeProvider(treeId)
        if (postTree !== undefined) {
          reconcileAllTransforms(postTree, treeId, deps.sink)
        }
      } finally {
        currentWaveByTree.delete(treeId)
      }
    } finally {
      deps.lockManager.release(treeId)
    }

    // Drain OUTSIDE the lock so each drained wave can acquire its own.
    // Reached only on the step-5 success path: every early-exit (tag-gate,
    // busy, missing tree, cost-cancel, enqueue) returns from inside the try
    // and bypasses this block; an exception from step 5 propagates through
    // the finally and exits the function before this block runs.
    const q = queueByTree.get(treeId) ?? []
    while (q.length > 0) {
      const next = q.shift() as QueuedWave
      await runShim(treeId, next.scope, next.triggerKind)
    }
  }

  return {
    refreshNode: (treeId, nodeIdToFire) =>
      runShim(treeId, { kind: 'node', nodeId: nodeIdToFire }, 'refresh_node'),
    refreshSubtree: (treeId, rootNodeId) =>
      runShim(treeId, { kind: 'subtree', rootNodeId }, 'refresh_subtree'),
    refreshTree: (treeId) => runShim(treeId, { kind: 'tree' }, 'refresh_tree'),
    retryFailedNodes: (treeId, nodeIds) =>
      runShim(treeId, { kind: 'retry', nodeIds: [...nodeIds] }, 'retry_failed'),
    cancelWave: async (treeId) => {
      const active = currentWaveByTree.get(treeId)
      if (active === undefined) return
      active.controller.cancel()
      // Wait for settle so the public contract — "returns when the wave fully
      // settles" — holds. Swallow rejection so cancelWave itself never throws.
      await active.settled.catch(() => undefined)
    },
    cancelQueued: async (treeId) => {
      const q = queueByTree.get(treeId)
      if (q === undefined || q.length === 0) return
      const dropped = q.splice(0)
      for (const w of dropped) {
        deps.sink.emitWaveEvent({
          kind: 'complete',
          waveId: w.waveId,
          emittedAt: nowIso(),
          summary: {
            succeeded: 0,
            failed: { transient: 0, rate_limited: 0, permanent: 0 },
            blocked: 0,
            cancelled: w.leafCount,
            reflog_evicted: 0,
          },
        })
      }
    },
  }
}

// ============================================================================
// Helpers
// ============================================================================

function estimateCalls(
  tree: ConversationTree,
  S: ReadonlySet<ConversationTreeNodeId>,
): number {
  let total = 0
  for (const leaf of computeReady(tree, S)) {
    total += 1 + resolvePathPartition(tree, leaf.id).freshSuffix.length
  }
  return total
}
