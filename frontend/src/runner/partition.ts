// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Path-partition resolver for the tree-UI runner.
 *
 * Given a leaf {@link SendNode} in a {@link ConversationTree}, walks the
 * root-to-leaf path and produces a dispatch plan the wave loop turns into
 * one `create_attack` + N `add_message` calls.
 *
 * Clean Sends above the first stale/edited/failed/cancelled Send are loaded
 * into `prepended_conversation` as context. The stale suffix is re-dispatched;
 * clean parent responses are not re-recorded when refreshing a leaf.
 *
 * Pure: no I/O, no React. Builds and discards an index per call; callers in
 * the hot dispatch path will memoize at their own layer.
 */

import type { PrependedMessageRequest } from '../types'
import type {
  ConversationTree,
  ConversationTreeNode,
  ConversationTreeNodeId,
  ConverterRef,
  FanAxis,
  RootPromptNode,
  SendNode,
  UserTurnNode,
} from './treeTypes'

// ============================================================================
// Public output shape
// ============================================================================

/**
 * Synthetic "user turn" form used when a SendNode's input is the root prompt
 * itself (no operator-authored UserTurn between root and Send). Has a real
 * `kind` discriminator so consumers can narrow via TS rather than duck-checks.
 * The dispatcher reads role/text/attachments uniformly across both shapes via
 * `if (ut.kind === 'synthetic_user_turn_from_root') ...` narrowing.
 */
export interface SyntheticUserTurnFromRoot {
  readonly kind: 'synthetic_user_turn_from_root'
  readonly id: ConversationTreeNodeId
  readonly role: 'user'
  readonly text: string
  readonly attachments: RootPromptNode['params']['attachments']
}

/** The variant capture for a Send sitting under a Fan ancestor on the path. */
export interface FanVariantOnPath {
  axis: FanAxis
  slotIndex: number
}

export interface FreshSuffixEntry {
  /** Either an operator-authored UserTurn or root-promoted-to-user-turn. */
  userTurn: UserTurnNode | SyntheticUserTurnFromRoot
  /** The variant from the nearest Fan ancestor since the last UserTurn. Null when none. */
  fanVariant: FanVariantOnPath | null
  /** Converter transform nodes encountered between the user turn and this Send. */
  converterPipeline: ConverterRef[]
  sendNode: SendNode
}

export interface PathPartition {
  prepended: PrependedMessageRequest[]
  freshSuffix: FreshSuffixEntry[]
  /** (axis, slotIndex) tuples for every Fan ancestor on the path, in topo order. */
  treePathSegments: Array<[FanAxis, number]>
  /** The resolved target_registry_name for this leaf (per-Send override wins over root). */
  target: string
}

// ============================================================================
// Public predicates
// ============================================================================

/**
 * Predicate retained from the V1 design's resolver model: a Send is "stale"
 * when its state demands re-dispatch OR it has no execution to reuse.
 *
 * A Send is stale when its state demands re-dispatch OR it has no execution
 * to reuse.
 */
export function isStaleForResolver(send: SendNode): boolean {
  if (send.execution === null) return true
  return (
    send.state === 'edited' ||
    send.state === 'stale' ||
    send.state === 'failed' ||
    send.state === 'cancelled'
  )
}

// ============================================================================
// Path traversal
// ============================================================================

/**
 * Walk from the leaf to the root following `parentId` pointers, then reverse.
 * Throws if `leafId` is not present in the tree.
 */
export function rootToLeafPath(
  tree: ConversationTree,
  leafId: ConversationTreeNodeId,
): ConversationTreeNode[] {
  const byId = new Map<ConversationTreeNodeId, ConversationTreeNode>()
  for (const n of tree.nodes) byId.set(n.id, n)

  const target = byId.get(leafId)
  if (target === undefined) {
    throw new Error(`resolvePathPartition: node '${leafId}' is not in tree '${tree.id}'`)
  }

  const reversed: ConversationTreeNode[] = []
  let cursor: ConversationTreeNode | undefined = target
  while (cursor !== undefined) {
    reversed.push(cursor)
    cursor = cursor.parentId === null ? undefined : byId.get(cursor.parentId)
  }
  return reversed.reverse()
}

