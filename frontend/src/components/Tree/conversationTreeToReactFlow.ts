// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Adapter: ConversationTree → react-flow Node[] + Edge[].
 *
 * Pure function, no react-flow runtime dependency (only types). The PR5b
 * node components register by kind into ReactFlow's `nodeTypes` prop; PR5g
 * wraps this output with `d3-hierarchy` layout to compute final positions.
 *
 * Edge type 'smoothstep' = orthogonal routing (rounded corners), the
 * tree-diagram standard. Edge data carries slotIndex so the PR5e Stack
 * predicate + PR5f Pick/Unpick can read it directly.
 */

import type { Edge, Node } from '@xyflow/react'

import { computeStackAggregate, type StackAggregate } from './fanStack'
import type {
  ConversationTree,
  ConversationTreeId,
  ConversationTreeNode,
  ConversationTreeNodeId,
  ConversationTreeNodeKind,
  FanNode,
  ImportMessageNode,
  RootPromptNode,
  ScoreNode,
  SendNode,
  UserTurnNode,
} from '../../runner/treeTypes'

// ============================================================================
// Result types — kind-discriminated so PR5b's node components can narrow
// ============================================================================

/**
 * Per-card fan-child context. Attached by the adapter to every node whose
 * parent is a FanNode; absent when the node is not a fan-child. Cards
 * read these to render the dim / promoted visual + the per-child Pick
 * toggle on the action rail (PR5f).
 */
export interface FanChildInfo {
  parentFanId: ConversationTreeNodeId
  slotIndex: number
  /** True when the parent fan's promotedChildSlotIndex matches this child. */
  promoted: boolean
  /** True when the parent has a promotion AND this child is NOT it. */
  dimmed: boolean
}

export type TreeFlowNode =
  | Node<{ node: RootPromptNode; fanChildInfo?: FanChildInfo }, 'root_prompt'>
  | Node<{ node: ImportMessageNode; fanChildInfo?: FanChildInfo }, 'import_message'>
  | Node<{ node: UserTurnNode; fanChildInfo?: FanChildInfo }, 'user_turn'>
  | Node<{ node: SendNode; fanChildInfo?: FanChildInfo }, 'send'>
  | Node<
      { node: FanNode; fanChildInfo?: FanChildInfo; stackedSummary?: StackAggregate },
      'fan'
    >
  | Node<{ node: ScoreNode; fanChildInfo?: FanChildInfo }, 'score'>

export interface TreeFlowEdgeData extends Record<string, unknown> {
  /** Mirror of the source `ConversationTreeEdge.slotIndex`. */
  slotIndex: number
  /**
   * Source node's kind, surfaced on the edge so PR5d's insert-on-edge
   * `+` chip can pick the kind-aware menu without doing a tree lookup
   * at render. Adapter computes it once per edge.
   */
  parentKind: ConversationTreeNodeKind
}

export type TreeFlowEdge = Edge<TreeFlowEdgeData, 'smoothstep' | 'insert'>

export interface TreeFlowAdapterResult {
  treeId: ConversationTreeId
  nodes: TreeFlowNode[]
  edges: TreeFlowEdge[]
}

export interface TreeFlowAdapterOptions {
  /**
   * Set of fan-node ids whose children render as a collapsed Fan-Children
   * Stack. When a fan is in this set, the adapter:
   *   - drops the fan's descendant subtrees from the result (the
   *     children + everything below)
   *   - attaches a `stackedSummary: StackAggregate` to the fan's `data`
   *     so the FanCard renders the stack body in place of the per-child
   *     cards.
   * When omitted or empty, the adapter behaves exactly as in PR5d
   * (1:1 node + edge mapping, no stack collapse).
   */
  collapsedFanIds?: ReadonlySet<ConversationTreeNodeId>
}

// ============================================================================
// Adapter
// ============================================================================

const PLACEHOLDER_POSITION = { x: 0, y: 0 } as const

// Placeholder dimensions for every node. react-flow won't render edges
// until source + target nodes have measured dimensions; in production
// the cards report their real size on mount, but tests + initial render
// need defaults so edges (and the PR5d insert chip) appear. PR5g's
// layout pass overrides positions; the runtime dimensions resolve once
// the DOM measures the actual card.
const PLACEHOLDER_WIDTH = 260
const PLACEHOLDER_HEIGHT = 80

export function conversationTreeToReactFlow(
  tree: ConversationTree,
  options: TreeFlowAdapterOptions = {},
): TreeFlowAdapterResult {
  const collapsedFanIds = options.collapsedFanIds ?? EMPTY_SET
  const nodeKindById = new Map<string, ConversationTreeNodeKind>()
  for (const n of tree.nodes) nodeKindById.set(n.id, n.kind)

  // Build a parent-fan lookup so each fan-child can pick up its parent's
  // promotedChildSlotIndex without a tree walk per node. Map childId →
  // { parentFan, slotIndex }. Lookup is O(1) per child; assembly is
  // O(nodes + edges).
  const fanChildIndex = new Map<
    ConversationTreeNodeId,
    { parentFan: FanNode; slotIndex: number }
  >()
  const fansById = new Map<ConversationTreeNodeId, FanNode>()
  for (const n of tree.nodes) {
    if (n.kind === 'fan') fansById.set(n.id, n)
  }
  for (const edge of tree.edges) {
    const parent = fansById.get(edge.parentId)
    if (parent === undefined) continue
    fanChildIndex.set(edge.childId, { parentFan: parent, slotIndex: edge.slotIndex })
  }

  // Compute the set of node ids hidden by stack collapse: every
  // descendant (recursive) of every collapsed fan. The fan node itself
  // stays visible; only its subtree below disappears.
  const hiddenNodeIds = collapsedFanIds.size === 0
    ? EMPTY_SET
    : collectHiddenDescendants(tree, collapsedFanIds)

  const visibleNodes = tree.nodes.filter((n) => !hiddenNodeIds.has(n.id))
  const visibleEdges = tree.edges.filter(
    (e) => !hiddenNodeIds.has(e.parentId) && !hiddenNodeIds.has(e.childId),
  )

  return {
    treeId: tree.id,
    nodes: visibleNodes.map((n) =>
      toFlowNode(n, tree, collapsedFanIds, fanChildIndex),
    ),
    edges: visibleEdges.map((e) => toFlowEdge(e, nodeKindById)),
  }
}

