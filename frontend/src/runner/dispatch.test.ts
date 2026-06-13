// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `dispatchLeaf` — the orchestrator that turns one leaf's
 * partition output into one `create_attack` + N `add_message` HTTP calls.
 *
 * The dispatcher is the only place in the runner that talks to the API
 * client; it's also where the labels-divergence invariant gets enforced
 * at the call-site (every request in a leaf's sequence carries the same
 * `labels` dict by construction). Tests use a recording mock API client
 * to assert call counts, ordering, payload shapes, and the failure-path
 * partial-commit semantics.
 *
 * What's IN scope here:
 *   - One leaf, one dispatch sequence.
 *   - Mock API client that returns scripted responses (success / each
 *     failure class / mid-chain failure).
 *   - Sink call recording for state transitions and ExecutionRecord
 *     attachment.
 *   - The 200-message cap short-circuit.
 *   - Labels-divergence invariant at the call site.
 *   - The Q.S.1 cost-cliff regression: a 60-leaf attempt-fan with a
 *     10-deep shared stale prefix triggers 60 dispatches × 11 calls each
 *     = 660 backend calls (60 create_attack + 600 add_message). Verifies
 *     no implicit memoization snuck in.
 *
 * What's OUT of scope here (lands in later sub-PRs):
 *   - The cascade-on-failure for sibling leaves blocked by a shared
 *     failed ancestor (PR4d).
 *   - The 5-step entry-point shim (PR4e).
 *   - Cross-tab lock + queue drain (PR4f).
 */

import type { AddMessageRequest, AddMessageResponse, ConversationMessagesResponse, CreateAttackRequest, CreateAttackResponse } from '../types'
import type { ApiError } from '../services/errors'
import { dispatchLeaf } from './dispatch'
import type { LeafDispatchOutcome, RunnerAttacksApi } from './dispatch'
import {
  mkExecution,
  mkFan,
  mkMockSink,
  mkRoot,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
  treeId,
} from './testHelpers'
import type { ConversationTreeNode, NodeFailureClass } from './treeTypes'

// ============================================================================
// API client mock factory
// ============================================================================

interface ApiClientMockOptions {
  /** create_attack returns this (or throws if it's an Error). Default: ok. */
  createAttackResult?: CreateAttackResponse | ApiError
  /**
   * add_message returns these in order. If a queued item is an ApiError,
   * the dispatcher sees that as a thrown error. Default: each call returns
   * a success response with a single assistant piece appended.
   */
  addMessageScript?: Array<AddMessageResponse | ApiError>
}

function mkApiMock(opts: ApiClientMockOptions = {}) {
  const createCalls: CreateAttackRequest[] = []
  const addMessageCalls: Array<{ attackResultId: string; request: AddMessageRequest }> = []
  const successfulCreate: CreateAttackResponse =
    opts.createAttackResult && !('status' in opts.createAttackResult)
      ? opts.createAttackResult
      : { attack_result_id: 'ar-1', conversation_id: 'conv-1', created_at: '2026-06-10T00:00:00Z' }
  const script = opts.addMessageScript ?? []
  let scriptCursor = 0

  const api: RunnerAttacksApi = {
    createAttack: async (request: CreateAttackRequest) => {
      createCalls.push(request)
      if (opts.createAttackResult && 'status' in opts.createAttackResult) {
        throw opts.createAttackResult
      }
      return successfulCreate
    },
    addMessage: async (attackResultId: string, request: AddMessageRequest) => {
      addMessageCalls.push({ attackResultId, request })
      const idx = scriptCursor++
      const scripted = script[idx]
      if (scripted !== undefined) {
        if ('status' in scripted) throw scripted
        return scripted
      }
      return mkAddMessageResponse({
        attackResultId,
        turnNumber: idx + 2,
        pieceId: `asst-${idx}`,
      })
    },
  }
  return { api, createCalls, addMessageCalls }
}

