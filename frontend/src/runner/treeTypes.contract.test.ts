// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Type-shape contract tests for the tree-UI domain types.
 *
 * Each test uses TypeScript `satisfies` clauses (or `switch`-on-discriminator
 * narrowing exercised at runtime) to enforce a shape obligation that a future
 * refactor could break silently. The runtime `expect` calls are deliberately
 * minimal — `satisfies` does the real work; runtime sanity is here only where
 * it adds value (discriminator narrowing actually exercising the union at
 * runtime, default-value behavior on optional fields whose null-vs-absent
 * distinction matters).
 *
 * Compile-time coverage runs via `npx tsc -p tsconfig.contract.json` (CI gate);
 * ts-jest at runtime only transpiles, so the type assertions would otherwise
 * be unenforced.
 */

import type {
  ConversationTree,
  ConversationTreeNode,
  ConversationTreeNodeBase,
  ConversationTreeNodeId,
  ConverterRef,
  CostGuardrail,
  CrossTabLockManager,
  ExecutionRecord,
  FanVariant,
  PieceSpec,
  ReflogEntry,
  Runner,
  RunnerStateSink,
  UndoOp,
  WaveEvent,
  WaveTriggerKind,
  Workspace,
} from './treeTypes'
import { mkExecution, nodeId, treeId } from './testHelpers'

