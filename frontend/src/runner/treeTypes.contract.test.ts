// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Contract tests for the tree-UI domain types and runner interfaces.
 *
 * These tests are the design-doc-to-code firewall: each `satisfies` clause
 * encodes a shape obligation from doc/gui/design/01_tree_primitives.md
 * §4–§6 / §13 (data model) and doc/gui/design/03_runner.md §2 / §6
 * (runner interfaces).
 *
 * As with the API-surface contracts in treeUi.contract.test.ts, compile-time
 * coverage runs via `npx tsc -p tsconfig.test.json`; ts-jest at run time only
 * transpiles. The runtime `expect` statements add sanity for narrowing /
 * default-value behavior.
 */

import type {
  ApiErrorReason,
  ConversationTree,
  ConversationTreeEdge,
  ConversationTreeId,
  ConversationTreeNode,
  ConversationTreeNodeBase,
  ConversationTreeNodeId,
  ConverterRef,
  CostGuardrail,
  CrossTabLockManager,
  ExecutionRecord,
  FanAxis,
  FanNode,
  FanVariant,
  ImportMessageNode,
  NodeFailureClass,
  NodeState,
  PieceSpec,
  PromptDataType,
  ReflogEntry,
  RootPromptNode,
  Runner,
  RunnerStateSink,
  ScoreNode,
  SendNode,
  UndoOp,
  UserTurnNode,
  WaveEvent,
  WaveTriggerKind,
  Workspace,
  WorkspaceSettings,
} from './treeTypes'

