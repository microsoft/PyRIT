// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Auto-reverse pure functions per spec 01 §9.3 + §9.3.1 Algorithm 1.
 *
 * Three entry points consumed by the reload-reconstruction host:
 *   - `parseTreePath` — decode a leaf AR's `labels.tree_path`
 *     (JSON-encoded `[[axis, slotIndex], ...]`); fail-soft to `[]`.
 *   - `linearChainFromMessages` — build a root→user→send→user→send
 *     `ConversationTree` from a single conversation's `BackendMessage[]`.
 *     System messages hoist into the root's `systemPrompt`.
 *   - `detectFansV10Plus` — Algorithm 1 (01 §9.3.1) — groups leaves
 *     by `(parent_path, axis)` to reconstruct fan topology.
 *   - `reconstructVariantPayloads` — derives per-slot variant payloads
 *     for axes `attempt` (empty) and `converter` (consensus by
 *     most-frequent + divergence callback).
 *
 * No React. No async. Pure functions over wire-shape inputs.
 */

import type {
  ConversationTree,
  ConversationTreeEdge,
  ConversationTreeId,
  ConversationTreeNode,
  ConversationTreeNodeId,
  ConverterRef,
  FanAxis,
  FanVariant,
  RootPromptNode,
  SendNode,
  UserTurnNode,
} from './treeTypes'
import type { BackendMessage, ComponentIdentifier } from '../types'

// ============================================================================
// parseTreePath
// ============================================================================

export type TreePathSegment = readonly [string, number]

export function parseTreePath(raw: string): TreePathSegment[] {
  if (raw === '') return []
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return []
  }
  if (!Array.isArray(parsed)) return []
  const out: TreePathSegment[] = []
  for (const seg of parsed) {
    if (!Array.isArray(seg) || seg.length !== 2) return []
    const [axis, slot] = seg
    if (typeof axis !== 'string' || typeof slot !== 'number') return []
    out.push([axis, slot])
  }
  return out
}

// ============================================================================
// linearChainFromMessages
// ============================================================================

const NOW = '2026-06-11T00:00:00.000Z'

function emptyHash(): string {
  return ''
}

function mintTreeId(): ConversationTreeId {
  return crypto.randomUUID() as ConversationTreeId
}

function mintNodeId(): ConversationTreeNodeId {
  return crypto.randomUUID() as ConversationTreeNodeId
}

function buildConverterPipeline(
  identifiers: ComponentIdentifier[],
): ConverterRef[] {
  return identifiers.map((id) => ({
    inline: { type: id.class_name, params: id.params },
  }))
}

function emptyTree(): ConversationTree {
  return {
    id: mintTreeId(),
    nodes: [],
    edges: [],
    rootId: '' as ConversationTreeNodeId,
    displayName: '',
    createdAt: NOW,
    parentConversationTreeId: null,
    parentSourceConversationId: null,
    undoStack: [],
  }
}

