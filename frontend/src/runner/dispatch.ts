// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Leaf-dispatch orchestrator. Turns one leaf SendNode's partition plan into
 * one `create_attack` + N `add_message` HTTP calls. Owns:
 *   - the per-leaf concurrency-slot's API call sequence
 *   - state transitions for every Send in the fresh suffix
 *   - ExecutionRecord attachment for each successfully-completed Send
 *   - mid-chain partial-commit semantics on failure
 *   - the 200-message cap short-circuit
 *
 * The dispatcher is the only place in the runner that talks to the API
 * client; this is also where the labels-divergence invariant gets enforced
 * at the call site (one `buildLabels` call per dispatch; the same dict
 * passed on every request).
 */

import type {
  AddMessageRequest,
  AddMessageResponse,
  BackendMessage,
  BackendMessagePiece,
  CreateAttackRequest,
  CreateAttackResponse,
  MessagePieceRequest,
} from '../types'
import { toApiError } from '../services/errors'
import { buildLabels, formatApiError } from './dispatchHelpers'
import { resolvePathPartition } from './partition'
import type { FreshSuffixEntry, PathPartition } from './partition'
import type {
  ConversationTree,
  ConversationTreeId,
  ConversationTreeNodeId,
  ExecutionRecord,
  NodeFailureClass,
  RunnerStateSink,
  WaveTriggerKind,
} from './treeTypes'

// ============================================================================
// Public types
// ============================================================================

/** The subset of the backend attacks API the runner uses. */
export interface RunnerAttacksApi {
  createAttack(request: CreateAttackRequest): Promise<CreateAttackResponse>
  addMessage(attackResultId: string, request: AddMessageRequest): Promise<AddMessageResponse>
}

export interface DispatchLeafArgs {
  treeId: ConversationTreeId
  tree: ConversationTree
  leafId: ConversationTreeNodeId
  sink: RunnerStateSink
  api: RunnerAttacksApi
  operator: string
  operation: string
  waveId: string
  waveTriggerKind: WaveTriggerKind
  parentConversationTreeId: ConversationTreeId | null
}

export type LeafDispatchOutcome =
  | {
      kind: 'success'
      leafId: ConversationTreeNodeId
      callsIssued: number
    }
  | {
      kind: 'failed'
      leafId: ConversationTreeNodeId
      failedNodeId: ConversationTreeNodeId
      failureClass: NodeFailureClass
      /** The partial AR id if create_attack succeeded; null if the failure was pre-create_attack. */
      partialAttackResultId: string | null
    }

// ============================================================================
// Public entry point
// ============================================================================

/** Backend `CreateAttackRequest.prepended_conversation` cap (Pydantic max_length=200). */
const PREPENDED_CAP = 200

export async function dispatchLeaf(args: DispatchLeafArgs): Promise<LeafDispatchOutcome> {
  const partition = resolvePathPartition(args.tree, args.leafId)

  // 200-cap short-circuit. The cap is on prepended_conversation only; the
  // post-create_attack add_message calls extend the AR's conversation past
  // 200 messages cleanly. Operator recovery is branch-from-midpoint.
  if (partition.prepended.length > PREPENDED_CAP) {
    const reason = {
      message: `Clean prefix exceeds ${PREPENDED_CAP}-turn ceiling — branch from a midpoint to continue`,
      failure_class: 'permanent' as const,
    }
    args.sink.setNodeState(args.treeId, args.leafId, 'failed', { reason })
    args.sink.clearExecution(args.treeId, args.leafId)
    return {
      kind: 'failed',
      leafId: args.leafId,
      failedNodeId: args.leafId,
      failureClass: 'permanent',
      partialAttackResultId: null,
    }
  }

  const labels = buildLabels({
    operator: args.operator,
    operation: args.operation,
    treeId: args.treeId,
    waveId: args.waveId,
    waveTriggerKind: args.waveTriggerKind,
    treePathSegments: partition.treePathSegments,
    parentConversationTreeId: args.parentConversationTreeId,
  })

  // Mark every fresh-suffix Send as `running` atomically at sequence start.
  // The §3.1 dispatch loop assumes the dispatcher owns these transitions;
  // siblings observing the in-progress state see them all `running` together
  // rather than one-at-a-time.
  for (const entry of partition.freshSuffix) {
    args.sink.setNodeState(args.treeId, entry.sendNode.id, 'running')
  }

  // ----- create_attack -----

  let createResp: CreateAttackResponse
  try {
    const req: CreateAttackRequest = {
      target_registry_name: partition.target,
      prepended_conversation: partition.prepended,
      labels,
    }
    createResp = await args.api.createAttack(req)
  } catch (raw) {
    const reason = formatApiError(toApiError(raw), 'create_attack')
    failRemaining({
      sink: args.sink,
      treeId: args.treeId,
      entries: partition.freshSuffix,
      failedAt: 0,
      reason,
    })
    return {
      kind: 'failed',
      leafId: args.leafId,
      failedNodeId: partition.freshSuffix[0].sendNode.id,
      failureClass: reason.failure_class as NodeFailureClass,
      partialAttackResultId: null,
    }
  }

  // ----- N add_messages -----

  let priorMaxTurnNumber = partition.prepended.length
  for (let i = 0; i < partition.freshSuffix.length; i++) {
    const entry = partition.freshSuffix[i]
    const req: AddMessageRequest = {
      role: 'user',
      pieces: piecesForUserTurn(entry),
      send: true,
      target_registry_name: partition.target,
      target_conversation_id: createResp.conversation_id,
      converter_ids: resolvedConverterIds(entry),
      labels,
    }
    let resp: AddMessageResponse
    try {
      resp = await args.api.addMessage(createResp.attack_result_id, req)
    } catch (raw) {
      const reason = formatApiError(toApiError(raw), 'add_message')
      // The failing Send → failed; later Sends → stale (roll-back).
      args.sink.setNodeState(args.treeId, entry.sendNode.id, 'failed', { reason })
      args.sink.clearExecution(args.treeId, entry.sendNode.id)
      for (const later of partition.freshSuffix.slice(i + 1)) {
        args.sink.setNodeState(args.treeId, later.sendNode.id, 'stale')
        args.sink.clearExecution(args.treeId, later.sendNode.id)
      }
      return {
        kind: 'failed',
        leafId: args.leafId,
        failedNodeId: entry.sendNode.id,
        failureClass: reason.failure_class as NodeFailureClass,
        partialAttackResultId: createResp.attack_result_id,
      }
    }

    // Diff by turn_number to extract the assistant pieces newly produced
    // by THIS add_message. The response carries the entire conversation;
    // anything strictly above the prior watermark is new.
    const { newPieces, newMax } = extractNewAssistantPieces(resp, priorMaxTurnNumber)
    priorMaxTurnNumber = newMax

    const record: ExecutionRecord = buildExecutionRecord({
      attackResultId: createResp.attack_result_id,
      conversationId: createResp.conversation_id,
      newPieces,
      hashAtExecution: entry.sendNode.resolvedInputHash,
      waveId: args.waveId,
      waveTriggerKind: args.waveTriggerKind,
    })
    args.sink.recordExecution(args.treeId, entry.sendNode.id, record)
    args.sink.setNodeState(args.treeId, entry.sendNode.id, 'clean')
  }

  return {
    kind: 'success',
    leafId: args.leafId,
    callsIssued: 1 + partition.freshSuffix.length,
  }
}