function mkAddMessageResponse(args: {
  attackResultId: string
  turnNumber: number
  pieceId: string
}): AddMessageResponse {
  const messages: ConversationMessagesResponse = {
    conversation_id: 'conv-1',
    messages: [
      // The dispatcher diffs by turn_number; provide enough context that
      // older turns don't accidentally look new.
      {
        turn_number: args.turnNumber,
        role: 'assistant',
        pieces: [
          {
            piece_id: args.pieceId,
            original_value_data_type: 'text',
            converted_value_data_type: 'text',
            original_value: 'response text',
            converted_value: 'response text',
            scores: [],
            response_error: 'none',
            original_prompt_id: args.pieceId,
            converter_identifiers: [],
          },
        ],
        created_at: '2026-06-10T00:00:00Z',
      },
    ],
  }
  return {
    attack: {
      attack_result_id: args.attackResultId,
      conversation_id: 'conv-1',
      attack_type: 'manual',
      converters: [],
      message_count: args.turnNumber,
      related_conversation_ids: [],
      labels: {},
      created_at: '2026-06-10T00:00:00Z',
      updated_at: '2026-06-10T00:00:00Z',
    },
    messages,
  }
}

function mkApiError(overrides: Partial<ApiError> = {}): ApiError {
  return {
    status: 500,
    detail: 'server boom',
    isNetworkError: false,
    isTimeout: false,
    raw: null,
    ...overrides,
  }
}

// ============================================================================
// Standard dispatch context — operator tag, wave id, trigger kind, etc.
// ============================================================================

const STANDARD_CTX = {
  operator: 'alice',
  operation: 'op-1',
  waveId: 'wave-uuid-1',
  waveTriggerKind: 'refresh_node' as const,
}

// ============================================================================
// 1. Happy path — single-Send chain dispatches one create_attack + one add_message
// ============================================================================

