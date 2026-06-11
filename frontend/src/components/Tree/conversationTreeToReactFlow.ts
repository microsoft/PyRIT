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

import type {
  ConversationTree,
  ConversationTreeId,
  ConversationTreeNode,
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

export type TreeFlowNode =
  | Node<{ node: RootPromptNode }, 'root_prompt'>
  | Node<{ node: ImportMessageNode }, 'import_message'>
  | Node<{ node: UserTurnNode }, 'user_turn'>
  | Node<{ node: SendNode }, 'send'>
  | Node<{ node: FanNode }, 'fan'>
  | Node<{ node: ScoreNode }, 'score'>

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

export function conversationTreeToReactFlow(tree: ConversationTree): TreeFlowAdapterResult {
  const nodeKindById = new Map<string, ConversationTreeNodeKind>()
  for (const n of tree.nodes) nodeKindById.set(n.id, n.kind)
  return {
    treeId: tree.id,
    nodes: tree.nodes.map(toFlowNode),
    edges: tree.edges.map((e) => toFlowEdge(e, nodeKindById)),
  }
}

// ============================================================================
// Private mappers
// ============================================================================

function toFlowNode(node: ConversationTreeNode): TreeFlowNode {
  // Per-kind narrowing keeps the result's discriminated union honest. The
  // exhaustive switch will fail at compile time if a new kind lands in
  // ConversationTreeNodeKind without an arm here.
  const common = {
    id: node.id,
    position: { ...PLACEHOLDER_POSITION },
    width: PLACEHOLDER_WIDTH,
    height: PLACEHOLDER_HEIGHT,
  }
  const kind: ConversationTreeNodeKind = node.kind
  switch (kind) {
    case 'root_prompt':
      return { ...common, type: 'root_prompt', data: { node: node as RootPromptNode } }
    case 'import_message':
      return { ...common, type: 'import_message', data: { node: node as ImportMessageNode } }
    case 'user_turn':
      return { ...common, type: 'user_turn', data: { node: node as UserTurnNode } }
    case 'send':
      return { ...common, type: 'send', data: { node: node as SendNode } }
    case 'fan':
      return { ...common, type: 'fan', data: { node: node as FanNode } }
    case 'score':
      return { ...common, type: 'score', data: { node: node as ScoreNode } }
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