describe('tree-UI domain types (V1.0)', () => {
  // ------------------------------------------------------------------
  // Identifier types — branded for distinguishability without runtime overhead
  // ------------------------------------------------------------------

  describe('identifier types', () => {
    it('treats tree ids and node ids as opaque strings', () => {
      // Branded string types so a node id can't be silently passed where a
      // tree id is required (catches a class of bugs early without runtime
      // cost). The brand is type-only; values are just strings.
      const treeId = 't-1' as ConversationTreeId
      const nodeId = 'n-1' as ConversationTreeNodeId
      expect(typeof treeId).toBe('string')
      expect(typeof nodeId).toBe('string')
    })
  })

  // ------------------------------------------------------------------
  // Lifecycle — NodeState / NodeFailureClass / ApiErrorReason
  // ------------------------------------------------------------------

  describe('NodeState', () => {
    it('admits all seven lifecycle values from 01 §6.1', () => {
      const states = [
        'draft',
        'clean',
        'edited',
        'stale',
        'running',
        'failed',
        'cancelled',
      ] as const satisfies readonly NodeState[]
      expect(states).toHaveLength(7)
    })
  })

  describe('NodeFailureClass', () => {
    it('admits the four classes from 01 §6.1 / 03 §3.3a', () => {
      const classes = [
        'transient',
        'rate_limited',
        'permanent',
        'blocked',
      ] as const satisfies readonly NodeFailureClass[]
      expect(classes).toHaveLength(4)
    })
  })

  describe('ApiErrorReason', () => {
    it('carries a message + failure_class', () => {
      const reason = {
        message: 'add_message failed (500): server error — transient, retry',
        failure_class: 'transient',
      } satisfies ApiErrorReason
      expect(reason.failure_class).toBe('transient')
    })
  })

  // ------------------------------------------------------------------
  // Shared types — ConverterRef / PieceSpec / PromptDataType / ExecutionRecord / ReflogEntry
  // ------------------------------------------------------------------

  describe('shared types', () => {
    it('PromptDataType admits the five literal values', () => {
      const types = [
        'text',
        'image_path',
        'audio_path',
        'video_path',
        'binary_path',
      ] as const satisfies readonly PromptDataType[]
      expect(types).toContain('text')
    })

    it('ConverterRef can hold either a stored converter id or an inline spec', () => {
      const stored = { converterId: 'conv-1' } satisfies ConverterRef
      const inline = {
        inline: { type: 'Base64Converter', params: { encoding: 'utf-8' } },
      } satisfies ConverterRef
      expect(stored.converterId).toBe('conv-1')
      expect(inline.inline?.type).toBe('Base64Converter')
    })

    it('PieceSpec carries dataType + value (+ optional metadata)', () => {
      const piece = {
        dataType: 'text',
        value: 'hello',
        mimeType: 'text/plain',
        originalPromptId: '0c1b9c7d-0000-0000-0000-000000000001',
      } satisfies PieceSpec
      expect(piece.dataType).toBe('text')
    })

    it('ExecutionRecord carries timing triple (dispatchedAt / targetFirstByteAt / completedAt)', () => {
      // Per 01 §4.6 (rev 18 / Finding C.1): all three are required on
      // successful dispatches; nullable to cover failures that never reached
      // the target. The runner writes them inline with state transitions.
      const exec = {
        executionId: 'exec-1',
        attemptedAt: '2026-06-10T00:00:00Z',
        attackResultId: 'ar-1',
        conversationId: 'conv-1',
        pieceIds: ['p1', 'p2'],
        outcome: 'success',
        resolvedInputHashAtExecution: 'sha256:abc',
        waveId: 'w-1',
        waveTriggerKind: 'refresh_node',
        dispatchedAt: '2026-06-10T00:00:01Z',
        targetFirstByteAt: '2026-06-10T00:00:02Z',
        completedAt: '2026-06-10T00:00:03Z',
      } satisfies ExecutionRecord
      expect(exec.outcome).toBe('success')
      expect(exec.dispatchedAt).toBe('2026-06-10T00:00:01Z')
    })

    it('ReflogEntry wraps an ExecutionRecord with a per-tree pinned flag', () => {
      const entry = {
        execution: makeExec(),
        pinned: false,
      } satisfies ReflogEntry
      expect(entry.pinned).toBe(false)
    })
  })

  // ------------------------------------------------------------------
  // Node taxonomy — discriminated union by `kind`
  // ------------------------------------------------------------------

  describe('node taxonomy', () => {
    it('RootPromptNode carries text + target + optional systemPrompt', () => {
      const node = {
        ...baseFields('root-1', null),
        kind: 'root_prompt',
        params: {
          text: 'How do I bake bread?',
          attachments: [],
          targetRegistryName: 'gpt-4o',
          systemPrompt: 'You are a helpful assistant.',
        },
      } satisfies RootPromptNode
      expect(node.kind).toBe('root_prompt')
    })

    it('ImportMessageNode carries sourceConversationId + cutoffIndex', () => {
      const node = {
        ...baseFields('import-1', null),
        kind: 'import_message',
        params: {
          sourceConversationId: 'src-conv-1',
          cutoffIndex: 4,
        },
      } satisfies ImportMessageNode
      expect(node.params.cutoffIndex).toBe(4)
    })

    it('UserTurnNode carries role + text + optional converterPipeline', () => {
      const node = {
        ...baseFields('ut-1', 'root-1'),
        kind: 'user_turn',
        params: {
          role: 'user',
          text: 'Now expand on point 3',
          attachments: [],
          converterPipeline: [{ converterId: 'b64' }, { converterId: 'rot13' }],
        },
      } satisfies UserTurnNode
      expect(node.params.role).toBe('user')
      expect(node.params.converterPipeline).toHaveLength(2)
    })

    it('UserTurnNode role admits the three non-assistant values only', () => {
      // Per 01 §4.2: 'assistant' (real responses) come only from a Send,
      // never from operator input. UserTurn role union excludes it.
      const valid: UserTurnNode['params']['role'][] = ['user', 'simulated_assistant', 'system']
      expect(valid).toHaveLength(3)
    })

    it('SendNode carries optional target + converter pipeline overrides', () => {
      const node = {
        ...baseFields('s-1', 'ut-1'),
        kind: 'send',
        params: {
          targetRegistryName: 'claude-3.5-sonnet',
          converterPipeline: [],
        },
      } satisfies SendNode
      expect(node.kind).toBe('send')
    })

    it('SendNode params may be empty (target inherited from upstream root)', () => {
      const node = {
        ...baseFields('s-2', 'ut-2'),
        kind: 'send',
        params: {},
      } satisfies SendNode
      expect(node.params).toEqual({})
    })

    it('FanNode carries axis + variants + promotedChildSlotIndex + deletedSlotIndices', () => {
      const node = {
        ...baseFields('f-1', 'ut-1'),
        kind: 'fan',
        params: {
          axis: 'attempt',
          variants: [
            { axis: 'attempt', payload: {} },
            { axis: 'attempt', payload: {} },
          ],
          mode: 'each',
          promotedChildSlotIndex: null,
          deletedSlotIndices: [],
        },
      } satisfies FanNode
      expect(node.params.axis).toBe('attempt')
      expect(node.params.variants).toHaveLength(2)
    })

    it('FanAxis admits the V1.0 + V1.1 axes', () => {
      const axes = [
        'attempt',
        'converter',
        'prompt',
        'target',
        'system_prompt',
        'temperature',
      ] as const satisfies readonly FanAxis[]
      expect(axes).toContain('attempt')
      expect(axes).toContain('converter')
    })

    it('FanVariant is a discriminated union over `axis` with per-axis payload', () => {
      // attempt: empty payload
      const att: FanVariant = { axis: 'attempt', payload: {} }
      // converter: list of ConverterRef
      const cnv: FanVariant = {
        axis: 'converter',
        payload: { converters: [{ converterId: 'b64' }] },
      }
      // prompt (V1.1): text override
      const prm: FanVariant = {
        axis: 'prompt',
        payload: { text: 'alternative prompt' },
      }
      // target (V1.1): registry name override
      const tgt: FanVariant = {
        axis: 'target',
        payload: { targetRegistryName: 'claude-3.5-sonnet' },
      }
      // system_prompt (V1.1)
      const sys: FanVariant = {
        axis: 'system_prompt',
        payload: { systemPrompt: 'alt system' },
      }
      // temperature (V1.1+)
      const tmp: FanVariant = { axis: 'temperature', payload: { temperature: 0.7 } }
      expect([att, cnv, prm, tgt, sys, tmp]).toHaveLength(6)
    })

    it('ScoreNode carries scorer config (V1.0 render-only)', () => {
      const node = {
        ...baseFields('sc-1', 's-1'),
        kind: 'score',
        params: {
          scorerType: 'truthfulness',
          scorerParams: { threshold: 0.5 },
        },
      } satisfies ScoreNode
      expect(node.kind).toBe('score')
    })

    it('ConversationTreeNode discriminator narrows the union by `kind`', () => {
      // The discriminator is what makes the runner switch on node kind in
      // a type-safe way. This test proves narrowing works for each kind.
      const nodes: ConversationTreeNode[] = [
        { ...baseFields('r', null), kind: 'root_prompt', params: { text: '', attachments: [], targetRegistryName: 'gpt-4o' } },
        { ...baseFields('i', null), kind: 'import_message', params: { sourceConversationId: 'c', cutoffIndex: 0 } },
        { ...baseFields('u', 'r'), kind: 'user_turn', params: { role: 'user', text: '', attachments: [] } },
        { ...baseFields('s', 'u'), kind: 'send', params: {} },
        { ...baseFields('f', 'u'), kind: 'fan', params: { axis: 'attempt', variants: [], promotedChildSlotIndex: null, deletedSlotIndices: [] } },
        { ...baseFields('sc', 's'), kind: 'score', params: { scorerType: 'truthfulness' } },
      ]
      // For each kind, narrowing must give us access to the kind-specific params:
      const summary = nodes.map((n) => {
        switch (n.kind) {
          case 'root_prompt':
            return n.params.text
          case 'import_message':
            return n.params.sourceConversationId
          case 'user_turn':
            return n.params.role
          case 'send':
            return n.params.targetRegistryName ?? '<inherited>'
          case 'fan':
            return n.params.axis
          case 'score':
            return n.params.scorerType
        }
      })
      expect(summary).toEqual(['', 'c', 'user', '<inherited>', 'attempt', 'truthfulness'])
    })
  })

  // ------------------------------------------------------------------
  // Edge — parent/child + slotIndex (the fan-discriminator)
  // ------------------------------------------------------------------

  describe('ConversationTreeEdge', () => {
    it('carries id + parentId + childId + slotIndex', () => {
      const edge = {
        id: 'edge-1',
        parentId: 'r' as ConversationTreeNodeId,
        childId: 'c' as ConversationTreeNodeId,
        slotIndex: 0,
      } satisfies ConversationTreeEdge
      expect(edge.slotIndex).toBe(0)
    })
  })

  // ------------------------------------------------------------------
  // ConversationTree — the top-level container
  // ------------------------------------------------------------------

  describe('ConversationTree', () => {
    it('carries nodes + edges + rootId + lifecycle fields', () => {
      const tree = {
        id: 't-1' as ConversationTreeId,
        nodes: [],
        edges: [],
        rootId: 'r' as ConversationTreeNodeId,
        displayName: 'My exploration',
        createdAt: '2026-06-10T00:00:00Z',
        parentConversationTreeId: null,
        parentSourceConversationId: null,
        undoStack: [],
      } satisfies ConversationTree
      expect(tree.id).toBe('t-1')
      expect(tree.parentConversationTreeId).toBeNull()
    })

    it('parentConversationTreeId carries a tree id when set via branchToNewTree', () => {
      const tree = {
        id: 't-clone' as ConversationTreeId,
        nodes: [],
        edges: [],
        rootId: 'r' as ConversationTreeNodeId,
        displayName: 'Clone of My exploration',
        createdAt: '2026-06-10T00:00:00Z',
        parentConversationTreeId: 't-1' as ConversationTreeId,
        parentSourceConversationId: null,
        undoStack: [],
      } satisfies ConversationTree
      expect(tree.parentConversationTreeId).toBe('t-1')
    })
  })

  // ------------------------------------------------------------------
  // Workspace — V1.0 minimal shape
  // ------------------------------------------------------------------

  describe('Workspace (V1.0 minimal)', () => {
    it('carries currentTree + recentTreeIds + settings', () => {
      const ws = {
        currentTree: null,
        recentTreeIds: [],
        settings: {
          reflogCapPerNode: 50,
          confirmThresholdCount: 20,
          suppressConfirmModalThisSession: false,
        } satisfies WorkspaceSettings,
      } satisfies Workspace
      expect(ws.settings.reflogCapPerNode).toBe(50)
    })
  })

  // ------------------------------------------------------------------
  // UndoOp — discriminated union per 01 §6.9
  // ------------------------------------------------------------------

  describe('UndoOp', () => {
    it('admits all five variant kinds with their state-snapshot widening', () => {
      const ops: UndoOp[] = [
        {
          kind: 'add',
          nodeId: 'n1' as ConversationTreeNodeId,
          autoInsertedChildIds: ['n2' as ConversationTreeNodeId],
        },
        {
          kind: 'delete',
          subtreeSnapshot: [],
          edgesSnapshot: [],
          parentId: 'n0' as ConversationTreeNodeId,
        },
        {
          kind: 'editParams',
          nodeId: 'n1' as ConversationTreeNodeId,
          oldParams: { text: 'old', attachments: [], role: 'user' },
          priorState: 'clean',
          priorDescendantStates: new Map(),
        },
        {
          kind: 'regenerateFanChildren',
          fanNodeId: 'f' as ConversationTreeNodeId,
          oldChildren: [],
          oldChildEdges: [],
        },
        {
          kind: 'makeCurrent',
          nodeId: 'n1' as ConversationTreeNodeId,
          priorExecution: null, // null is a valid prior per 01 §6.7 step 0
          promotedExecution: makeExec(),
          priorDescendantStates: new Map(),
          priorDescendantExecutions: new Map(),
        },
      ]
      expect(ops).toHaveLength(5)
    })
  })

  // ------------------------------------------------------------------
  // Wave bookkeeping — WaveTriggerKind / WaveEvent (03 §6)
  // ------------------------------------------------------------------

  describe('WaveTriggerKind', () => {
    it('admits the four V1.0 kinds plus the V1.1/V2 markers', () => {
      const kinds = [
        'refresh_node',
        'refresh_subtree',
        'refresh_tree',
        'retry_failed',
        'synced_peer_add', // V1.1
        'cross_tree_rebase', // V2.1+
      ] as const satisfies readonly WaveTriggerKind[]
      expect(kinds).toContain('refresh_node')
      expect(kinds).toContain('retry_failed')
    })
  })

  describe('WaveEvent', () => {
    it("discriminates the 'start' event with treeId + triggerKind + estimatedCalls", () => {
      const ev = {
        kind: 'start',
        waveId: 'w-1',
        triggerKind: 'refresh_tree',
        estimatedCalls: 60,
        treeId: 't-1' as ConversationTreeId,
        emittedAt: '2026-06-10T00:00:00Z',
      } satisfies WaveEvent
      expect(ev.kind).toBe('start')
    })

    it("discriminates the 'node_complete' event", () => {
      const ev = {
        kind: 'node_complete',
        waveId: 'w-1',
        nodeId: 'n-1' as ConversationTreeNodeId,
        outcome: 'success',
        emittedAt: '2026-06-10T00:00:01Z',
      } satisfies WaveEvent
      expect(ev.outcome).toBe('success')
    })

    it("discriminates the 'complete' event with bucketed failure summary", () => {
      // Per 03 §6.3 (rev 16 / Findings 2 + 3): failed is bucketed by class
      // (transient / rate_limited / permanent); blocked is in-flight-cascade
      // victims (state=stale, failure_class='blocked'); cancelled is operator
      // wave-abort; reflog_evicted rolls up wave-time evictions.
      const ev = {
        kind: 'complete',
        waveId: 'w-1',
        emittedAt: '2026-06-10T00:00:05Z',
        summary: {
          succeeded: 57,
          failed: { transient: 2, rate_limited: 1, permanent: 0 },
          blocked: 0,
          cancelled: 0,
          reflog_evicted: 3,
        },
      } satisfies WaveEvent
      if (ev.kind === 'complete') {
        expect(ev.summary.succeeded).toBe(57)
        expect(ev.summary.failed.transient).toBe(2)
      }
    })

    it("discriminates the 'busy' / 'queued' / 'reflog_eviction' / 'operator_tag_required' events", () => {
      const busy = {
        kind: 'busy',
        treeId: 't-1' as ConversationTreeId,
        holderTabId: 'tab-other',
        emittedAt: '2026-06-10T00:00:00Z',
      } satisfies WaveEvent
      const queued = {
        kind: 'queued',
        waveId: 'w-2',
        treeId: 't-1' as ConversationTreeId,
        queueDepth: 1,
        emittedAt: '2026-06-10T00:00:00Z',
      } satisfies WaveEvent
      const evict = {
        kind: 'reflog_eviction',
        treeId: 't-1' as ConversationTreeId,
        nodeId: 'n-1' as ConversationTreeNodeId,
        evictedExecutionId: 'exec-old',
        preview: 'How do I...',
        emittedAt: '2026-06-10T00:00:00Z',
      } satisfies WaveEvent
      const tagReq = {
        kind: 'operator_tag_required',
        treeId: 't-1' as ConversationTreeId,
        emittedAt: '2026-06-10T00:00:00Z',
      } satisfies WaveEvent
      expect([busy, queued, evict, tagReq]).toHaveLength(4)
    })
  })

  // ------------------------------------------------------------------
  // Runner interfaces (03 §2.1, §2.2, §2.3, §10.4) — checked structurally
  // ------------------------------------------------------------------

  describe('Runner interface', () => {
    it('exposes refreshNode / refreshSubtree / refreshTree / cancelWave / cancelQueued / retryFailedNodes', () => {
      const stub: Runner = {
        refreshNode: async () => undefined,
        refreshSubtree: async () => undefined,
        refreshTree: async () => undefined,
        cancelWave: async () => undefined,
        cancelQueued: async () => undefined,
        retryFailedNodes: async () => undefined,
      }
      expect(typeof stub.refreshNode).toBe('function')
      expect(typeof stub.retryFailedNodes).toBe('function')
    })
  })

  describe('RunnerStateSink interface', () => {
    it('exposes setNodeState / recordExecution / clearExecution / setReflogPinned / emitWaveEvent', () => {
      const stub: RunnerStateSink = {
        setNodeState: () => undefined,
        recordExecution: () => undefined,
        clearExecution: () => undefined,
        setReflogPinned: () => undefined,
        emitWaveEvent: () => undefined,
      }
      expect(typeof stub.setNodeState).toBe('function')
    })

    it('setNodeState accepts the three opts.reason shapes (string / ApiErrorReason / null)', () => {
      // Per 03 §2.2 sink reason semantics:
      // - string → normalized to { message, failure_class: 'transient' }
      // - ApiErrorReason → written directly to node.lastError
      // - null → clears node.lastError
      const sink: RunnerStateSink = {
        setNodeState: () => undefined,
        recordExecution: () => undefined,
        clearExecution: () => undefined,
        setReflogPinned: () => undefined,
        emitWaveEvent: () => undefined,
      }
      sink.setNodeState('t' as ConversationTreeId, 'n' as ConversationTreeNodeId, 'failed', {
        reason: 'string form',
      })
      sink.setNodeState('t' as ConversationTreeId, 'n' as ConversationTreeNodeId, 'failed', {
        reason: { message: 'structured', failure_class: 'permanent' },
      })
      sink.setNodeState('t' as ConversationTreeId, 'n' as ConversationTreeNodeId, 'stale', {
        reason: null,
      })
      sink.setNodeState('t' as ConversationTreeId, 'n' as ConversationTreeNodeId, 'running')
    })
  })

  describe('CostGuardrail interface', () => {
    it('exposes approve returning a Promise<boolean>', () => {
      const stub: CostGuardrail = {
        approve: async () => true,
      }
      expect(typeof stub.approve).toBe('function')
    })
  })

  describe('CrossTabLockManager interface', () => {
    it('exposes acquire (Promise<acquired | busy>) and release', () => {
      const stub: CrossTabLockManager = {
        acquire: async () => 'acquired',
        release: () => undefined,
      }
      expect(typeof stub.acquire).toBe('function')
    })
  })
})

