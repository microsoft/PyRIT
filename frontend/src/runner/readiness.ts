// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Readiness + topological-walk primitives for the tree-UI runner.
 *
 * Pure functions over a {@link ConversationTree}; the only sink interaction is
 * {@link demoteRetryFailedNodes}, which is separated out so the rest of the
 * module composes freely without side effects.
 *
 * Backed by the design in
 * - doc/gui/design/03_runner.md §3.1 (the topological walk + readiness rule)
 * - doc/gui/design/03_runner.md §3.2 (interior Sends never in `ready`)
 * - doc/gui/design/03_runner.md §5.3 (cascade-on-failure / retry-failed)
 */

import type {
  ConversationTree,
  ConversationTreeNode,
  ConversationTreeNodeId,
  NodeState,
  RunnerStateSink,
  SendNode,
} from './treeTypes'

/** States the §3.1 readiness rule accepts as "S-eligible". */
const DISPATCHABLE_STATES: ReadonlySet<NodeState> = new Set<NodeState>([
  'edited',
  'stale',
  'failed',
  'cancelled',
])

/**
 * States a SEND ancestor may carry and still allow its leaf descendant to enter
 * `ready` (per §3.1 readiness rule, rev-15 Finding 4). `failed` and `cancelled`
 * are deliberately excluded: a leaf whose Send ancestor failed in the same wave
 * must NOT dispatch (the §5.3 in-flight cascade marks it `blocked` instead).
 */
const ANCESTOR_ALLOWED_STATES: ReadonlySet<NodeState> = new Set<NodeState>([
  'edited',
  'stale',
  'running',
  'clean',
])

// ============================================================================
// Index helpers (build once per call; trees are small enough that this is fine)
// ============================================================================

interface TreeIndex {
  /** O(1) lookup by node id. */
  byId: Map<ConversationTreeNodeId, ConversationTreeNode>
  /** O(1) lookup of a node's direct children. */
  childrenOf: Map<ConversationTreeNodeId, ConversationTreeNode[]>
}

function indexTree(tree: ConversationTree): TreeIndex {
  const byId = new Map<ConversationTreeNodeId, ConversationTreeNode>()
  const childrenOf = new Map<ConversationTreeNodeId, ConversationTreeNode[]>()
  for (const n of tree.nodes) {
    byId.set(n.id, n)
  }
  for (const n of tree.nodes) {
    if (n.parentId === null) continue
    const siblings = childrenOf.get(n.parentId)
    if (siblings === undefined) {
      childrenOf.set(n.parentId, [n])
    } else {
      siblings.push(n)
    }
  }
  return { byId, childrenOf }
}

// ============================================================================
// Leaf detection
// ============================================================================

/**
 * True iff `node` is a SendNode with no SendNode descendant. UserTurn / Fan /
 * Score descendants do not make a Send interior (per the §2 vocabulary
 * definition: "Leaf Send — a SendNode with no SendNode descendant").
 *
 * An orphan Send (Send with no children at all) is also a leaf per 03 §3.2.
 */
export function isLeafSend(tree: ConversationTree, nodeId: ConversationTreeNodeId): boolean {
  const idx = indexTree(tree)
  const node = idx.byId.get(nodeId)
  if (node === undefined || node.kind !== 'send') return false
  return !hasSendDescendant(node.id, idx)
}

/**
 * All SendNodes in the tree that have no SendNode descendant. Operator-typical
 * shape: each Fan child Send is a leaf; the deepest Send of a Crescendo-style
 * chain is a leaf; Sends with only Score / UserTurn descendants are leaves.
 *
 * Returns nodes in tree-iteration order (callers needing a specific order
 * should sort by `nodeId` or similar).
 */
export function findLeafSends(tree: ConversationTree): SendNode[] {
  const idx = indexTree(tree)
  const out: SendNode[] = []
  for (const n of tree.nodes) {
    if (n.kind !== 'send') continue
    if (!hasSendDescendant(n.id, idx)) {
      out.push(n)
    }
  }
  return out
}

function hasSendDescendant(id: ConversationTreeNodeId, idx: TreeIndex): boolean {
  // BFS over direct children. Cheap on V1.0 trees (soft cap 1000 nodes per 01 §9.4.6).
  const queue: ConversationTreeNodeId[] = []
  const seed = idx.childrenOf.get(id)
  if (seed === undefined) return false
  for (const c of seed) queue.push(c.id)
  while (queue.length > 0) {
    const next = queue.shift()!
    const node = idx.byId.get(next)
    if (node === undefined) continue
    if (node.kind === 'send') return true
    const grandchildren = idx.childrenOf.get(next)
    if (grandchildren !== undefined) {
      for (const c of grandchildren) queue.push(c.id)
    }
  }
  return false
}

// ============================================================================
// Readiness — the §3.1 rule
// ============================================================================

/**
 * `ready ← { n ∈ S : n is a leaf Send AND every Send ancestor of n has node.state
 * ∈ {edited, stale, running} or is clean }` (03 §3.1).
 *
 * Interior Sends never enter `ready` — they're dispatched as part of their
 * descendant leaf's sequence per §3.2. `failed` / `cancelled` Send ancestors
 * block the leaf (the §5.3 in-flight cascade rule, rev-15 Finding 4 anti-
 * amplification).
 *
 * Returns leaves in tree-iteration order; the dispatch loop picks via
 * `ready.popNext()` (FIFO in V1.0).
 */