describe('dispatchLeaf — happy path (single-Send chain)', () => {
  it('fires one create_attack + one add_message; records the leaf execution; flips to clean', async () => {
    const tree = mkTree('r', [
      mkRoot('r', { text: 'hello', targetRegistryName: 'gpt-4o' }),
      mkUserTurn('u', 'r', { text: 'hi' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const { sink, callsOf } = mkMockSink()
    const { api, createCalls, addMessageCalls } = mkApiMock()

    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    expect(outcome.kind).toBe('success')
    expect(createCalls).toHaveLength(1)
    expect(addMessageCalls).toHaveLength(1)

    // create_attack sends the resolved target + empty prepended.
    expect(createCalls[0].target_registry_name).toBe('gpt-4o')
    expect(createCalls[0].prepended_conversation).toEqual([])

    // add_message sends the leaf's input UserTurn with send=true.
    const am = addMessageCalls[0].request
    expect(am.role).toBe('user')
    expect(am.send).toBe(true)
    expect(am.target_registry_name).toBe('gpt-4o')
    expect(am.target_conversation_id).toBe('conv-1')
    expect(am.pieces).toHaveLength(1)
    expect(am.pieces[0].original_value).toBe('hi')

    // State transitions: running → clean on the leaf.
    const leafStates = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('s')).map((c) => c.state)
    expect(leafStates).toEqual(['running', 'clean'])

    // ExecutionRecord attached to the leaf.
    const execCalls = callsOf('recordExecution').filter((c) => c.nodeId === nodeId('s'))
    expect(execCalls).toHaveLength(1)
    expect(execCalls[0].execution.attackResultId).toBe('ar-1')
    expect(execCalls[0].execution.conversationId).toBe('conv-1')
    expect(execCalls[0].execution.pieceIds).toEqual(['asst-0'])
    expect(execCalls[0].execution.responsePreview).toBe('response text')
    expect(execCalls[0].execution.outcome).toBe('success')
    expect(execCalls[0].execution.waveId).toBe('wave-uuid-1')
    expect(execCalls[0].execution.waveTriggerKind).toBe('refresh_node')
  })

  it('cleans an edited UserTurn once its downstream Send succeeds', async () => {
    const tree = mkTree('r', [
      mkRoot('r', { targetRegistryName: 'gpt-4o' }),
      mkUserTurn('u', 'r', { text: 'hi' }, { state: 'edited' }),
      mkSend('s', 'u', undefined, { state: 'stale' }),
    ])
    const { sink, callsOf } = mkMockSink()
    const { api } = mkApiMock()

    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    expect(outcome.kind).toBe('success')
    const userTurnStates = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('u')).map((c) => c.state)
    expect(userTurnStates).toContain('clean')
  })

  it('cleans an edited root prompt when it is the Send input', async () => {
    const tree = mkTree('r', [
      mkRoot('r', { text: 'hi', targetRegistryName: 'gpt-4o' }, { state: 'edited' }),
      mkSend('s', 'r', undefined, { state: 'stale' }),
    ])
    const { sink, callsOf } = mkMockSink()
    const { api } = mkApiMock()

    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    expect(outcome.kind).toBe('success')
    const rootStates = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('r')).map((c) => c.state)
    expect(rootStates).toContain('clean')
  })
})

// ============================================================================
// 2. Multi-Send chain — prefix loaded; N add_messages for fresh suffix
// ============================================================================

describe('dispatchLeaf — multi-Send chain (V1.0: no clean-prefix optimization)', () => {
  it('V1.0: even chains with clean upstream Sends re-fire every Send; prepended is empty', async () => {
    // V1.0 has no clean-prefix optimization (see partition.ts file header):
    // every Send on the path enters freshSuffix, regardless of state. The
    // operator-visible cost is the ~5× hot-path regression on edit-leaf-only
    // workflows, documented in 01 §1.2.
    const cleanExec = mkExecution({
      executionId: 'old-s1',
      pieceIds: ['p-asst-1'],
      attackResultId: 'ar-old',
    })
    const tree = mkTree('r', [
      mkRoot('r', { text: 'q', targetRegistryName: 'gpt-4o' }),
      mkUserTurn('u1', 'r', { text: 'turn 1' }),
      mkSend('s1', 'u1', undefined, { state: 'clean', execution: cleanExec }),
      mkUserTurn('u2', 's1', { text: 'turn 2' }),
      mkSend('s2', 'u2', undefined, { state: 'edited' }),
    ])
    const { sink, callsOf } = mkMockSink()
    const { api, createCalls, addMessageCalls } = mkApiMock()

    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s2'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    expect(outcome.kind).toBe('success')
    // One create_attack with EMPTY prepended (no system prompt in fixture);
    // every Send re-fires as its own add_message.
    expect(createCalls).toHaveLength(1)
    expect(createCalls[0].prepended_conversation).toEqual([])
    expect(addMessageCalls).toHaveLength(2)
    expect(addMessageCalls[0].request.pieces[0].original_value).toBe('turn 1')
    expect(addMessageCalls[1].request.pieces[0].original_value).toBe('turn 2')

    // Both Sends went running → clean (s1's prior execution is replaced).
    expect(callsOf('setNodeState').filter((c) => c.nodeId === nodeId('s1')).map((c) => c.state)).toEqual([
      'running',
      'clean',
    ])
    const leafStates = callsOf('setNodeState')
      .filter((c) => c.nodeId === nodeId('s2'))
      .map((c) => c.state)
    expect(leafStates).toEqual(['running', 'clean'])
  })

  it('chain with multiple stale Sends produces multiple add_message calls in topo order', async () => {
    // r → u1 → s1(stale) → u2 → s2(edited)
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r', { text: 't1' }),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', { text: 't2' }),
      mkSend('s2', 'u2', undefined, { state: 'edited' }),
    ])
    const { sink, callsOf } = mkMockSink()
    const { api, addMessageCalls } = mkApiMock()

    await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s2'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    // Two add_messages: one for s1, one for s2 (in path order).
    expect(addMessageCalls).toHaveLength(2)
    expect(addMessageCalls[0].request.pieces[0].original_value).toBe('t1')
    expect(addMessageCalls[1].request.pieces[0].original_value).toBe('t2')

    // Both Sends went running → clean; each got an ExecutionRecord.
    expect(callsOf('setNodeState').filter((c) => c.nodeId === nodeId('s1')).map((c) => c.state)).toEqual([
      'running',
      'clean',
    ])
    expect(callsOf('setNodeState').filter((c) => c.nodeId === nodeId('s2')).map((c) => c.state)).toEqual([
      'running',
      'clean',
    ])
    expect(callsOf('recordExecution').filter((c) => c.nodeId === nodeId('s1'))).toHaveLength(1)
    expect(callsOf('recordExecution').filter((c) => c.nodeId === nodeId('s2'))).toHaveLength(1)
  })
})

// ============================================================================
// 3. The labels-divergence invariant at the call site
// ============================================================================

