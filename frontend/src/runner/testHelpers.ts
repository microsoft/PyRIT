// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Shared test helpers for the tree-UI runner test suite.
 *
 * Intentionally low-magic: a small set of builder functions for nodes / edges /
 * trees, plus a recording mock implementation of `RunnerStateSink`. Tests stay
 * readable by composing these directly rather than reaching for fixture files.
 *
 * The helpers fill in the boilerplate fields every node carries (timestamps,
 * empty execution history, default state) so tests can name only the fields
 * they care about for the property under test.
 */

import type {
  ApiErrorReason,
  ConversationTree,
  ConversationTreeEdge,
  ConversationTreeId,
  ConversationTreeNode,
  ConversationTreeNodeId,
  ExecutionRecord,
  FanNode,
  ImportMessageNode,
  NodeState,
  RootPromptNode,
  RunnerStateSink,
  ScoreNode,
  SendNode,
  UndoOp,
  UserTurnNode,
  WaveEvent,
} from './treeTypes'

// ----------------------------------------------------------------------------
// Branded id casts (the brand exists only at the type level; values are strings)
// ----------------------------------------------------------------------------

export const treeId = (s: string): ConversationTreeId => s as ConversationTreeId
export const nodeId = (s: string): ConversationTreeNodeId => s as ConversationTreeNodeId

// ----------------------------------------------------------------------------
// Node builders (one per kind). Each fills boilerplate fields with sensible
// defaults so tests can override only what they care about.
// ----------------------------------------------------------------------------

const ISO_FIXED = '2026-06-10T00:00:00.000Z'

interface BaseOverrides {
  state?: NodeState
  execution?: ExecutionRecord | null
  resolvedInputHash?: string
  lastError?: ApiErrorReason | null
}

function base(id: string, parentId: string | null, overrides: BaseOverrides = {}) {
  return {
    id: nodeId(id),
    parentId: parentId === null ? null : nodeId(parentId),
    resolvedInputHash: overrides.resolvedInputHash ?? `sha256:${id}`,
    state: overrides.state ?? ('clean' as NodeState),
    execution: overrides.execution ?? null,
    executionHistory: [] as ConversationTreeNode['executionHistory'],
    lastError: overrides.lastError ?? null,
    labels: {} as Record<string, string>,
    createdAt: ISO_FIXED,
    updatedAt: ISO_FIXED,
    version: 1,
  }
}

export function mkRoot(
  id: string,
  params?: Partial<RootPromptNode['params']>,
  overrides: BaseOverrides = {},
): RootPromptNode {
  return {
    ...base(id, null, overrides),
    kind: 'root_prompt',
    params: {
      text: params?.text ?? 'root prompt',
      attachments: params?.attachments ?? [],
      systemPrompt: params?.systemPrompt,
      targetRegistryName: params?.targetRegistryName ?? 'gpt-4o',
    },
  }
}

export function mkImport(
  id: string,
  params?: Partial<ImportMessageNode['params']>,
  overrides: BaseOverrides = {},
): ImportMessageNode {
  return {
    ...base(id, null, overrides),
    kind: 'import_message',
    params: {
      sourceConversationId: params?.sourceConversationId ?? 'src-conv-1',
      cutoffIndex: params?.cutoffIndex ?? 0,
    },
  }
}

export function mkUserTurn(
  id: string,
  parentId: string,
  params?: Partial<UserTurnNode['params']>,
  overrides: BaseOverrides = {},
): UserTurnNode {
  return {
    ...base(id, parentId, overrides),
    kind: 'user_turn',
    params: {
      role: params?.role ?? 'user',
      text: params?.text ?? `text ${id}`,
      attachments: params?.attachments ?? [],
      converterPipeline: params?.converterPipeline,
    },
  }
}

export function mkSend(
  id: string,
  parentId: string,
  params?: Partial<SendNode['params']>,
  overrides: BaseOverrides = {},
): SendNode {
  return {
    ...base(id, parentId, overrides),
    kind: 'send',
    params: {
      targetRegistryName: params?.targetRegistryName,
      converterPipeline: params?.converterPipeline,
    },
  }
}

export function mkFan(
  id: string,
  parentId: string,
  params?: Partial<FanNode['params']>,
  overrides: BaseOverrides = {},
): FanNode {
  return {
    ...base(id, parentId, overrides),
    kind: 'fan',
    params: {
      axis: params?.axis ?? 'attempt',
      variants: params?.variants ?? [],
      mode: params?.mode,
      promotedChildSlotIndex: params?.promotedChildSlotIndex ?? null,
      deletedSlotIndices: params?.deletedSlotIndices ?? [],
    },
  }
}

export function mkScore(
  id: string,
  parentId: string,
  params?: Partial<ScoreNode['params']>,
  overrides: BaseOverrides = {},
): ScoreNode {
  return {
    ...base(id, parentId, overrides),
    kind: 'score',
    params: {
      scorerType: params?.scorerType ?? 'truthfulness',
      scorerParams: params?.scorerParams,
    },
  }
}

// ----------------------------------------------------------------------------
// Edge builder
// ----------------------------------------------------------------------------

export function mkEdge(parentId: string, childId: string, slotIndex = 0): ConversationTreeEdge {
  return {
    id: `e-${parentId}-${childId}-${slotIndex}`,
    parentId: nodeId(parentId),
    childId: nodeId(childId),
    slotIndex,
  }
}