// ============================================================================
// The main resolver
// ============================================================================

/**
 * Walk the root-to-leaf path and produce the dispatch plan. See PathPartition
 * for the output shape and the test file for the per-shape expectations.
 *
 * Preconditions (enforced; throws on violation):
 * - `leafId` exists in the tree.
 * - The node at `leafId` is a SendNode.
 * - The SendNode has no SendNode descendant (i.e., is a leaf).
 *
 * The runner's dispatch loop only calls this for leaves it picked from the
 * ready queue, so under correct caller use the assertions never fire — they
 * exist to fail loudly on buggy callers.
 */
export function resolvePathPartition(
  tree: ConversationTree,
  leafId: ConversationTreeNodeId,
): PathPartition {
  const path = rootToLeafPath(tree, leafId)
  const leaf = path[path.length - 1]
  if (leaf.kind !== 'send') {
    throw new Error(`resolvePathPartition: '${leafId}' is not a leaf Send (kind=${leaf.kind})`)
  }
  // Leaf-ness: no SendNode descendant.
  for (const n of tree.nodes) {
    if (n.kind === 'send' && n.id !== leaf.id && isAncestor(tree, leaf.id, n.id)) {
      throw new Error(`resolvePathPartition: '${leafId}' is not a leaf Send (has Send descendant '${n.id}')`)
    }
  }

  const edgeSlotByChildId = indexEdgeSlots(tree)

  // Walker state.
  const prepended: PrependedMessageRequest[] = []
  const freshSuffix: FreshSuffixEntry[] = []
  const treePathSegments: Array<[FanAxis, number]> = []
  let pendingUserTurn: UserTurnNode | SyntheticUserTurnFromRoot | null = null
  let pendingFanVariant: FanVariantOnPath | null = null
  let pendingConverterPipeline: ConverterRef[] = []
  let freshSuffixStarted = false
  let target: string | null = null
  // `target` resolves to the leaf's own override if present; otherwise the root prompt's.

  for (const node of path) {
    switch (node.kind) {
      case 'root_prompt': {
        target = node.params.targetRegistryName
        if (node.params.systemPrompt && node.params.systemPrompt.length > 0) {
          prepended.push(systemMessageOf(node.params.systemPrompt))
        }
        pendingUserTurn = promoteRootToUserTurn(node)
        pendingFanVariant = null
        pendingConverterPipeline = []
        break
      }
      case 'import_message': {
        // Not supported as a path ancestor in V1.0 dispatch (the runner walks
        // its imported context via a separate code path). Defensive throw so
        // a future caller that wires Import into the dispatch path notices.
        throw new Error(
          'resolvePathPartition: ImportMessageNode on the dispatch path is not supported in V1.0',
        )
      }
      case 'user_turn': {
        pendingUserTurn = node
        pendingFanVariant = null
        pendingConverterPipeline = []
        break
      }
      case 'converter': {
        pendingConverterPipeline = [...pendingConverterPipeline, ...node.params.pipeline]
        break
      }
      case 'fan': {
        const slotIndex = edgeSlotByChildId.get(nextOnPathChildOf(path, node) ?? node.id)
        if (slotIndex === undefined) {
          // The Fan's path-successor's edge had no slotIndex — fixture bug or
          // a malformed tree. Surfaces as a hard error to keep tests honest.
          throw new Error(
            `resolvePathPartition: Fan '${node.id}' has no edge to its path-successor child`,
          )
        }
        pendingFanVariant = { axis: node.params.axis, slotIndex }
        treePathSegments.push([node.params.axis, slotIndex])
        break
      }
      case 'send': {
        if (pendingUserTurn === null) {
          // Impossible under the tree-shape invariant (every Send has a UserTurn
          // or Root ancestor with Fan/Score transparent); defensive guard.
          throw new Error(
            `resolvePathPartition: Send '${node.id}' has no input UserTurn on its path`,
          )
        }
        const entry: FreshSuffixEntry = {
          userTurn: pendingUserTurn,
          fanVariant: pendingFanVariant,
          converterPipeline: pendingConverterPipeline,
          sendNode: node,
        }
        if (!freshSuffixStarted && canReuseAsPrepended(node)) {
          prepended.push(messageOfUserTurn(pendingUserTurn))
          prepended.push(assistantMessageOf(node.params.responsePreview!))
        } else {
          freshSuffixStarted = true
          freshSuffix.push(entry)
        }
        // Per-Send target override takes precedence; root's value is the fallback already in `target`.
        if (node.params.targetRegistryName !== undefined) {
          target = node.params.targetRegistryName
        }
        pendingUserTurn = null
        pendingFanVariant = null
        pendingConverterPipeline = []
        break
      }
      case 'score': {
        // Pure pass-through: ScoreNode is observational and does not consume
        // pending state.
        break
      }
    }
  }

  if (target === null) {
    throw new Error(`resolvePathPartition: no target resolved for leaf '${leafId}'`)
  }

  return { prepended, freshSuffix, treePathSegments, target }
}