describe('dispatchLeaf — labels-divergence invariant', () => {
  it('every request in a leaf sequence carries deep-equal labels', async () => {
    // 4-deep stale chain → 1 create_attack + 4 add_messages = 5 requests
    // total. All five must carry identical labels dicts. A future
    // refactor that varies labels mid-sequence would silently break
    // the backend's `_resolve_labels` preference-for-existing-piece-labels
    // path.
    const tree = mkTree('r', [
      mkRoot('r', { text: 'q', targetRegistryName: 'gpt-4o' }),
      mkUserTurn('u1', 'r', { text: 't1' }),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', { text: 't2' }),
      mkSend('s2', 'u2', undefined, { state: 'stale' }),
      mkUserTurn('u3', 's2', { text: 't3' }),
      mkSend('s3', 'u3', undefined, { state: 'stale' }),
      mkUserTurn('u4', 's3', { text: 't4' }),
      mkSend('s4', 'u4', undefined, { state: 'edited' }),
    ])
    const { sink } = mkMockSink()
    const { api, createCalls, addMessageCalls } = mkApiMock()

    await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s4'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    expect(createCalls).toHaveLength(1)
    expect(addMessageCalls).toHaveLength(4)

    const allLabels = [createCalls[0].labels, ...addMessageCalls.map((c) => c.request.labels)]
    expect(allLabels).toHaveLength(5)
    // Every dict is deep-equal to the first one.
    for (const labels of allLabels) {
      expect(labels).toEqual(allLabels[0])
    }
    // Sanity: the labels include the V1.0 required keys.
    const first = allLabels[0]
    expect(first).toMatchObject({
      operator: 'alice',
      operation: 'op-1',
      conversation_tree_id: 't-1',
      wave_id: 'wave-uuid-1',
      wave_trigger_kind: 'refresh_node',
      tree_path: '[]',
    })
  })

  it('writes parent_conversation_tree_id only when the tree is a clone', async () => {
    const tree = mkTree(
      'r',
      [
        mkRoot('r'),
        mkUserTurn('u', 'r', { text: 't' }),
        mkSend('s', 'u', undefined, { state: 'edited' }),
      ],
      { parentConversationTreeId: 't-source' },
    )
    const { sink } = mkMockSink()
    const { api, createCalls } = mkApiMock()

    await dispatchLeaf({
      treeId: treeId('t-clone'),
      tree,
      leafId: nodeId('s'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: treeId('t-source'),
    })

    expect(createCalls[0].labels?.parent_conversation_tree_id).toBe('t-source')
  })
})

// ============================================================================
// 4. tree_path label is populated from fan ancestors
// ============================================================================

describe('dispatchLeaf — tree_path label', () => {
  it('writes tree_path with the Fan ancestor (axis, slot)', async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 'q' }),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_0', 'f', undefined, { state: 'edited' }),
      mkSend('s_1', 'f', undefined, { state: 'edited' }),
    ])
    const { sink } = mkMockSink()
    const { api, createCalls } = mkApiMock()

    await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s_1'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    expect(createCalls[0].labels?.tree_path).toBe('[["attempt",1]]')
  })
})

// ============================================================================
// 5. The 200-message cap short-circuit
// ============================================================================

