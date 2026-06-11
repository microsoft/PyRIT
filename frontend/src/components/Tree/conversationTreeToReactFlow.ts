// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Adapter: ConversationTree → react-flow Node[] + Edge[].
 *
 * Pure shape mapping: 1:1 node + edge translation, with per-edge
 * `parentKind` and per-fan-child `fanChildInfo` attached. No render-time
 * policy — collapse filtering and `stackedSummary` attachment live in the
 * companion `applyStackCollapse` pass so the adapter output stays
 * reference-stable across UI-state changes that don't alter shape (Pick,
 * wave-state flips). The TreeCanvas pipeline runs `adapter → collapse →
 * layout`; layout memoizes on the adapter output, which lets a 60-leaf
 * wave's per-leaf state flips re-render cards without re-running layout.
 */

import type { Edge, Node } from '@xyflow/react'

import type { StackAggregate } from './fanStack'
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
): TreeFlowAdapterResult {
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

  return {
    treeId: tree.id,
    nodes: tree.nodes.map((n) => toFlowNode(n, fanChildIndex)),
    edges: tree.edges.map((e) => toFlowEdge(e, nodeKindById)),
  }
}

// ============================================================================
// Private mappers
// ============================================================================

function toFlowNode(
  node: ConversationTreeNode,
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
    case 'fan':
      return {
        ...common,
        type: 'fan',
        data: { node: node as FanNode, fanChildInfo },
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
  // Adapter contract: tree.edges must reference tree.nodes. A missing
  // parentId here means the input tree is malformed; throwing surfaces
  // the bug loudly rather than steering the InsertEdge menu to a
  // silently-wrong kind via a 'root_prompt' fallback.
  const parentKind = nodeKindById.get(edge.parentId)
  if (parentKind === undefined) {
    throw new Error(
      `conversationTreeToReactFlow: edge ${edge.id} references parentId ` +
        `${edge.parentId} which is not present in tree.nodes`,
    )
  }
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
