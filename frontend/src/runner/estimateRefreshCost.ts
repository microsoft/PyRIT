// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Pre-dispatch cost estimator. Returns the number of target calls the
 * runner would fire if `runWave(tree, S)` ran right now, plus the leaf
 * count for operator-friendly tooltip framing.
 *
 * Per spec §8.1 (the modal's count input) and §2.2 Finding D.3 (the
 * ↻ button's hover-tooltip cost preview): both surfaces consume this
 * same function so the modal's "60 calls" never disagrees with the
 * tooltip's "≈60 calls" that the operator just hovered.
 *
 * Semantics match the shim's local `estimateCalls` (no clean-prefix
 * optimization in V1.0 — every Send on each leaf's root-to-leaf path
 * enters `freshSuffix`; see 01 §1.2). Returning `leaves` separately
 * lets the tooltip read "5 leaves" without re-running the readiness
 * walk.
 */

import { computeReady } from './readiness'
import { resolvePathPartition } from './partition'
import type {
  ConversationTree,
  ConversationTreeNodeId,
} from './treeTypes'

export interface CostEstimate {
  /** Total target-call count: `Σ leaves (1 + freshSuffix.length)`. */
  calls: number
  /** Leaf-Send count the wave would dispatch from. */
  leaves: number
}

export function estimateRefreshCost(
  tree: ConversationTree,
  S: ReadonlySet<ConversationTreeNodeId>,
): CostEstimate {
  let calls = 0
  let leaves = 0
  for (const leaf of computeReady(tree, S)) {
    calls += 1 + resolvePathPartition(tree, leaf.id).freshSuffix.length
    leaves += 1
  }
  return { calls, leaves }
}