// ============================================================================
// Private helpers
// ============================================================================

/**
 * Mark every fresh-suffix Send `failed` with the same reason — used for the
 * create_attack-failure path where no AR exists and no Send can ever land.
 */
function failRemaining(args: {
  sink: RunnerStateSink
  treeId: ConversationTreeId
  entries: ReadonlyArray<FreshSuffixEntry>
  failedAt: number
  reason: ReturnType<typeof formatApiError>
}): void {
  for (let i = args.failedAt; i < args.entries.length; i++) {
    const sid = args.entries[i].sendNode.id
    args.sink.setNodeState(args.treeId, sid, 'failed', { reason: args.reason })
    args.sink.clearExecution(args.treeId, sid)
  }
}

function piecesForUserTurn(entry: FreshSuffixEntry): MessagePieceRequest[] {
  const ut = entry.userTurn
  // Discriminated narrowing via the `kind` field (synthetic vs real UserTurnNode).
  // Both shapes expose role/text/attachments uniformly to the request builder.
  const text = ut.kind === 'synthetic_user_turn_from_root' ? ut.text : ut.params.text
  const attachments =
    ut.kind === 'synthetic_user_turn_from_root' ? ut.attachments : ut.params.attachments

  const pieces: MessagePieceRequest[] = attachments.map((a) => ({
    data_type: a.dataType,
    original_value: a.value,
    mime_type: a.mimeType,
    original_prompt_id: a.originalPromptId,
  }))
  pieces.push({ data_type: 'text', original_value: text })
  return pieces
}

function resolvedConverterIds(entry: FreshSuffixEntry): string[] {
  const ut = entry.userTurn
  // Synthetic root-as-user-turn has no converter pipeline (root prompt's
  // params don't carry one in V1.0). Real UserTurnNodes read their pipeline.
  if (ut.kind === 'synthetic_user_turn_from_root') return []
  const pipeline = ut.params.converterPipeline
  if (!pipeline) return []
  const ids: string[] = []
  for (const ref of pipeline) {
    if (ref.converterId !== undefined) ids.push(ref.converterId)
  }
  return ids
}

function extractNewAssistantPieces(
  resp: AddMessageResponse,
  priorMax: number,
): { newPieces: BackendMessagePiece[]; newMax: number } {
  const newPieces: BackendMessagePiece[] = []
  let newMax = priorMax
  for (const msg of resp.messages.messages as BackendMessage[]) {
    if (msg.turn_number > priorMax && msg.role === 'assistant') {
      newPieces.push(...msg.pieces)
      if (msg.turn_number > newMax) newMax = msg.turn_number
    }
  }
  return { newPieces, newMax }
}

function buildExecutionRecord(args: {
  attackResultId: string
  conversationId: string
  newPieces: BackendMessagePiece[]
  hashAtExecution: string
  waveId: string
  waveTriggerKind: WaveTriggerKind
}): ExecutionRecord {
  const now = new Date().toISOString()
  return {
    executionId: cryptoRandomUuid(),
    attemptedAt: now,
    attackResultId: args.attackResultId,
    conversationId: args.conversationId,
    pieceIds: args.newPieces.map((p) => p.piece_id),
    outcome: 'success',
    resolvedInputHashAtExecution: args.hashAtExecution,
    waveId: args.waveId,
    waveTriggerKind: args.waveTriggerKind,
    dispatchedAt: now,
    targetFirstByteAt: now,
    completedAt: now,
  }
}

// Re-exported for callers that build PathPartition externally (e.g., tests
// that want to assert against the resolver's intermediate shape).
export type { PathPartition }

// jsdom and modern Node both have crypto.randomUUID. No fallback needed.
function cryptoRandomUuid(): string {
  return crypto.randomUUID()
}