describe('treeTypes — type-level contracts', () => {
  // ------------------------------------------------------------------
  // The high-value tests: discriminated unions narrow correctly.
  // A future refactor that breaks discriminator narrowing breaks the
  // runner's switch-on-kind, the wave-event dispatch, and undo handling.
  // ------------------------------------------------------------------

  it('ConversationTreeNode kind discriminator narrows to per-kind params', () => {
    const nodes: ConversationTreeNode[] = [
      mkRoot(),
      mkImport(),
      mkUserTurn(),
      mkSend(),
      mkFan(),
      mkScore(),
    ]
    const params = nodes.map((n) => {
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
    expect(params).toEqual(['hi', 'src', 'user', '<inherited>', 'attempt', 'truthfulness'])
  })

  it('FanVariant axis discriminator narrows to per-axis payload', () => {
    const variants: FanVariant[] = [
      { axis: 'attempt', payload: {} },
      { axis: 'converter', payload: { converters: [{ converterId: 'b64' }] } },
      { axis: 'prompt', payload: { text: 'alt' } },
      { axis: 'target', payload: { targetRegistryName: 'gpt' } },
      { axis: 'system_prompt', payload: { systemPrompt: 'sys' } },
      { axis: 'temperature', payload: { temperature: 0.7 } },
    ]
    const summaries = variants.map((v) => {
      switch (v.axis) {
        case 'attempt':
          return Object.keys(v.payload).length
        case 'converter':
          return v.payload.converters.length
        case 'prompt':
          return v.payload.text
        case 'target':
          return v.payload.targetRegistryName
        case 'system_prompt':
          return v.payload.systemPrompt
        case 'temperature':
          return v.payload.temperature
      }
    })
    expect(summaries).toEqual([0, 1, 'alt', 'gpt', 'sys', 0.7])
  })

  it('WaveEvent kind discriminator narrows to per-kind payload', () => {
    const events: WaveEvent[] = [
      {
        kind: 'start',
        waveId: 'w',
        triggerKind: 'refresh_tree',
        estimatedCalls: 1,
        treeId: treeId('t'),
        emittedAt: 'now',
      },
      {
        kind: 'node_complete',
        waveId: 'w',
        nodeId: nodeId('n'),
        outcome: 'success',
        emittedAt: 'now',
      },
      {
        kind: 'complete',
        waveId: 'w',
        emittedAt: 'now',
        summary: {
          succeeded: 1,
          failed: { transient: 0, rate_limited: 0, permanent: 0 },
          blocked: 0,
          cancelled: 0,
          reflog_evicted: 0,
        },
      },
      { kind: 'busy', treeId: treeId('t'), holderTabId: 'tab2', emittedAt: 'now' },
      { kind: 'queued', waveId: 'w', treeId: treeId('t'), queueDepth: 1, emittedAt: 'now' },
      {
        kind: 'reflog_eviction',
        treeId: treeId('t'),
        nodeId: nodeId('n'),
        evictedExecutionId: 'e',
        preview: 'p',
        emittedAt: 'now',
      },
      { kind: 'operator_tag_required', treeId: treeId('t'), emittedAt: 'now' },
    ]
    expect(events.map((e) => e.kind)).toEqual([
      'start',
      'node_complete',
      'complete',
      'busy',
      'queued',
      'reflog_eviction',
      'operator_tag_required',
    ])
  })

  it('UndoOp kind discriminator narrows to per-kind snapshot fields', () => {
    // editParams and makeCurrent carry state-snapshot widening — a refactor
    // that drops a snapshot field reverts undo to silently leaving descendants
    // stale. Asserting the discriminator pins each variant's shape.
    const ops: UndoOp[] = [
      { kind: 'add', nodeId: nodeId('n'), autoInsertedChildIds: [nodeId('c')] },
      { kind: 'delete', subtreeSnapshot: [], edgesSnapshot: [], parentId: nodeId('p') },
      {
        kind: 'editParams',
        nodeId: nodeId('n'),
        oldParams: { text: 'old', attachments: [], role: 'user' },
        priorState: 'clean',
        priorDescendantStates: new Map(),
      },
      {
        kind: 'regenerateFanChildren',
        fanNodeId: nodeId('f'),
        oldChildren: [],
        oldChildEdges: [],
      },
      {
        kind: 'makeCurrent',
        nodeId: nodeId('n'),
        priorExecution: null, // failed-node makeCurrent path: null prior is valid
        promotedExecution: mkExecution(),
        priorDescendantStates: new Map(),
        priorDescendantExecutions: new Map(),
      },
    ]
    expect(ops.map((o) => o.kind)).toEqual([
      'add',
      'delete',
      'editParams',
      'regenerateFanChildren',
      'makeCurrent',
    ])
  })

  // ------------------------------------------------------------------
  // Default-value contracts that the runner depends on at runtime.
  // ------------------------------------------------------------------

  it('SendNode.params permits an empty object (target inherited from upstream root)', () => {
    // If params ever became required-target, leaves under a single-target
    // tree would force operators to re-state the target on every Send.
    const node = mkSend({ params: {} })
    expect(node.params.targetRegistryName).toBeUndefined()
  })

  it('FanNode.promotedChildSlotIndex distinguishes "no Pick" (null) from "Pick slot 0"', () => {
    // The UI dims non-promoted children when `!== null`. Making the field
    // optional-number (instead of nullable-number) would silently collapse
    // "Pick slot 0" into "no Pick" via `undefined`.
    const pickedFirstSlot = mkFan({ params: { promotedChildSlotIndex: 0 } })
    const noPick = mkFan({ params: { promotedChildSlotIndex: null } })
    expect(pickedFirstSlot.params.promotedChildSlotIndex).toBe(0)
    expect(noPick.params.promotedChildSlotIndex).toBeNull()
  })

  it('ExecutionRecord timing triple admits null per-field (failure paths without target call)', () => {
    // Pre-target-call failures have nothing to time; the latency drawer reads
    // null as "no data" rather than computing "0ms" against a sentinel.
    const noTargetCall: ExecutionRecord = mkExecution({
      outcome: 'failure',
      dispatchedAt: null,
      targetFirstByteAt: null,
      completedAt: '2026-06-10T00:00:00Z',
    })
    expect(noTargetCall.dispatchedAt).toBeNull()
    expect(noTargetCall.completedAt).not.toBeNull()
  })

  // ------------------------------------------------------------------
  // Interface shape sanity: each interface accepts a minimal stub. Catches
  // accidental field-rename refactors that silently break the interface
  // contract for consumers.
  // ------------------------------------------------------------------

  it('Runner interface accepts a stub of all six entry points', () => {
    const stub: Runner = {
      refreshNode: async () => undefined,
      refreshSubtree: async () => undefined,
      refreshTree: async () => undefined,
      cancelWave: async () => undefined,
      cancelQueued: async () => undefined,
      retryFailedNodes: async () => undefined,
    }
    expect(Object.keys(stub).sort()).toEqual(
      [
        'cancelQueued',
        'cancelWave',
        'refreshNode',
        'refreshSubtree',
        'refreshTree',
        'retryFailedNodes',
      ].sort(),
    )
  })

  it('RunnerStateSink accepts the three reason shapes (string / ApiErrorReason / null)', () => {
    // The three shapes are load-bearing: string normalizes to transient
    // (for legacy non-API failures); ApiErrorReason carries the runner's
    // classification; null clears lastError for the retry-failed demotion.
    // Each call site below must compile; that's the contract.
    const sink: RunnerStateSink = {
      setNodeState: () => undefined,
      recordExecution: () => undefined,
      clearExecution: () => undefined,
      setReflogPinned: () => undefined,
      emitWaveEvent: () => undefined,
    }
    const t = treeId('t')
    const n = nodeId('n')
    sink.setNodeState(t, n, 'failed', { reason: 'string form' })
    sink.setNodeState(t, n, 'failed', { reason: { message: 'm', failure_class: 'permanent' } })
    sink.setNodeState(t, n, 'stale', { reason: null })
    sink.setNodeState(t, n, 'running')
    expect(typeof sink.setNodeState).toBe('function')
  })

  it('CostGuardrail + CrossTabLockManager stubs satisfy the interfaces', () => {
    const guardrail: CostGuardrail = { approve: async () => true }
    const lock: CrossTabLockManager = {
      acquire: async () => ({ acquired: true, holderTabId: null }),
      release: () => undefined,
    }
    expect([typeof guardrail.approve, typeof lock.acquire, typeof lock.release]).toEqual([
      'function',
      'function',
      'function',
    ])
  })

  // ------------------------------------------------------------------
  // Forward-compat: V1.1+ markers stay in the type union so V1.1
  // enablement is a code change, not a type ripple through every site.
  // ------------------------------------------------------------------

  it('WaveTriggerKind admits the V1.1+ markers (synced_peer_add, cross_tree_rebase)', () => {
    const kinds: WaveTriggerKind[] = [
      'refresh_node',
      'refresh_subtree',
      'refresh_tree',
      'retry_failed',
      'synced_peer_add',
      'cross_tree_rebase',
    ]
    expect(kinds).toHaveLength(6)
  })
})

// ----------------------------------------------------------------------------
// Type-only assertions (compile-time only; runtime no-ops). Each catches a
// structural drift the runtime tests above don't.
// ----------------------------------------------------------------------------

type _AssertConversationTreeUsesBrandedIds = ConversationTree extends {
  id: infer I
  rootId: infer R
}
  ? I & R extends string
    ? true
    : never
  : never
const _branded: _AssertConversationTreeUsesBrandedIds = true

type _AssertReflogEntryWrapsExecution = ReflogEntry extends {
  execution: ExecutionRecord
  pinned: boolean
}
  ? true
  : never
const _reflog: _AssertReflogEntryWrapsExecution = true

type _AssertConverterRefAcceptsEitherShape = ConverterRef extends
  | { converterId?: string }
  | { inline?: object }
  ? true
  : never
const _conv: _AssertConverterRefAcceptsEitherShape = true

type _AssertPieceSpecCarriesDataType = PieceSpec extends { dataType: string; value: string }
  ? true
  : never
const _piece: _AssertPieceSpecCarriesDataType = true

type _AssertWorkspaceShape = Workspace extends {
  currentTree: ConversationTree | null
  recentTreeIds: ReadonlyArray<unknown>
  settings: object
}
  ? true
  : never
const _ws: _AssertWorkspaceShape = true

// Suppress unused-local warnings; the values exist only to anchor the type assertions.
void _branded
void _reflog
void _conv
void _piece
void _ws
void (null as ConversationTreeNodeId | null)

// ----------------------------------------------------------------------------
// Local fixture helpers — minimal partial fixtures focused on the params
// shape under test. Distinct from testHelpers.ts's `mk*` builders, which
// produce full ConversationTreeNodes with default state / executionHistory /
// resolved hashes that the type-shape tests don't care about.
// ----------------------------------------------------------------------------

const ISO = '2026-06-10T00:00:00.000Z'

function base(): Pick<
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
    id: nodeId('x'),
    parentId: null,
    resolvedInputHash: 'sha256:0',
    state: 'draft',
    execution: null,
    executionHistory: [],
    lastError: null,
    labels: {},
    createdAt: ISO,
    updatedAt: ISO,
    version: 1,
  }
}