export function linearChainFromMessages(
  messages: BackendMessage[],
): ConversationTree {
  if (messages.length === 0) return emptyTree()

  // Sort by turn_number to be defensive against unordered backend payloads.
  const sorted = [...messages].sort((a, b) => a.turn_number - b.turn_number)

  // Hoist leading system message(s) into root.systemPrompt.
  const systemTexts: string[] = []
  let i = 0
  while (i < sorted.length && sorted[i].role === 'system') {
    const firstPiece = sorted[i].pieces[0]
    if (firstPiece !== undefined) {
      systemTexts.push(firstPiece.converted_value ?? firstPiece.original_value ?? '')
    }
    i += 1
  }

  // First non-system message must be a user turn for a well-formed conversation.
  const firstNonSystem = sorted[i]
  if (firstNonSystem === undefined || firstNonSystem.role !== 'user') {
    // Defensive: malformed input. Return greenfield rather than crash; the
    // caller surfaces this via the reload "Tree not found" banner.
    return emptyTree()
  }

  const treeId = mintTreeId()
  const rootId = mintNodeId()
  const firstUserPiece = firstNonSystem.pieces[0]
  const firstUserText = firstUserPiece?.converted_value ?? firstUserPiece?.original_value ?? ''

  const root: RootPromptNode = {
    id: rootId,
    kind: 'root_prompt',
    parentId: null,
    resolvedInputHash: emptyHash(),
    state: 'clean',
    execution: null,
    executionHistory: [],
    lastError: null,
    labels: {},
    createdAt: NOW,
    updatedAt: NOW,
    version: 1,
    params: {
      text: firstUserText,
      attachments: [],
      ...(systemTexts.length > 0 ? { systemPrompt: systemTexts.join('\n\n') } : {}),
      targetRegistryName: '',
    },
  }

  const nodes: ConversationTreeNode[] = [root]
  const edges: ConversationTreeEdge[] = []
  let parentId: ConversationTreeNodeId = rootId
  i += 1 // consumed first user message into root

  for (; i < sorted.length; i += 1) {
    const msg = sorted[i]
    const piece = msg.pieces[0]
    const text = piece?.converted_value ?? piece?.original_value ?? ''
    if (msg.role === 'assistant') {
      const sendId = mintNodeId()
      const send: SendNode = {
        id: sendId,
        kind: 'send',
        parentId,
        resolvedInputHash: emptyHash(),
        state: 'clean',
        execution: null,
        executionHistory: [],
        lastError: null,
        labels: {},
        createdAt: NOW,
        updatedAt: NOW,
        version: 1,
        params: {},
      }
      nodes.push(send)
      edges.push({ id: `${parentId}->${sendId}`, parentId, childId: sendId, slotIndex: 0 })
      parentId = sendId
    } else if (
      msg.role === 'user' ||
      msg.role === 'simulated_assistant' ||
      msg.role === 'system'
    ) {
      const userId = mintNodeId()
      const converters = piece !== undefined ? buildConverterPipeline(piece.converter_identifiers) : []
      const userTurn: UserTurnNode = {
        id: userId,
        kind: 'user_turn',
        parentId,
        resolvedInputHash: emptyHash(),
        state: 'clean',
        execution: null,
        executionHistory: [],
        lastError: null,
        labels: {},
        createdAt: NOW,
        updatedAt: NOW,
        version: 1,
        params: {
          role: msg.role,
          text,
          attachments: [],
          ...(converters.length > 0 ? { converterPipeline: converters } : {}),
        },
      }
      nodes.push(userTurn)
      edges.push({ id: `${parentId}->${userId}`, parentId, childId: userId, slotIndex: 0 })
      parentId = userId
    }
  }

  return {
    id: treeId,
    nodes,
    edges,
    rootId,
    displayName: firstUserText.slice(0, 40),
    createdAt: NOW,
    parentConversationTreeId: null,
    parentSourceConversationId: null,
    undoStack: [],
  }
}

// ============================================================================
// detectFansV10Plus
// ============================================================================

/**
 * Minimal leaf shape consumed by Algorithm 1. The reload host adapts each
 * leaf `AttackSummary` into this shape; tests construct it directly.
 */
export interface LeafForFanDetection {
  attack_result_id: string
  labels: Record<string, string>
}

export interface ImplicitFan {
  parent_path: TreePathSegment[]
  axis: string
  /** One entry per contributing leaf, parallel to `member_slot_indices`. */
  member_ars: LeafForFanDetection[]
  /**
   * Per-leaf slot contributions, parallel to `member_ars` (NOT a list of
   * distinct slots). A nested fan's leaves repeat the same slot index once
   * per descendant leaf, so this array can contain duplicates — callers
   * computing slot COUNT must dedupe (e.g. `new Set(member_slot_indices).size`),
   * and callers sizing the variant array use `Math.max(...)+1`.
   * `reconstructVariantPayloads` relies on the duplicates as per-slot votes.
   */
  member_slot_indices: number[]
}