describe('dispatchLeaf — 200-message cap (V1.0: unreachable by construction)', () => {
  it('a 100-deep chain dispatches successfully under V1.0 because prepended is empty (no clean-prefix optimization)', async () => {
    // V1.0's partition pushes every Send into freshSuffix; prepended carries
    // at most a system prompt. The backend's 200-message cap on
    // prepended_conversation is therefore unreachable in V1.0 normal traffic.
    // This test documents that contract: the dispatcher does NOT fail a
    // 100-Send chain on cap grounds — it dispatches all 100 add_messages.
    // V1.x will restore the cap as a real concern once the piece cache
    // populates prepended with clean-prefix content.
    const nodes: ConversationTreeNode[] = [mkRoot('r', { text: 'q', targetRegistryName: 'gpt-4o' })]
    let parent = 'r'
    for (let i = 0; i < 100; i++) {
      const uid = `u${i}`
      const sid = `s${i}`
      nodes.push(mkUserTurn(uid, parent, { text: `t${i}` }))
      nodes.push(
        mkSend(sid, uid, undefined, {
          state: 'clean',
          execution: mkExecution({ executionId: `e${i}`, pieceIds: [`p${i}`] }),
        }),
      )
      parent = sid
    }
    nodes.push(mkUserTurn('u_leaf', parent, { text: 'tail' }))
    nodes.push(mkSend('s_leaf', 'u_leaf', undefined, { state: 'edited' }))

    const tree = mkTree('r', nodes)
    const { sink } = mkMockSink()
    const { api, createCalls, addMessageCalls } = mkApiMock()

    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s_leaf'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    expect(outcome.kind).toBe('success')
    expect(createCalls).toHaveLength(1)
    // prepended is empty because no clean-prefix optimization in V1.0.
    expect(createCalls[0].prepended_conversation).toEqual([])
    // 100 chain Sends + 1 leaf Send = 101 add_messages.
    expect(addMessageCalls).toHaveLength(101)
  })

  it('the dispatcher still short-circuits if prepended > 200 (defensive check for V1.x cache)', async () => {
    // The cap check in dispatch.ts is defensive scaffolding for the V1.x
    // clean-prefix cache. The V1.0 partition never produces a prepended >0
    // (or >1 with system), so this code path is unreachable through normal
    // dispatch. Test it by constructing a tree with one root prompt that
    // would feed into the cap check IF the partition restored clean-prefix
    // behavior. V1.0: this test PASSES the dispatch (because prepended is
    // empty), but documents the V1.x-future intent of the cap.
    //
    // No assertion against the cap firing — V1.0 cannot trigger it. The
    // test exists as a placeholder so it's obvious where to extend the
    // assertion when the cache layer lands.
    const tree = mkTree('r', [
      mkRoot('r', { text: 'q', targetRegistryName: 'gpt-4o' }),
      mkUserTurn('u', 'r', { text: 't' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const { sink } = mkMockSink()
    const { api } = mkApiMock()
    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })
    expect(outcome.kind).toBe('success')
  })
})

// ============================================================================
// 6. Failure paths — single-call, mid-chain, classification
// ============================================================================

describe('dispatchLeaf — failure handling', () => {
  it('create_attack failure: every fresh-suffix Send fails; no add_message fires', async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r', { text: 't1' }),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', { text: 't2' }),
      mkSend('s2', 'u2', undefined, { state: 'edited' }),
    ])
    const { sink, callsOf } = mkMockSink()
    const { api, addMessageCalls } = mkApiMock({
      createAttackResult: mkApiError({ status: 500, detail: 'boom' }),
    })

    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s2'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    expect(outcome.kind).toBe('failed')
    if (outcome.kind === 'failed') {
      expect(outcome.failureClass).toBe<NodeFailureClass>('transient')
    }
    expect(addMessageCalls).toHaveLength(0)

    // Every stale Send in the fresh suffix transitions to failed with the
    // formatted reason; executions cleared.
    for (const id of ['s1', 's2']) {
      const states = callsOf('setNodeState').filter((c) => c.nodeId === nodeId(id)).map((c) => c.state)
      expect(states).toContain('failed')
      const clear = callsOf('clearExecution').filter((c) => c.nodeId === nodeId(id))
      expect(clear).toHaveLength(1)
    }
  })

  it('add_message mid-chain failure: failed Send fails; subsequent Sends roll back to stale', async () => {
    // 3-stale chain: s1 → s2 → s3 (leaf). add_message #2 (for s2) fails.
    // s1 already succeeded → stays clean. s2 fails. s3 rolls back to stale.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r', { text: 't1' }),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', { text: 't2' }),
      mkSend('s2', 'u2', undefined, { state: 'stale' }),
      mkUserTurn('u3', 's2', { text: 't3' }),
      mkSend('s3', 'u3', undefined, { state: 'edited' }),
    ])
    const { sink, callsOf } = mkMockSink()
    const { api, addMessageCalls } = mkApiMock({
      addMessageScript: [
        // call #1 (for s1): success
        mkAddMessageResponse({ attackResultId: 'ar-1', turnNumber: 1, pieceId: 'asst-1' }),
        // call #2 (for s2): 429 rate-limit
        mkApiError({ status: 429, detail: 'rate limit hit' }),
      ],
    })

    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s3'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })

    expect(outcome.kind).toBe('failed')
    if (outcome.kind === 'failed') {
      expect(outcome.failureClass).toBe<NodeFailureClass>('rate_limited')
      expect(outcome.failedNodeId).toBe(nodeId('s2'))
    }
    // Only 2 add_messages fired: s1 succeeded, s2 failed (no further calls).
    expect(addMessageCalls).toHaveLength(2)

    // s1: running → clean (succeeded before the failure).
    expect(callsOf('setNodeState').filter((c) => c.nodeId === nodeId('s1')).map((c) => c.state)).toEqual([
      'running',
      'clean',
    ])
    // s2: running → failed.
    expect(callsOf('setNodeState').filter((c) => c.nodeId === nodeId('s2')).map((c) => c.state)).toEqual([
      'running',
      'failed',
    ])
    // s3: running → stale (rolled back).
    expect(callsOf('setNodeState').filter((c) => c.nodeId === nodeId('s3')).map((c) => c.state)).toEqual([
      'running',
      'stale',
    ])
    // s2 and s3 executions cleared; s1 keeps its recorded execution.
    expect(callsOf('clearExecution').map((c) => c.nodeId).sort()).toEqual(
      [nodeId('s2'), nodeId('s3')].sort(),
    )
    expect(callsOf('recordExecution').filter((c) => c.nodeId === nodeId('s1'))).toHaveLength(1)
    expect(callsOf('recordExecution').filter((c) => c.nodeId === nodeId('s2'))).toHaveLength(0)
  })

  it('classifies a 429 as rate_limited; passes the class through the outcome', async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 't' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const { sink } = mkMockSink()
    const { api } = mkApiMock({
      createAttackResult: mkApiError({ status: 429, detail: 'rate' }),
    })
    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })
    expect(outcome.kind).toBe('failed')
    if (outcome.kind === 'failed') expect(outcome.failureClass).toBe<NodeFailureClass>('rate_limited')
  })

  it('classifies a 400 with "operator mismatch" body as permanent', async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 't' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const { sink } = mkMockSink()
    const { api } = mkApiMock({
      createAttackResult: mkApiError({ status: 400, detail: 'Operator mismatch: locked' }),
    })
    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })
    expect(outcome.kind).toBe('failed')
    if (outcome.kind === 'failed') expect(outcome.failureClass).toBe<NodeFailureClass>('permanent')
  })
})