// ----------------------------------------------------------------------------
// Tree builder. Derives edges from `parentId` if not supplied explicitly.
// ----------------------------------------------------------------------------

interface TreeOverrides {
  id?: string
  displayName?: string
  parentConversationTreeId?: string | null
  parentSourceConversationId?: string | null
  undoStack?: UndoOp[]
  edges?: ConversationTreeEdge[]
}

export function mkTree(rootId: string, nodes: ConversationTreeNode[], overrides: TreeOverrides = {}): ConversationTree {
  // Default edges: one per child node. Children of FanNode parents are
  // auto-numbered by ordinal so attempt-fan tests get distinct slotIndices
  // (slotIndex feeds the resolved-input hash; sharing it across siblings
  // makes fixtures lie about the tree's identity rule).
  const fanCounters = new Map<string, number>()
  const isFanParent = new Set(nodes.filter((n) => n.kind === 'fan').map((n) => n.id as string))
  const derivedEdges: ConversationTreeEdge[] =
    overrides.edges ??
    nodes
      .filter((n) => n.parentId !== null)
      .map((n) => {
        const parent = n.parentId as string
        if (isFanParent.has(parent)) {
          const next = fanCounters.get(parent) ?? 0
          fanCounters.set(parent, next + 1)
          return mkEdge(parent, n.id as string, next)
        }
        return mkEdge(parent, n.id as string)
      })
  return {
    id: treeId(overrides.id ?? 't-1'),
    nodes,
    edges: derivedEdges,
    rootId: nodeId(rootId),
    displayName: overrides.displayName ?? 'Test tree',
    createdAt: ISO_FIXED,
    parentConversationTreeId:
      overrides.parentConversationTreeId == null
        ? null
        : treeId(overrides.parentConversationTreeId),
    parentSourceConversationId: overrides.parentSourceConversationId ?? null,
    undoStack: overrides.undoStack ?? [],
  }
}

/**
 * Locate the rendered card wrapper for a given node id. Pinned to the
 * `[data-tree-node-id="..."][data-selected]` selector that CardFrame
 * always emits — the `[data-selected]` clause filters out the rail's
 * own data-tree-node-id (rails carry it for DOM scoping but lack
 * data-selected).
 */
export function findCard(container: HTMLElement, id: string): HTMLElement {
  const el = container.querySelector(`[data-tree-node-id="${id}"][data-selected]`)
  if (el === null) throw new Error(`findCard: no card with data-tree-node-id="${id}"`)
  return el as HTMLElement
}

// ----------------------------------------------------------------------------
// Mock ExecutionRecord
// ----------------------------------------------------------------------------

export function mkExecution(overrides: Partial<ExecutionRecord> = {}): ExecutionRecord {
  // Spread-merge rather than per-field `??` so an explicit `null` override
  // (e.g. dispatchedAt: null for a pre-target-call failure) survives.
  return {
    executionId: 'exec-1',
    attemptedAt: ISO_FIXED,
    attackResultId: 'ar-1',
    conversationId: 'conv-1',
    pieceIds: [],
    outcome: 'success',
    resolvedInputHashAtExecution: 'sha256:00',
    waveId: 'w-1',
    waveTriggerKind: 'refresh_node',
    dispatchedAt: ISO_FIXED,
    targetFirstByteAt: ISO_FIXED,
    completedAt: ISO_FIXED,
    ...overrides,
  }
}

// ----------------------------------------------------------------------------
// Recording mock `RunnerStateSink`
// ----------------------------------------------------------------------------

export type SinkCall =
  | {
      method: 'setNodeState'
      treeId: ConversationTreeId
      nodeId: ConversationTreeNodeId
      state: NodeState
      reason?: string | ApiErrorReason | null
    }
  | {
      method: 'recordExecution'
      treeId: ConversationTreeId
      nodeId: ConversationTreeNodeId
      execution: ExecutionRecord
    }
  | {
      method: 'clearExecution'
      treeId: ConversationTreeId
      nodeId: ConversationTreeNodeId
    }
  | {
      method: 'setReflogPinned'
      treeId: ConversationTreeId
      nodeId: ConversationTreeNodeId
      executionId: string
      pinned: boolean
    }
  | {
      method: 'emitWaveEvent'
      event: WaveEvent
    }

export interface MockSink {
  sink: RunnerStateSink
  calls: SinkCall[]
  callsOf<M extends SinkCall['method']>(method: M): Extract<SinkCall, { method: M }>[]
}

export function mkMockSink(): MockSink {
  const calls: SinkCall[] = []
  const sink: RunnerStateSink = {
    setNodeState: (treeId, nodeId, state, opts) => {
      calls.push({ method: 'setNodeState', treeId, nodeId, state, reason: opts?.reason })
    },
    recordExecution: (treeId, nodeId, execution) => {
      calls.push({ method: 'recordExecution', treeId, nodeId, execution })
    },
    clearExecution: (treeId, nodeId) => {
      calls.push({ method: 'clearExecution', treeId, nodeId })
    },
    setReflogPinned: (treeId, nodeId, executionId, pinned) => {
      calls.push({ method: 'setReflogPinned', treeId, nodeId, executionId, pinned })
    },
    emitWaveEvent: (event) => {
      calls.push({ method: 'emitWaveEvent', event })
    },
  }
  return {
    sink,
    calls,
    callsOf: <M extends SinkCall['method']>(method: M) =>
      calls.filter((c): c is Extract<SinkCall, { method: M }> => c.method === method),
  }
}
