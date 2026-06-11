// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * applyStackCollapse — render-time decoration pass.
 *
 * Input:  shape result from `conversationTreeToReactFlow(tree)` plus the
 *         tree it came from plus the set of fan ids the operator has
 *         collapsed into a Fan-Children Stack.
 * Output: a NEW result where descendants of collapsed fans are filtered
 *         out, edges into/out of hidden nodes are dropped, and each
 *         collapsed fan node carries a freshly computed `stackedSummary`.
 *
 * Split out of the adapter in PR5h.1 so the adapter stays a pure
 * shape→shape mapping. Layout memoizes on the adapter output (stable
 * across state flips); this pass re-runs on collapse toggles and on
 * the per-tree state changes the summary aggregates (state, executions).
 */

import { computeStackAggregate } from './fanStack'
import type {
  TreeFlowAdapterResult,
  TreeFlowEdge,
  TreeFlowNode,
} from './conversationTreeToReactFlow'
import type {
  ConversationTree,
  ConversationTreeNodeId,
} from '../../runner/treeTypes'

export function applyStackCollapse(
  shape: TreeFlowAdapterResult,
  tree: ConversationTree,
  collapsedFanIds: ReadonlySet<ConversationTreeNodeId>,
): TreeFlowAdapterResult {
  if (collapsedFanIds.size === 0) {
    return shape
  }

  const hidden = collectHiddenDescendants(tree, collapsedFanIds)
  // react-flow's Edge.source/target are plain strings; the hidden set
  // entries are branded ConversationTreeNodeId. Brand membership is
  // structural at runtime — read through a string view so TS doesn't
  // require a per-call cast.
  const hiddenStrings = hidden as unknown as ReadonlySet<string>
  const nodes: TreeFlowNode[] = []
  for (const n of shape.nodes) {
    if (hiddenStrings.has(n.id)) continue
    if (n.type === 'fan' && collapsedFanIds.has(n.id as ConversationTreeNodeId)) {
      // Clone the fan-node's data so we don't mutate the shape input.
      nodes.push({
        ...n,
        data: {
          ...n.data,
          stackedSummary: computeStackAggregate(tree, n.id as ConversationTreeNodeId),
        },
      })
    } else {
      nodes.push(n)
    }
  }
  const edges: TreeFlowEdge[] = shape.edges.filter(
    (e) => !hiddenStrings.has(e.source) && !hiddenStrings.has(e.target),
  )

  return { treeId: shape.treeId, nodes, edges }
}

// Walk every collapsed fan's subtree and collect descendant ids. The
// fan itself stays visible; only its subtree below disappears. Returns
// an empty set on empty input.
function collectHiddenDescendants(
  tree: ConversationTree,
  collapsedFanIds: ReadonlySet<ConversationTreeNodeId>,
): ReadonlySet<ConversationTreeNodeId> {
  const childrenOf = new Map<ConversationTreeNodeId, ConversationTreeNodeId[]>()
  for (const n of tree.nodes) {
    if (n.parentId === null) continue
    const siblings = childrenOf.get(n.parentId)
    if (siblings === undefined) childrenOf.set(n.parentId, [n.id])
    else siblings.push(n.id)
  }
  const hidden = new Set<ConversationTreeNodeId>()
  const queue: ConversationTreeNodeId[] = []
  for (const fanId of collapsedFanIds) {
    const seed = childrenOf.get(fanId)
    if (seed !== undefined) queue.push(...seed)
  }
  while (queue.length > 0) {
    const id = queue.shift() as ConversationTreeNodeId
    if (hidden.has(id)) continue
    hidden.add(id)
    const grand = childrenOf.get(id)
    if (grand !== undefined) queue.push(...grand)
  }
  return hidden
}