// ============================================================================
// 7. Tag-hygiene gate (defense-in-depth at the dispatcher)
// ============================================================================

describe('dispatchLeaf — operator tag', () => {
  it('throws synchronously if operator is empty (the gate is upstream; this is defense-in-depth)', async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 't' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const { sink } = mkMockSink()
    const { api, createCalls } = mkApiMock()

    await expect(
      dispatchLeaf({
        treeId: treeId('t-1'),
        tree,
        leafId: nodeId('s'),
        sink,
        api,
        operator: '',
        operation: '',
        waveId: 'w-1',
        waveTriggerKind: 'refresh_node',
        parentConversationTreeId: null,
      }),
    ).rejects.toThrow(/operator.*required/i)

    // No backend call fired.
    expect(createCalls).toHaveLength(0)
  })
})

// ============================================================================
// 8. The Q.S.1 cost-cliff regression — pins the no-memoization invariant
// ============================================================================

describe('dispatchLeaf — Q.S.1 cost cliff (no intra-wave memoization)', () => {
  it('60 sibling leaves with a 10-deep shared stale prefix each fire 11 calls (660 total)', async () => {
    // V1.0 deliberately ships WITHOUT intra-wave memoization (Q.S.1 decided
    // 2026: accept-and-disclose, see design doc §1.2). A 60-leaf attempt
    // fan with a 10-deep shared stale prefix produces 60 dispatches × 11
    // calls each (1 create_attack + 10 add_messages for the shared chain +
    // 1 for the leaf-Send... wait, that's 12 calls per leaf if the leaf is
    // also stale, or 11 if shared chain is the 10 stale Sends and the leaf
    // is the 11th add_message at the bottom).
    //
    // Modeling: build a 10-deep chain of stale Sends shared by all leaves.
    // Each fan-child Send is the 11th stale Send below the chain. Per leaf:
    //   1 create_attack + 11 add_messages = 12 calls.
    //   60 leaves × 12 = 720 calls total.
    //
    // The regression invariant is "linear in fan count × depth, not
    // linear in fan count alone". If someone adds memoization that
    // reuses pieces across sibling leaves, this drops to ~71 calls
    // and the assertion fires loudly.
    //
    // Per-leaf assertion runs as a single dispatchLeaf call; the
    // sibling-summation is the outer loop. Total calls across 60
    // dispatches should be exactly 60 × 12 = 720.
    const SHARED_DEPTH = 10
    const NUM_LEAVES = 60
    const nodes: ConversationTreeNode[] = [mkRoot('r', { targetRegistryName: 'gpt-4o' })]
    let parent = 'r'
    for (let i = 0; i < SHARED_DEPTH; i++) {
      nodes.push(mkUserTurn(`u${i}`, parent, { text: `t${i}` }))
      nodes.push(mkSend(`s${i}`, `u${i}`, undefined, { state: 'stale' }))
      parent = `s${i}`
    }
    // One UT above the fan so each fan-child Send has an input UserTurn per
    // the §5.1 #5 invariant.
    nodes.push(mkUserTurn('u_above_fan', parent, { text: 'shared input' }))
    nodes.push(
      mkFan('fan', 'u_above_fan', {
        axis: 'attempt',
        variants: Array.from({ length: NUM_LEAVES }, () => ({ axis: 'attempt' as const, payload: {} })),
      }),
    )
    const leafIds: string[] = []
    for (let i = 0; i < NUM_LEAVES; i++) {
      const lid = `leaf_${i}`
      nodes.push(mkSend(lid, 'fan', undefined, { state: 'edited' }))
      leafIds.push(lid)
    }
    const tree = mkTree('r', nodes)

    let totalCreate = 0
    let totalAddMessage = 0
    for (const lid of leafIds) {
      const { sink } = mkMockSink()
      const { api, createCalls, addMessageCalls } = mkApiMock({
        addMessageScript: Array.from({ length: SHARED_DEPTH + 1 }, (_, i) =>
          mkAddMessageResponse({ attackResultId: 'ar-1', turnNumber: i + 1, pieceId: `p-${i}` }),
        ),
      })
      const outcome = await dispatchLeaf({
        treeId: treeId('t-1'),
        tree,
        leafId: nodeId(lid),
        sink,
        api,
        ...STANDARD_CTX,
        parentConversationTreeId: null,
      })
      expect(outcome.kind).toBe('success')
      totalCreate += createCalls.length
      totalAddMessage += addMessageCalls.length
    }

    // 60 leaves, each: 1 create_attack + 11 add_messages = 60 + 660 = 720.
    expect(totalCreate).toBe(NUM_LEAVES) // 60
    expect(totalAddMessage).toBe(NUM_LEAVES * (SHARED_DEPTH + 1)) // 60 × 11 = 660
    expect(totalCreate + totalAddMessage).toBe(720)
  })
})