export function detectFansV10Plus(
  leaves: ReadonlyArray<LeafForFanDetection>,
): ImplicitFan[] {
  // Per spec §9.3.1 Algorithm 1: every (parent_path, axis) prefix with ≥2
  // distinct member entries is one ImplicitFan. The spec's pseudocode only
  // buckets at the terminal segment (path[:-1]); for nested fans the prompt-
  // ancestor of an attempt-leaf must also surface as its own fan, otherwise
  // §9.4.1 reload reconstructs the inner attempt fan but loses the outer
  // prompt fan entirely. We walk every depth d ≥ 1 per leaf and bucket by
  // (path[:d-1], path[d-1].axis). A leaf at depth D contributes to D groups.
  interface Entry {
    leaf: LeafForFanDetection
    parentKey: string
    axis: string
    slotIndex: number
  }
  const entries: Entry[] = []
  for (const leaf of leaves) {
    const path = parseTreePath(leaf.labels.tree_path ?? '')
    for (let d = 1; d <= path.length; d += 1) {
      const segment = path[d - 1]
      entries.push({
        leaf,
        parentKey: JSON.stringify(path.slice(0, d - 1)),
        axis: segment[0],
        slotIndex: segment[1],
      })
    }
  }

  // Group by parent_path string-key then by axis.
  const byParent = new Map<string, Map<string, Entry[]>>()
  for (const e of entries) {
    let byAxis = byParent.get(e.parentKey)
    if (byAxis === undefined) {
      byAxis = new Map()
      byParent.set(e.parentKey, byAxis)
    }
    const group = byAxis.get(e.axis) ?? []
    group.push(e)
    byAxis.set(e.axis, group)
  }

  const fans: ImplicitFan[] = []
  for (const [parentKey, byAxis] of byParent.entries()) {
    const parentPath = JSON.parse(parentKey) as TreePathSegment[]
    for (const [axis, group] of byAxis.entries()) {
      // The group must contain ≥2 distinct slot indices to be a real fan;
      // multiple leaves at the SAME slot (nested-fan descendants of one
      // outer slot) don't on their own indicate a fan at this depth.
      const distinctSlots = new Set(group.map((e) => e.slotIndex))
      if (distinctSlots.size < 2) continue
      fans.push({
        parent_path: parentPath,
        axis,
        member_ars: group.map((g) => g.leaf),
        member_slot_indices: group.map((g) => g.slotIndex),
      })
    }
  }
  return fans
}

// ============================================================================
// reconstructVariantPayloads
// ============================================================================

export type ConverterResolver = (leaf: LeafForFanDetection) => ComponentIdentifier[]

export interface ReconstructOptions {
  /** Fires once per slot with disagreeing converter pipelines. */
  onDivergence?: (slotIndex: number) => void
}

export function reconstructVariantPayloads(
  fan: ImplicitFan,
  converterResolver: ConverterResolver,
  options: ReconstructOptions = {},
): FanVariant[] {
  if (fan.axis === 'attempt') {
    return reconstructAttemptVariants(fan)
  }
  if (fan.axis === 'converter') {
    return reconstructConverterVariants(fan, converterResolver, options)
  }
  throw new Error(
    `reconstructVariantPayloads: axis='${fan.axis}' is not implemented; V1.0 supports 'attempt' and 'converter'.`,
  )
}

function reconstructAttemptVariants(fan: ImplicitFan): FanVariant[] {
  const max = Math.max(...fan.member_slot_indices)
  const variants: FanVariant[] = []
  for (let s = 0; s <= max; s += 1) {
    variants.push({ axis: 'attempt', payload: {} })
  }
  return variants
}

function reconstructConverterVariants(
  fan: ImplicitFan,
  resolver: ConverterResolver,
  options: ReconstructOptions,
): FanVariant[] {
  const bySlot = new Map<number, ComponentIdentifier[][]>()
  for (let i = 0; i < fan.member_ars.length; i += 1) {
    const slot = fan.member_slot_indices[i]
    const list = bySlot.get(slot) ?? []
    list.push(resolver(fan.member_ars[i]))
    bySlot.set(slot, list)
  }
  const max = Math.max(...fan.member_slot_indices)
  const variants: FanVariant[] = []
  for (let s = 0; s <= max; s += 1) {
    const candidates = bySlot.get(s)
    if (candidates === undefined || candidates.length === 0) {
      variants.push({ axis: 'converter', payload: { converters: [] } })
      continue
    }
    const { winner, divergent } = mostFrequent(candidates)
    if (divergent) options.onDivergence?.(s)
    variants.push({
      axis: 'converter',
      payload: { converters: buildConverterPipeline(winner) },
    })
  }
  return variants
}

function mostFrequent<T>(candidates: T[][]): { winner: T[]; divergent: boolean } {
  // Bucket by JSON-shape equality; pick the highest-count bucket.
  const counts = new Map<string, { count: number; value: T[] }>()
  for (const c of candidates) {
    const key = JSON.stringify(c)
    const entry = counts.get(key) ?? { count: 0, value: c }
    entry.count += 1
    counts.set(key, entry)
  }
  let winner: T[] = candidates[0]
  let winnerCount = 0
  for (const { count, value } of counts.values()) {
    if (count > winnerCount) {
      winnerCount = count
      winner = value
    }
  }
  return { winner, divergent: counts.size > 1 }
}

// Re-export FanAxis so the host doesn't need to dual-import.
export type { FanAxis }
