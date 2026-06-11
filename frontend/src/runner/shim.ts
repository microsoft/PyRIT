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
  ConversationTreeNode,
  ConversationTreeNodeId,
  CostGuardrail,
  CrossTabLockManager,
  NodeState,
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
  /**
   * The sink the dispatcher writes through during this wave. The shim wraps
   * its `deps.sink` in a recording proxy that captures every `setNodeState`
   * call into a per-wave map, then constructs the post-wave tree from the
   * captures so the wave-end `reconcileAllTransforms` walk reads the same
   * world the dispatcher just produced. The production runWave adapter
   * forwards this sink directly to `runWave`; do NOT swap it for the
   * shim's `deps.sink` or the reconcile read-back will race the React
   * state container's commit timing (rubber-duck Finding D, PR4e+f.1).
   */
  sink: RunnerStateSink
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
      // Wrap the sink in a per-wave recorder. The recorder captures every
      // setNodeState the dispatcher makes during this wave; after settle,
      // we reconstruct the post-wave tree by overlaying the captured states
      // onto the input tree. This is the production-correct way to feed
      // reconcileAllTransforms — the alternative (a fresh treeProvider
      // lookup) races React's setState commit timing, since `await settled`
      // resumes on a microtask but React's state container may not have
      // committed the wave's setState calls by that boundary. Rubber-duck
      // Finding D, PR4e+f.1.
      const recorder = createStateRecorder(deps.sink)
      const settled = deps.runWaveStarter({
        treeId,
        tree,
        S,
        waveId,
        waveTriggerKind: triggerKind,
        operator,
        parentConversationTreeId: tree.parentConversationTreeId,
        controller,
        sink: recorder.sink,
      })
      currentWaveByTree.set(treeId, { waveId, controller, settled })
      try {
        await settled
        // Wave-end transform reconcile (step 6). The post-wave tree is built
        // from the input tree + the recorder's captured state mutations —
        // every Send the dispatcher transitioned to clean during the wave
        // shows up here, which is exactly what reconcileAllTransforms needs
        // to flip sibling transforms whose ancestors are now clean.
        const postTree = applyStateRecorder(tree, recorder.snapshot())
        reconcileAllTransforms(postTree, treeId, deps.sink)
      } finally {
        currentWaveByTree.delete(treeId)
      }
    } finally {
      deps.lockManager.release(treeId)
    }

    // Drain OUTSIDE the lock so each drained wave can acquire its own.
    // Drain-outside (vs the §2.1 spec's literal drain-inside-the-finally) is
    // a deliberate divergence for cross-tab fairness: when this shim's queue
    // is N deep, draining inside the outer lock makes the other tab wait
    // for N waves' worth of compute time before its own acquire can succeed.
    // Releasing between drained waves lets the other tab interleave.
    //
    // Reachability: this block runs only on the step-5 success path. Every
    // early-exit branch (tag-gate, busy, missing tree, cost-cancel, enqueue)
    // uses `return` inside the outer try — the return propagates through
    // both finallys and exits the function before this block. A step-5
    // exception likewise propagates through both finallys and exits. The
    // structural invariant: every early-exit MUST use `return`, not fall
    // through. A future refactor that replaces a guarded return with an
    // `if (!cond) { else-branch }` would silently start draining on the
    // early-exit path; the test "drained re-entry recomputes S from the
    // LATEST tree state" would catch the most obvious failure modes but
    // not all of them. If you touch this file, preserve the return-on-
    // early-exit invariant.
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

// ============================================================================
// State recorder — captures setNodeState writes so the wave-end reconcile
// can read a post-wave tree snapshot that doesn't depend on React state
// batch-commit timing (rubber-duck Finding D, PR4e+f.1).
// ============================================================================

interface StateRecorder {
  /** A RunnerStateSink that records setNodeState then forwards everything to the underlying sink. */
  sink: RunnerStateSink
  /** Returns the per-wave Map<NodeId, NodeState> captured by sink writes. */
  snapshot(): ReadonlyMap<ConversationTreeNodeId, NodeState>
}

function createStateRecorder(underlying: RunnerStateSink): StateRecorder {
  const states = new Map<ConversationTreeNodeId, NodeState>()
  const sink: RunnerStateSink = {
    setNodeState: (treeId, nodeId, state, opts) => {
      states.set(nodeId, state)
      underlying.setNodeState(treeId, nodeId, state, opts)
    },
    recordExecution: (treeId, nodeId, record) => {
      underlying.recordExecution(treeId, nodeId, record)
    },
    clearExecution: (treeId, nodeId) => {
      underlying.clearExecution(treeId, nodeId)
    },
    setReflogPinned: (treeId, nodeId, executionId, pinned) => {
      underlying.setReflogPinned(treeId, nodeId, executionId, pinned)
    },
    emitWaveEvent: (event) => {
      underlying.emitWaveEvent(event)
    },
  }
  return {
    sink,
    snapshot: () => states,
  }
}

/**
 * Overlay the recorder's per-node state captures onto the input tree's
 * nodes. Returns the same tree reference when no states were captured (no-op
 * waves don't perturb downstream caller-side memoization).
 */
function applyStateRecorder(
  tree: ConversationTree,
  states: ReadonlyMap<ConversationTreeNodeId, NodeState>,
): ConversationTree {
  if (states.size === 0) return tree
  return {
    ...tree,
    nodes: tree.nodes.map((n) => {
      const next = states.get(n.id)
      return next === undefined ? n : ({ ...n, state: next } as ConversationTreeNode)
    }),
  }
}