export function computeReady(tree: ConversationTree, S: ReadonlySet<ConversationTreeNodeId>): SendNode[] {
  if (S.size === 0) return []
  const idx = indexTree(tree)
  const out: SendNode[] = []
  for (const n of tree.nodes) {
    if (n.kind !== 'send') continue
    if (!S.has(n.id)) continue
    if (hasSendDescendant(n.id, idx)) continue // interior Sends excluded
    if (!hasAcceptableSendAncestors(n, idx)) continue
    out.push(n)
  }
  return out
}

/**
 * Walk parents from `leaf` to the root; return true iff every SEND ancestor
 * (skipping UserTurn / Fan / Score per the §5.1 invariant 5 transparency)
 * has state in {edited, stale, running, clean}.
 *
 * The leaf's OWN state is not inspected here — it's the readiness rule's S
 * membership that admits the leaf as a candidate. Per §3.1: failed/cancelled
 * leaves DO enter S for normal waves and dispatch normally as long as their
 * ancestors are clean; the retry-failed wave is the special case that demotes
 * S-member failures back to `stale` before this check runs.
 */
function hasAcceptableSendAncestors(leaf: ConversationTreeNode, idx: TreeIndex): boolean {
  let cursor = leaf.parentId === null ? undefined : idx.byId.get(leaf.parentId)
  while (cursor !== undefined) {
    if (cursor.kind === 'send' && !ANCESTOR_ALLOWED_STATES.has(cursor.state)) {
      return false
    }
    cursor = cursor.parentId === null ? undefined : idx.byId.get(cursor.parentId)
  }
  return true
}

// ============================================================================
// S construction (§3.1) — the in-need-of-dispatch set per refresh scope
// ============================================================================

/** `S = {n ∈ tree : n.state ∈ {edited, stale, failed, cancelled}}` */
export function buildSForTree(tree: ConversationTree): Set<ConversationTreeNodeId> {
  const S = new Set<ConversationTreeNodeId>()
  for (const n of tree.nodes) {
    if (DISPATCHABLE_STATES.has(n.state)) S.add(n.id)
  }
  return S
}

/**
 * `S` scoped to the subtree rooted at `rootNodeId` (inclusive of the root).
 * Per `refreshSubtree(treeId, rootNodeId)` (03 §2.1).
 */
export function buildSForSubtree(
  tree: ConversationTree,
  rootNodeId: ConversationTreeNodeId,
): Set<ConversationTreeNodeId> {
  const idx = indexTree(tree)
  const S = new Set<ConversationTreeNodeId>()
  const root = idx.byId.get(rootNodeId)
  if (root === undefined) return S
  const queue: ConversationTreeNode[] = [root]
  while (queue.length > 0) {
    const n = queue.shift()!
    if (DISPATCHABLE_STATES.has(n.state)) S.add(n.id)
    const children = idx.childrenOf.get(n.id)
    if (children !== undefined) {
      for (const c of children) queue.push(c)
    }
  }
  return S
}

/**
 * `S` scoped to a single node. Returns the singleton `{nodeId}` if dispatchable,
 * otherwise empty. Per `refreshNode(treeId, nodeId)` (03 §2.1).
 */
export function buildSForNode(
  tree: ConversationTree,
  nodeIdToCheck: ConversationTreeNodeId,
): Set<ConversationTreeNodeId> {
  const S = new Set<ConversationTreeNodeId>()
  const n = tree.nodes.find((x) => x.id === nodeIdToCheck)
  if (n !== undefined && DISPATCHABLE_STATES.has(n.state)) S.add(n.id)
  return S
}

// ============================================================================
// Retry-failed pre-readiness demotion (§3.1 step 2b)
// ============================================================================

/**
 * For `waveTriggerKind === 'retry_failed'` only: flip every S-member node
 * currently in `{failed, cancelled}` back to `stale` and clear its execution
 * BEFORE the §3.1 readiness rule runs.
 *
 * Without this, the rule's ancestor allowlist excludes failed/cancelled, so
 * a retry wave's leaves would never enter `ready` — the wave would silently
 * no-op. Per rev-15 Finding 4 the demotion is the chosen mechanism over
 * weakening the readiness rule, because the rule's exclusion is what
 * prevents same-wave retry amplification (§5.3).
 *
 * The demotion writes through the sink (state transitions + execution clears
 * are observable side effects); per 03 §2.2 the `null` reason sentinel clears
 * `lastError` so the previous failure's error message doesn't linger.
 */
export function demoteRetryFailedNodes(
  tree: ConversationTree,
  S: ReadonlySet<ConversationTreeNodeId>,
  sink: RunnerStateSink,
): void {
  for (const node of tree.nodes) {
    if (!S.has(node.id)) continue
    if (node.state !== 'failed' && node.state !== 'cancelled') continue
    sink.setNodeState(tree.id, node.id, 'stale', { reason: null })
    sink.clearExecution(tree.id, node.id)
  }
}