function mkRoot(): Extract<ConversationTreeNode, { kind: 'root_prompt' }> {
  return {
    ...base(),
    kind: 'root_prompt',
    params: { text: 'hi', attachments: [], targetRegistryName: 'gpt-4o' },
  }
}

function mkImport(): Extract<ConversationTreeNode, { kind: 'import_message' }> {
  return {
    ...base(),
    kind: 'import_message',
    params: { sourceConversationId: 'src', cutoffIndex: 0 },
  }
}

function mkUserTurn(): Extract<ConversationTreeNode, { kind: 'user_turn' }> {
  return {
    ...base(),
    kind: 'user_turn',
    params: { role: 'user', text: 't', attachments: [] },
  }
}

function mkSend(
  overrides?: { params?: Partial<Extract<ConversationTreeNode, { kind: 'send' }>['params']> },
): Extract<ConversationTreeNode, { kind: 'send' }> {
  return {
    ...base(),
    kind: 'send',
    params: { ...overrides?.params },
  }
}

function mkFan(
  overrides?: { params?: Partial<Extract<ConversationTreeNode, { kind: 'fan' }>['params']> },
): Extract<ConversationTreeNode, { kind: 'fan' }> {
  return {
    ...base(),
    kind: 'fan',
    params: {
      axis: 'attempt',
      variants: [],
      promotedChildSlotIndex: null,
      deletedSlotIndices: [],
      ...overrides?.params,
    },
  }
}

function mkScore(): Extract<ConversationTreeNode, { kind: 'score' }> {
  return {
    ...base(),
    kind: 'score',
    params: { scorerType: 'truthfulness' },
  }
}
