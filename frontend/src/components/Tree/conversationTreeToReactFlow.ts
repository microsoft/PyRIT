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
}

export type TreeFlowEdge = Edge<TreeFlowEdgeData, 'smoothstep'>

export interface TreeFlowAdapterResult {
  treeId: ConversationTreeId
  nodes: TreeFlowNode[]
  edges: TreeFlowEdge[]
}

// ============================================================================
// Adapter
// ============================================================================

const PLACEHOLDER_POSITION = { x: 0, y: 0 } as const

export function conversationTreeToReactFlow(tree: ConversationTree): TreeFlowAdapterResult {
  return {
    treeId: tree.id,
    nodes: tree.nodes.map(toFlowNode),
    edges: tree.edges.map(toFlowEdge),
  }
}

// ============================================================================
// Private mappers
// ============================================================================

function toFlowNode(node: ConversationTreeNode): TreeFlowNode {
  // Per-kind narrowing keeps the result's discriminated union honest. The
  // exhaustive switch will fail at compile time if a new kind lands in
  // ConversationTreeNodeKind without an arm here.
  const kind: ConversationTreeNodeKind = node.kind
  switch (kind) {
    case 'root_prompt':
      return {
        id: node.id,
        type: 'root_prompt',
        position: { ...PLACEHOLDER_POSITION },
        data: { node: node as RootPromptNode },
      }
    case 'import_message':
      return {
        id: node.id,
        type: 'import_message',
        position: { ...PLACEHOLDER_POSITION },
        data: { node: node as ImportMessageNode },
      }
    case 'user_turn':
      return {
        id: node.id,
        type: 'user_turn',
        position: { ...PLACEHOLDER_POSITION },
        data: { node: node as UserTurnNode },
      }
    case 'send':
      return {
        id: node.id,
        type: 'send',
        position: { ...PLACEHOLDER_POSITION },
        data: { node: node as SendNode },
      }
    case 'fan':
      return {
        id: node.id,
        type: 'fan',
        position: { ...PLACEHOLDER_POSITION },
        data: { node: node as FanNode },
      }
    case 'score':
      return {
        id: node.id,
        type: 'score',
        position: { ...PLACEHOLDER_POSITION },
        data: { node: node as ScoreNode },
      }
    default: {
      // Exhaustiveness check: if a new kind lands without an arm above,
      // this assignment fails at compile time.
      const _exhaustive: never = kind
      throw new Error(`conversationTreeToReactFlow: unknown node kind ${String(_exhaustive)}`)
    }
  }
}

function toFlowEdge(edge: ConversationTree['edges'][number]): TreeFlowEdge {
  return {
    id: edge.id,
    source: edge.parentId,
    target: edge.childId,
    type: 'smoothstep',
    data: { slotIndex: edge.slotIndex },
  }
}