const EMPTY_SET: ReadonlySet<ConversationTreeNodeId> = new Set()

// ============================================================================
// Private mappers
// ============================================================================

function toFlowNode(
  node: ConversationTreeNode,
  tree: ConversationTree,
  collapsedFanIds: ReadonlySet<ConversationTreeNodeId>,
  fanChildIndex: ReadonlyMap<
    ConversationTreeNodeId,
    { parentFan: FanNode; slotIndex: number }
  >,
): TreeFlowNode {
  // Per-kind narrowing keeps the result's discriminated union honest. The
  // exhaustive switch will fail at compile time if a new kind lands in
  // ConversationTreeNodeKind without an arm here.
  const fanChildInfo = computeFanChildInfo(node.id, fanChildIndex)
  const common = {
    id: node.id,
    position: { ...PLACEHOLDER_POSITION },
    width: PLACEHOLDER_WIDTH,
    height: PLACEHOLDER_HEIGHT,
  }
  const kind: ConversationTreeNodeKind = node.kind
  switch (kind) {
    case 'root_prompt':
      return {
        ...common,
        type: 'root_prompt',
        data: { node: node as RootPromptNode, fanChildInfo },
      }
    case 'import_message':
      return {
        ...common,
        type: 'import_message',
        data: { node: node as ImportMessageNode, fanChildInfo },
      }
    case 'user_turn':
      return {
        ...common,
        type: 'user_turn',
        data: { node: node as UserTurnNode, fanChildInfo },
      }
    case 'send':
      return {
        ...common,
        type: 'send',
        data: { node: node as SendNode, fanChildInfo },
      }
    case 'fan': {
      const fanData: {
        node: FanNode
        fanChildInfo?: FanChildInfo
        stackedSummary?: StackAggregate
      } = { node: node as FanNode, fanChildInfo }
      if (collapsedFanIds.has(node.id)) {
        fanData.stackedSummary = computeStackAggregate(tree, node.id)
      }
      return { ...common, type: 'fan', data: fanData }
    }
    case 'score':
      return {
        ...common,
        type: 'score',
        data: { node: node as ScoreNode, fanChildInfo },
      }
    default: {
      // Exhaustiveness check: if a new kind lands without an arm above,
      // this assignment fails at compile time.
      const _exhaustive: never = kind
      throw new Error(`conversationTreeToReactFlow: unknown node kind ${String(_exhaustive)}`)
    }
  }
}

function toFlowEdge(
  edge: ConversationTree['edges'][number],
  nodeKindById: ReadonlyMap<string, ConversationTreeNodeKind>,
): TreeFlowEdge {
  const parentKind = nodeKindById.get(edge.parentId) ?? 'root_prompt'
  // Use the custom 'insert' edge type by default; TreeCanvas's edgeTypes
  // registry maps 'insert' to the InsertEdge component (which extends
  // SmoothStepEdge with a midpoint `+` chip). Falls back to the built-in
  // 'smoothstep' rendering when no edgeTypes entry registers — the chip
  // is suppressed in that case via the InsertEdge's callback-presence
  // check at render.
  return {
    id: edge.id,
    source: edge.parentId,
    target: edge.childId,
    type: 'insert',
    data: { slotIndex: edge.slotIndex, parentKind },
  }
}

/**
 * Walk every collapsed fan's subtree and collect all descendant ids
 * (the fan itself stays visible — only the subtree below disappears).
 * Returns an empty set on empty input so the caller can skip the
 * filter entirely.
 */
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

/**
 * Build the per-card FanChildInfo if `node` is a fan-child; return
 * undefined otherwise. Promoted = this child's slotIndex matches the
 * parent fan's `promotedChildSlotIndex`; dimmed = a sibling slot is
 * promoted instead.
 */
function computeFanChildInfo(
  nodeIdToCheck: ConversationTreeNodeId,
  fanChildIndex: ReadonlyMap<
    ConversationTreeNodeId,
    { parentFan: FanNode; slotIndex: number }
  >,
): FanChildInfo | undefined {
  const entry = fanChildIndex.get(nodeIdToCheck)
  if (entry === undefined) return undefined
  const promotedSlot = entry.parentFan.params.promotedChildSlotIndex
  const promoted = promotedSlot !== null && promotedSlot === entry.slotIndex
  const dimmed = promotedSlot !== null && promotedSlot !== entry.slotIndex
  return {
    parentFanId: entry.parentFan.id,
    slotIndex: entry.slotIndex,
    promoted,
    dimmed,
  }
}