// ============================================================================
// Private helpers
// ============================================================================

function indexEdgeSlots(tree: ConversationTree): Map<ConversationTreeNodeId, number> {
  const m = new Map<ConversationTreeNodeId, number>()
  for (const e of tree.edges) m.set(e.childId, e.slotIndex)
  return m
}

function isAncestor(
  tree: ConversationTree,
  ancestorId: ConversationTreeNodeId,
  candidateId: ConversationTreeNodeId,
): boolean {
  const byId = new Map(tree.nodes.map((n) => [n.id, n] as const))
  let cursor = byId.get(candidateId)?.parentId ?? null
  while (cursor !== null) {
    if (cursor === ancestorId) return true
    cursor = byId.get(cursor)?.parentId ?? null
  }
  return false
}

/** The next node on the root-to-leaf path after `current` (i.e., `current`'s descendant on the path). */
function nextOnPathChildOf(
  path: ConversationTreeNode[],
  current: ConversationTreeNode,
): ConversationTreeNodeId | null {
  const idx = path.indexOf(current)
  if (idx === -1 || idx === path.length - 1) return null
  return path[idx + 1].id
}

function promoteRootToUserTurn(root: RootPromptNode): SyntheticUserTurnFromRoot {
  return {
    kind: 'synthetic_user_turn_from_root',
    id: root.id,
    role: 'user',
    text: root.params.text,
    attachments: root.params.attachments,
  }
}

function systemMessageOf(systemPrompt: string): PrependedMessageRequest {
  return {
    role: 'system',
    pieces: [
      {
        data_type: 'text',
        original_value: systemPrompt,
        converted_value: systemPrompt,
      },
    ],
  }
}

function canReuseAsPrepended(send: SendNode): boolean {
  return !isStaleForResolver(send) && send.params.responsePreview !== undefined && send.params.responsePreview.length > 0
}

function messageOfUserTurn(userTurn: UserTurnNode | SyntheticUserTurnFromRoot): PrependedMessageRequest {
  const text = userTurn.kind === 'synthetic_user_turn_from_root' ? userTurn.text : userTurn.params.text
  const attachments = userTurn.kind === 'synthetic_user_turn_from_root' ? userTurn.attachments : userTurn.params.attachments
  const role = userTurn.kind === 'synthetic_user_turn_from_root' ? userTurn.role : userTurn.params.role
  return {
    role,
    pieces: [
      ...attachments.map((attachment) => ({
        data_type: attachment.dataType,
        original_value: attachment.value,
        mime_type: attachment.mimeType,
        original_prompt_id: attachment.originalPromptId,
      })),
      { data_type: 'text' as const, original_value: text },
    ],
  }
}

function assistantMessageOf(response: string): PrependedMessageRequest {
  return {
    role: 'assistant',
    pieces: [{ data_type: 'text', original_value: response, converted_value: response }],
  }
}
