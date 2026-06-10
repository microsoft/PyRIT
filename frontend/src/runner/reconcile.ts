// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Wave-end transform reconcile (03 §3.1 step 6 / §3.3a `reconcileAllTransforms`).
 *
 * Per-dispatch reconciliation lives in the dispatcher (path-scoped; lands
 * with the V1.x intra-wave memo work). This module owns the wave-end pass:
 * a single O(tree-size) walk that flips every transform-class node
 * (UserTurn / Fan / Score) from `stale → clean` when every parent is `clean`.
 *
 * The pass is the only thing that catches sibling-of-Send ScoreNodes — the
 * operator-typical placement that path-scoped reconcile cannot reach.
 */

import type {
  ConversationTree,
  ConversationTreeId,
  ConversationTreeNode,
  ConversationTreeNodeId,
  RunnerStateSink,
} from './treeTypes'

const TRANSFORM_KINDS: ReadonlySet<ConversationTreeNode['kind']> = new Set<
  ConversationTreeNode['kind']
>(['user_turn', 'fan', 'score'])

/**
 * Walk every transform node in the tree once. For each transform whose state
 * is `stale` and whose every parent is `clean`, fire `sink.setNodeState(...,
 * 'clean')`. Single pass: a transform whose parent is itself a transform
 * being flipped this pass stays stale (a follow-up wave will catch it).
 *
 * Send-state transitions are owned by the dispatcher; the reconciler must
 * not flip Send-class nodes or it would race recordExecution.
 */
export function reconcileAllTransforms(
  tree: ConversationTree,
  treeId: ConversationTreeId,
  sink: RunnerStateSink,
): void {
  const byId = new Map<ConversationTreeNodeId, ConversationTreeNode>()
  for (const n of tree.nodes) byId.set(n.id, n)
  for (const node of tree.nodes) {
    if (!TRANSFORM_KINDS.has(node.kind)) continue
    if (node.state !== 'stale') continue
    if (!allParentsClean(node, byId)) continue
    sink.setNodeState(treeId, node.id, 'clean')
  }
}

function allParentsClean(
  node: ConversationTreeNode,
  byId: Map<ConversationTreeNodeId, ConversationTreeNode>,
): boolean {
  if (node.parentId === null) return true
  const parent = byId.get(node.parentId)
  if (parent === undefined) return true
  return parent.state === 'clean'
}