// --------------------------------------------------------------------
// Test helpers (private to this file)
// --------------------------------------------------------------------

function baseFields(
  id: string,
  parentId: string | null,
): Pick<
  ConversationTreeNodeBase,
  | 'id'
  | 'parentId'
  | 'resolvedInputHash'
  | 'state'
  | 'execution'
  | 'executionHistory'
  | 'lastError'
  | 'labels'
  | 'createdAt'
  | 'updatedAt'
  | 'version'
> {
  return {
    id: id as ConversationTreeNodeId,
    parentId: parentId === null ? null : (parentId as ConversationTreeNodeId),
    resolvedInputHash: 'sha256:00',
    state: 'draft',
    execution: null,
    executionHistory: [],
    lastError: null,
    labels: {},
    createdAt: '2026-06-10T00:00:00Z',
    updatedAt: '2026-06-10T00:00:00Z',
    version: 1,
  }
}

function makeExec(): ExecutionRecord {
  return {
    executionId: 'exec-1',
    attemptedAt: '2026-06-10T00:00:00Z',
    attackResultId: 'ar-1',
    conversationId: 'conv-1',
    pieceIds: [],
    outcome: 'success',
    resolvedInputHashAtExecution: 'sha256:abc',
    waveId: 'w-1',
    waveTriggerKind: 'refresh_node',
    dispatchedAt: '2026-06-10T00:00:00Z',
    targetFirstByteAt: '2026-06-10T00:00:00Z',
    completedAt: '2026-06-10T00:00:01Z',
  }
}
