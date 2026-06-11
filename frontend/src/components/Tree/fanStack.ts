// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Fan-Children Stack render helpers (pure tree-walker).
 *
 * The Fan-Children Stack collapses N visually-identical fan-children
 * into a single summary card inside the FanCard body. V1.0 limits the
 * stack to the `attempt` axis (the only axis whose children diverge
 * only by slotIndex; other axes encode their differences in the variant
 * payload, so children aren't visually identical).
 *
 * Auto-collapse threshold: N > 3. Below that, stackable fans render
 * expanded by default; the operator can manually collapse via the
 * fan-card's ⊞ / ⊟ toggle.
 */

import type {
  ConversationTree,
  ConversationTreeNode,
  ConversationTreeNodeId,
  ConversationTreeNodeKind,
  NodeState,
} from '../../runner/treeTypes'

// ============================================================================
// Public types
// ============================================================================

export interface StackAggregate {
  /** Kind shared by all stacked children (V1.0: always 'send' for attempt-axis). */
  childKind: ConversationTreeNodeKind | null
  total: number
  byState: Record<NodeState, number>
}

const AUTO_COLLAPSE_THRESHOLD = 3

// ============================================================================
// Public API
// ============================================================================

/**
 * True iff the node is a FanNode whose children render as a Fan-Children
 * Stack — V1.0: attempt-axis, N >= 2, all children's subtrees structurally
 * identical (recursive shape + kinds; params and state may differ).
 */
export function isStackable(
  tree: ConversationTree,
  fanNodeId: ConversationTreeNodeId,
): boolean {
  const fan = tree.nodes.find((n) => n.id === fanNodeId)
  if (fan === undefined) return false
  if (fan.kind !== 'fan') return false
  if (fan.params.axis !== 'attempt') return false
  const idx = indexTree(tree)
  const children = idx.childrenOf.get(fanNodeId) ?? []
  if (children.length < 2) return false
  // All children must have structurally identical subtrees. Compare each
  // pair (linear via first-child reference is sufficient: equality is
  // transitive).
  const first = children[0]
  for (let i = 1; i < children.length; i++) {
    if (!subtreesEqual(first, children[i], idx)) return false
  }
  return true
}

/**
 * Default-collapsed set: every stackable fan with N > 3. Below threshold
 * the operator opts in via the fan-card toggle. TreeCanvas seeds its
 * collapse state with this set when a tree first mounts.
 */
export function defaultCollapsedFanIds(
  tree: ConversationTree,
): Set<ConversationTreeNodeId> {
  const idx = indexTree(tree)
  const out = new Set<ConversationTreeNodeId>()
  for (const n of tree.nodes) {
    if (n.kind !== 'fan') continue
    if (n.params.axis !== 'attempt') continue
    const children = idx.childrenOf.get(n.id) ?? []
    if (children.length <= AUTO_COLLAPSE_THRESHOLD) continue
    if (!isStackable(tree, n.id)) continue
    out.add(n.id)
  }
  return out
}

/**
 * Aggregate child-state counts for a fan's Fan-Children Stack. Used by
 * the collapsed FanCard body to render the *"Send ×10 (9 ✓, 1 ⚠)"* line.
 *
 * Returns total=0 + childKind=null for non-fan / empty-fan inputs so
 * callers can render a defensive empty state.
 */
export function computeStackAggregate(
  tree: ConversationTree,
  fanNodeId: ConversationTreeNodeId,
): StackAggregate {
  const empty: StackAggregate = {
    childKind: null,
    total: 0,
    byState: {
      draft: 0,
      clean: 0,
      edited: 0,
      stale: 0,
      running: 0,
      failed: 0,
      cancelled: 0,
    },
  }
  const fan = tree.nodes.find((n) => n.id === fanNodeId)
  if (fan === undefined || fan.kind !== 'fan') return empty
  const idx = indexTree(tree)
  const children = idx.childrenOf.get(fanNodeId) ?? []
  if (children.length === 0) return empty
  const byState: Record<NodeState, number> = { ...empty.byState }
  for (const c of children) byState[c.state]++
  return {
    childKind: children[0].kind,
    total: children.length,
    byState,
  }
}

// ============================================================================
// Private helpers
// ============================================================================

interface TreeIndex {
  byId: Map<ConversationTreeNodeId, ConversationTreeNode>
  childrenOf: Map<ConversationTreeNodeId, ConversationTreeNode[]>
}

function indexTree(tree: ConversationTree): TreeIndex {
  const byId = new Map<ConversationTreeNodeId, ConversationTreeNode>()
  const childrenOf = new Map<ConversationTreeNodeId, ConversationTreeNode[]>()
  for (const n of tree.nodes) byId.set(n.id, n)
  for (const n of tree.nodes) {
    if (n.parentId === null) continue
    const siblings = childrenOf.get(n.parentId)
    if (siblings === undefined) childrenOf.set(n.parentId, [n])
    else siblings.push(n)
  }
  return { byId, childrenOf }
}

/**
 * Structural equality: same kind + same child-count + recursive structural
 * equality of children (in tree-iteration order). Params and lifecycle
 * state are NOT compared — operator can dirty one child via Refresh and
 * the stack should still collapse, per the §3.1 "execution may differ"
 * note.
 */
function subtreesEqual(
  a: ConversationTreeNode,
  b: ConversationTreeNode,
  idx: TreeIndex,
): boolean {
  if (a.kind !== b.kind) return false
  const aChildren = idx.childrenOf.get(a.id) ?? []
  const bChildren = idx.childrenOf.get(b.id) ?? []
  if (aChildren.length !== bChildren.length) return false
  for (let i = 0; i < aChildren.length; i++) {
    if (!subtreesEqual(aChildren[i], bChildren[i], idx)) return false
  }
  return true
}