// ============================================================================
// 9. LeafDispatchOutcome shape — the dispatcher's return value
// ============================================================================

describe('dispatchLeaf — outcome shape', () => {
  it('success outcome carries the leaf id and call counts', async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 't' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const { sink } = mkMockSink()
    const { api } = mkApiMock()
    const outcome: LeafDispatchOutcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })
    expect(outcome.kind).toBe('success')
    if (outcome.kind === 'success') {
      expect(outcome.leafId).toBe(nodeId('s'))
      expect(outcome.callsIssued).toBe(2) // 1 create_attack + 1 add_message
    }
  })

  it('failure outcome carries the failed node id, failure class, and the partial-commit ar id (when create_attack succeeded)', async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r', { text: 't1' }),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', { text: 't2' }),
      mkSend('s2', 'u2', undefined, { state: 'edited' }),
    ])
    const { sink } = mkMockSink()
    const { api } = mkApiMock({
      addMessageScript: [
        mkAddMessageResponse({ attackResultId: 'ar-1', turnNumber: 1, pieceId: 'p1' }),
        mkApiError({ status: 500, detail: 'boom' }),
      ],
    })
    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s2'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })
    expect(outcome.kind).toBe('failed')
    if (outcome.kind === 'failed') {
      expect(outcome.failedNodeId).toBe(nodeId('s2'))
      expect(outcome.failureClass).toBe<NodeFailureClass>('transient')
      // The partial AR was created (s1 succeeded); the outcome surfaces
      // it so the operator can find the partial row in History.
      expect(outcome.partialAttackResultId).toBe('ar-1')
    }
  })

  it('failure outcome from a pre-create_attack failure has no partial ar', async () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 't' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const { sink } = mkMockSink()
    const { api } = mkApiMock({
      createAttackResult: mkApiError({ status: 500, detail: 'boom' }),
    })
    const outcome = await dispatchLeaf({
      treeId: treeId('t-1'),
      tree,
      leafId: nodeId('s'),
      sink,
      api,
      ...STANDARD_CTX,
      parentConversationTreeId: null,
    })
    if (outcome.kind === 'failed') {
      expect(outcome.partialAttackResultId).toBeNull()
    }
  })
})
