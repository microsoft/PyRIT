// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the tree-state reducer per spec 01 §6.4–6.6.
 *
 * The reducer is a pure-functional bridge between the runner's sink
 * interface (`RunnerStateSink`) and the React-state ConversationTree
 * the host owns. Each reducer takes a tree + args and returns a new
 * tree (immutable update); the host's sink methods compose them with
 * `setState`.
 */

import {
  applySetNodeState,
  applyRecordExecution,
  applyClearExecution,
  applySetReflogPinned,
  applyEditRootPromptParams,
  applyEditUserTurnText,
  applyAppendChild,
  applyAppendPromptWithResponse,
  applyInsertConverterBetween,
  applyInsertBetween,
  applyWrapWithFan,
  applySetConverterNodePipeline,
  applySetUserTurnConverterPipeline,
  applySetFanPromotedChild,
  applyPruneFanToPickedPath,
  applyDeleteSubtree,
  applyCloneTree,
  applyBranchFromNode,
} from './treeStateReducer'
import { mkConverterNode, mkFan, mkRoot, mkSend, mkTree, mkUserTurn, nodeId, treeId } from './testHelpers'
import type {
  ConversationTree,
  ExecutionRecord,
} from './treeTypes'

// ============================================================================
// Fixtures
// ============================================================================

function mkExec(id: string, attemptedAt = '2026-06-11T00:00:00Z'): ExecutionRecord {
  return {
    executionId: id,
    attemptedAt,
    attackResultId: `ar-${id}`,
    conversationId: `c-${id}`,
    pieceIds: [],
    outcome: 'success',
    resolvedInputHashAtExecution: 'h',
    waveId: `w-${id}`,
    waveTriggerKind: 'refresh_tree',
    dispatchedAt: attemptedAt,
    targetFirstByteAt: null,
    completedAt: attemptedAt,
  }
}

function tree1(): ConversationTree {
  return mkTree('root', [
    mkRoot('root'),
    mkSend('send-1', 'root'),
  ])
}

// ============================================================================
// applySetNodeState
// ============================================================================

describe('applySetNodeState', () => {
  it('replaces the named node state, bumps version and updatedAt', () => {
    const before = tree1()
    const send = before.nodes.find((n) => n.id === nodeId('send-1'))!
    const priorVersion = send.version
    const priorUpdatedAt = send.updatedAt

    const after = applySetNodeState(before, nodeId('send-1'), 'running')

    const afterSend = after.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(afterSend.state).toBe('running')
    expect(afterSend.version).toBe(priorVersion + 1)
    expect(afterSend.updatedAt >= priorUpdatedAt).toBe(true)
    // The unchanged root is referentially identical (structural sharing).
    const beforeRoot = before.nodes.find((n) => n.id === nodeId('root'))!
    const afterRoot = after.nodes.find((n) => n.id === nodeId('root'))!
    expect(afterRoot).toBe(beforeRoot)
  })

  it('string reason → lastError = { message, failure_class: transient }', () => {
    const before = tree1()
    const after = applySetNodeState(before, nodeId('send-1'), 'failed', { reason: 'timeout' })
    const send = after.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.lastError).toEqual({ message: 'timeout', failure_class: 'transient' })
  })

  it('ApiErrorReason → written directly to lastError', () => {
    const before = tree1()
    const after = applySetNodeState(before, nodeId('send-1'), 'failed', {
      reason: { message: '429', failure_class: 'rate_limited' },
    })
    const send = after.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.lastError).toEqual({ message: '429', failure_class: 'rate_limited' })
  })

  it('reason=null → clears lastError', () => {
    const before = applySetNodeState(tree1(), nodeId('send-1'), 'failed', { reason: 'boom' })
    const after = applySetNodeState(before, nodeId('send-1'), 'clean', { reason: null })
    const send = after.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.lastError).toBeNull()
  })

  it('reason omitted → preserves existing lastError', () => {
    const before = applySetNodeState(tree1(), nodeId('send-1'), 'failed', {
      reason: { message: 'x', failure_class: 'permanent' },
    })
    const after = applySetNodeState(before, nodeId('send-1'), 'stale')
    const send = after.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.lastError).toEqual({ message: 'x', failure_class: 'permanent' })
  })

  it('missing node → returns the same tree reference (no-op)', () => {
    const before = tree1()
    const after = applySetNodeState(before, nodeId('nonexistent'), 'running')
    expect(after).toBe(before)
  })
})

// ============================================================================
// applyRecordExecution
// ============================================================================

describe('applyRecordExecution', () => {
  it('first execution: sets execution; no reflog entries created', () => {
    const exec = mkExec('e1')
    const after = applyRecordExecution(tree1(), nodeId('send-1'), exec)
    const send = after.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.execution?.executionId).toBe('e1')
    expect(send.executionHistory).toEqual([])
  })

  it('recording a Send execution updates the response preview', () => {
    const exec = { ...mkExec('e1'), responsePreview: 'fresh assistant text' }
    const after = applyRecordExecution(tree1(), nodeId('send-1'), exec)
    const send = after.nodes.find((n) => n.id === nodeId('send-1'))

    expect(send?.kind).toBe('send')
    expect(send?.kind === 'send' ? send.params.responsePreview : undefined).toBe('fresh assistant text')
  })

  it('subsequent execution: prior moves to reflog with pinned=false', () => {
    const first = applyRecordExecution(tree1(), nodeId('send-1'), mkExec('e1'))
    const second = applyRecordExecution(first, nodeId('send-1'), mkExec('e2'))
    const send = second.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.execution?.executionId).toBe('e2')
    expect(send.executionHistory).toHaveLength(1)
    expect(send.executionHistory[0].execution.executionId).toBe('e1')
    expect(send.executionHistory[0].pinned).toBe(false)
  })

  it('reflog grows newest-first (most-recent push prepended)', () => {
    let t = applyRecordExecution(tree1(), nodeId('send-1'), mkExec('e1'))
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e2'))
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e3'))
    const send = t.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.execution?.executionId).toBe('e3')
    // Reflog has e2 first (more recent), e1 second.
    expect(send.executionHistory.map((e) => e.execution.executionId)).toEqual(['e2', 'e1'])
  })

  it('at cap with all unpinned: oldest unpinned evicted', () => {
    let t = applyRecordExecution(tree1(), nodeId('send-1'), mkExec('e0'))
    // Push enough to reach cap=3 (using test-supplied cap).
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e1'), { reflogCap: 3 })
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e2'), { reflogCap: 3 })
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e3'), { reflogCap: 3 })
    // Now executionHistory holds e2, e1, e0 (3 entries). Push e4; cap=3 evicts
    // the oldest unpinned (e0).
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e4'), { reflogCap: 3 })
    const send = t.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.execution?.executionId).toBe('e4')
    expect(send.executionHistory.map((e) => e.execution.executionId)).toEqual(['e3', 'e2', 'e1'])
  })

  it('at cap with one pinned entry: pinned preserved, oldest UNPINNED evicted', () => {
    let t = applyRecordExecution(tree1(), nodeId('send-1'), mkExec('e0'))
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e1'), { reflogCap: 3 })
    // Pin e0 (which is now in the reflog after the e1 push).
    t = applySetReflogPinned(t, nodeId('send-1'), 'e0', true)
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e2'), { reflogCap: 3 })
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e3'), { reflogCap: 3 })
    // Reflog at cap (3 entries: e2, e1, e0-pinned). Push e4; cap=3 evicts the
    // oldest unpinned (e1; e0 is pinned and survives).
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e4'), { reflogCap: 3 })
    const send = t.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.execution?.executionId).toBe('e4')
    const ids = send.executionHistory.map((e) => `${e.execution.executionId}${e.pinned ? '*' : ''}`)
    // Order: e3 (newest reflog), e2, e0* (pinned survivor). e1 evicted.
    expect(ids).toEqual(['e3', 'e2', 'e0*'])
  })

  it('missing node → returns same tree reference', () => {
    const before = tree1()
    const after = applyRecordExecution(before, nodeId('nonexistent'), mkExec('e1'))
    expect(after).toBe(before)
  })
})

// ============================================================================
// applyClearExecution
// ============================================================================

describe('applyClearExecution', () => {
  it('sets execution to null; reflog untouched', () => {
    let t = applyRecordExecution(tree1(), nodeId('send-1'), mkExec('e0'))
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e1'))
    const before = t
    const after = applyClearExecution(before, nodeId('send-1'))
    const send = after.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.execution).toBeNull()
    expect(send.executionHistory).toHaveLength(1) // e0 still in reflog
  })

  it('missing node → returns same tree reference', () => {
    const before = tree1()
    const after = applyClearExecution(before, nodeId('nonexistent'))
    expect(after).toBe(before)
  })
})

// ============================================================================
// applySetReflogPinned
// ============================================================================

describe('applySetReflogPinned', () => {
  it('sets pinned=true on the named reflog entry', () => {
    let t = applyRecordExecution(tree1(), nodeId('send-1'), mkExec('e0'))
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e1'))
    const after = applySetReflogPinned(t, nodeId('send-1'), 'e0', true)
    const send = after.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.executionHistory[0].pinned).toBe(true)
  })

  it('sets pinned=false on an already-pinned entry', () => {
    let t = applyRecordExecution(tree1(), nodeId('send-1'), mkExec('e0'))
    t = applyRecordExecution(t, nodeId('send-1'), mkExec('e1'))
    t = applySetReflogPinned(t, nodeId('send-1'), 'e0', true)
    const after = applySetReflogPinned(t, nodeId('send-1'), 'e0', false)
    const send = after.nodes.find((n) => n.id === nodeId('send-1'))!
    expect(send.executionHistory[0].pinned).toBe(false)
  })

  it('missing execution id → returns same tree reference (no-op)', () => {
    const before = applyRecordExecution(tree1(), nodeId('send-1'), mkExec('e0'))
    const after = applySetReflogPinned(before, nodeId('send-1'), 'never-existed', true)
    expect(after).toBe(before)
  })

  it('missing node → returns same tree reference', () => {
    const before = tree1()
    const after = applySetReflogPinned(before, nodeId('nonexistent'), 'e0', true)
    expect(after).toBe(before)
  })
})

// ============================================================================
// applyEdit*Params
// ============================================================================

describe('applyEditUserTurnText', () => {
  it('edits the user turn and marks clean descendants stale', () => {
    const before = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root', { text: 'before' }),
      mkSend('s1', 'u1'),
      mkUserTurn('u2', 's1'),
      mkSend('s2', 'u2'),
    ])

    const after = applyEditUserTurnText(before, nodeId('u1'), 'after')

    const byId = new Map(after.nodes.map((n) => [n.id, n]))
    expect(byId.get(nodeId('u1'))?.state).toBe('edited')
    expect(byId.get(nodeId('u1'))?.params).toMatchObject({ text: 'after' })
    expect(byId.get(nodeId('s1'))?.state).toBe('stale')
    expect(byId.get(nodeId('u2'))?.state).toBe('stale')
    expect(byId.get(nodeId('s2'))?.state).toBe('stale')
  })

  it('preserves already edited descendants so dirty state is not hidden', () => {
    const before = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root', { text: 'before' }),
      mkUserTurn('u2', 'u1', undefined, { state: 'edited' }),
    ])

    const after = applyEditUserTurnText(before, nodeId('u1'), 'after')

    expect(after.nodes.find((n) => n.id === nodeId('u2'))?.state).toBe('edited')
  })

  it('setting a converter pipeline marks the UserTurn edited and stales descendants', () => {
    const before = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root'),
      mkSend('s1', 'u1'),
    ])

    const after = applySetUserTurnConverterPipeline(before, nodeId('u1'), [{ converterId: 'base64' }])

    const user = after.nodes.find((node) => node.id === nodeId('u1'))
    const send = after.nodes.find((node) => node.id === nodeId('s1'))
    expect(user?.state).toBe('edited')
    expect(user?.kind === 'user_turn' ? user.params.converterPipeline : undefined).toEqual([{ converterId: 'base64' }])
    expect(send?.state).toBe('stale')
  })

  it('setting a visible converter node pipeline marks it edited and stales descendants', () => {
    const before = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root'),
      mkConverterNode('c1', 'u1'),
      mkSend('s1', 'c1'),
    ])

    const after = applySetConverterNodePipeline(before, nodeId('c1'), [{ converterId: 'base64' }])

    const converter = after.nodes.find((node) => node.id === nodeId('c1'))
    expect(converter?.kind === 'converter' ? converter.params.pipeline : []).toEqual([{ converterId: 'base64' }])
    expect(converter?.state).toBe('edited')
    expect(after.nodes.find((node) => node.id === nodeId('s1'))?.state).toBe('stale')
  })
})

describe('applyEditRootPromptParams', () => {
  it('edits root prompt params and stales descendants', () => {
    const before = mkTree('root', [
      mkRoot('root', { text: 'before', systemPrompt: 'sys', targetRegistryName: 'old-target' }),
      mkUserTurn('u1', 'root'),
      mkSend('s1', 'u1'),
    ])

    const after = applyEditRootPromptParams(before, nodeId('root'), {
      text: 'after',
      systemPrompt: '',
      targetRegistryName: 'new-target',
    })

    const root = after.nodes.find((n) => n.id === nodeId('root'))
    expect(root?.state).toBe('edited')
    expect(root?.params).toMatchObject({ text: 'after', targetRegistryName: 'new-target' })
    expect(root?.params.systemPrompt).toBeUndefined()
    expect(after.nodes.find((n) => n.id === nodeId('u1'))?.state).toBe('stale')
    expect(after.nodes.find((n) => n.id === nodeId('s1'))?.state).toBe('stale')
  })
})

// ============================================================================
// Structural edits
// ============================================================================

describe('structural insert reducers', () => {
  const ids = ['new-user', 'new-send', 'new-fan', 'fan-send', 'fan-user', 'fan-user-send']
  const uuid = jest.fn(() => ids.shift() ?? 'fallback-id')

  beforeEach(() => {
    ids.splice(0, ids.length, 'new-user', 'new-send', 'new-fan', 'fan-send', 'fan-user', 'fan-user-send')
    uuid.mockClear()
  })

  it('appends a follow-up user turn under a leaf response', () => {
    const before = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')])

    const after = applyAppendChild(before, nodeId('s1'), 'follow_up_user_turn', uuid)

    const added = after.nodes.find((node) => node.id === nodeId('new-user'))
    expect(added?.kind).toBe('user_turn')
    expect(added?.parentId).toBe(nodeId('s1'))
    expect(added?.state).toBe('edited')
    expect(after.edges.some((edge) => edge.parentId === nodeId('s1') && edge.childId === nodeId('new-user'))).toBe(true)
  })

  it('appends a follow-up prompt with a linked pending response under a leaf response', () => {
    const before = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')])

    const after = applyAppendPromptWithResponse(before, nodeId('s1'), uuid)

    const prompt = after.nodes.find((node) => node.id === nodeId('new-user'))
    const response = after.nodes.find((node) => node.id === nodeId('new-send'))
    expect(prompt?.kind).toBe('user_turn')
    expect(prompt?.parentId).toBe(nodeId('s1'))
    expect(prompt?.state).toBe('edited')
    expect(response?.kind).toBe('send')
    expect(response?.parentId).toBe(nodeId('new-user'))
    expect(response?.state).toBe('stale')
    expect(after.edges.some((edge) => edge.parentId === nodeId('s1') && edge.childId === nodeId('new-user'))).toBe(true)
    expect(after.edges.some((edge) => edge.parentId === nodeId('new-user') && edge.childId === nodeId('new-send'))).toBe(true)
  })

  it('appends a prompt with linked pending response under a root prompt', () => {
    const before = mkTree('root', [mkRoot('root')])

    const after = applyAppendPromptWithResponse(before, nodeId('root'), uuid, 'first path-chat prompt')

    const prompt = after.nodes.find((node) => node.id === nodeId('new-user'))
    const response = after.nodes.find((node) => node.id === nodeId('new-send'))
    expect(prompt?.kind).toBe('user_turn')
    expect(prompt?.parentId).toBe(nodeId('root'))
    expect(prompt?.kind === 'user_turn' ? prompt.params.text : '').toBe('first path-chat prompt')
    expect(response?.kind).toBe('send')
    expect(response?.parentId).toBe(nodeId('new-user'))
  })

  it('inserts a node between an existing parent and child edge', () => {
    const before = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')])

    const after = applyInsertBetween(before, nodeId('root'), nodeId('s1'), 'follow_up_user_turn', uuid)

    const inserted = after.nodes.find((node) => node.id === nodeId('new-user'))
    const child = after.nodes.find((node) => node.id === nodeId('s1'))
    expect(inserted?.parentId).toBe(nodeId('root'))
    expect(child?.parentId).toBe(nodeId('new-user'))
    expect(after.edges.some((edge) => edge.parentId === nodeId('root') && edge.childId === nodeId('new-user'))).toBe(true)
    expect(after.edges.some((edge) => edge.parentId === nodeId('new-user') && edge.childId === nodeId('s1'))).toBe(true)
  })

  it('adds a visible converter sibling path while preserving the direct response baseline', () => {
    const before = mkTree('root', [mkRoot('root'), mkUserTurn('u1', 'root'), mkSend('s1', 'u1')])

    const after = applyInsertConverterBetween(before, nodeId('u1'), nodeId('s1'), uuid)

    const converter = after.nodes.find((node) => node.id === nodeId('new-user'))
    const convertedResponse = after.nodes.find((node) => node.id === nodeId('new-send'))
    const directResponse = after.nodes.find((node) => node.id === nodeId('s1'))
    expect(converter?.kind).toBe('converter')
    expect(converter?.parentId).toBe(nodeId('u1'))
    expect(converter?.state).toBe('edited')
    expect(directResponse?.parentId).toBe(nodeId('u1'))
    expect(convertedResponse?.kind).toBe('send')
    expect(convertedResponse?.parentId).toBe(nodeId('new-user'))
    expect(convertedResponse?.state).toBe('stale')
    expect(after.edges.some((edge) => edge.parentId === nodeId('u1') && edge.childId === nodeId('s1'))).toBe(true)
    expect(after.edges.some((edge) => edge.parentId === nodeId('u1') && edge.childId === nodeId('new-user'))).toBe(true)
    expect(after.edges.some((edge) => edge.parentId === nodeId('new-user') && edge.childId === nodeId('new-send'))).toBe(true)
  })

  it('wraps an existing response edge in an attempt fan and adds a fresh response slot', () => {
    const before = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')])

    const after = applyWrapWithFan(before, nodeId('root'), nodeId('s1'), 'attempt', uuid)

    const fan = after.nodes.find((node) => node.id === nodeId('new-user'))
    const originalSend = after.nodes.find((node) => node.id === nodeId('s1'))
    const siblingSend = after.nodes.find((node) => node.id === nodeId('new-send'))
    expect(fan?.kind).toBe('fan')
    expect(fan?.kind === 'fan' ? fan.params.axis : null).toBe('attempt')
    expect(originalSend?.parentId).toBe(nodeId('new-user'))
    expect(siblingSend?.kind).toBe('send')
    expect(siblingSend?.parentId).toBe(nodeId('new-user'))
    expect(after.edges.find((edge) => edge.parentId === nodeId('new-user') && edge.childId === nodeId('s1'))?.slotIndex).toBe(0)
    expect(after.edges.find((edge) => edge.parentId === nodeId('new-user') && edge.childId === nodeId('new-send'))?.slotIndex).toBe(1)
  })

  it('wraps an existing response edge in an N-attempt fan with stable slot ids', () => {
    const before = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')])
    const manyIds = ['fan-5', 'send-1', 'send-2', 'send-3', 'send-4']
    const manyUuid = jest.fn(() => manyIds.shift() ?? 'fallback-id')

    const after = applyWrapWithFan(before, nodeId('root'), nodeId('s1'), 'attempt', manyUuid, { attemptCount: 5 })

    const fan = after.nodes.find((node) => node.id === nodeId('fan-5'))
    expect(fan?.kind === 'fan' ? fan.params.variants : []).toHaveLength(5)
    const childEdges = after.edges
      .filter((edge) => edge.parentId === nodeId('fan-5'))
      .sort((a, b) => a.slotIndex - b.slotIndex)
    expect(childEdges.map((edge) => edge.slotIndex)).toEqual([0, 1, 2, 3, 4])
    expect(new Set(childEdges.map((edge) => edge.id)).size).toBe(5)
    expect(after.nodes.filter((node) => node.kind === 'send' && node.parentId === nodeId('fan-5'))).toHaveLength(5)
  })

  it('wraps an existing response edge in a converter fan and adds a fresh converter branch', () => {
    const before = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')])

    const after = applyWrapWithFan(before, nodeId('root'), nodeId('s1'), 'converter', uuid)

    const fan = after.nodes.find((node) => node.id === nodeId('new-user'))
    const user = after.nodes.find((node) => node.id === nodeId('new-send'))
    const send = after.nodes.find((node) => node.id === nodeId('new-fan'))
    expect(fan?.kind).toBe('fan')
    expect(fan?.kind === 'fan' ? fan.params.axis : null).toBe('converter')
    expect(user?.kind).toBe('user_turn')
    expect(user?.parentId).toBe(nodeId('new-user'))
    expect(send?.kind).toBe('send')
    expect(send?.parentId).toBe(nodeId('new-send'))
  })
})

describe('applySetFanPromotedChild', () => {
  it('persists promotedChildSlotIndex without marking the fan dirty', () => {
    const before = mkTree('root', [
      mkRoot('root'),
      mkFan('fan', 'root', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }] }),
      mkSend('s0', 'fan'),
      mkSend('s1', 'fan'),
    ])

    const after = applySetFanPromotedChild(before, nodeId('fan'), 1)
    const fan = after.nodes.find((node) => node.id === nodeId('fan'))

    expect(fan?.state).toBe('clean')
    expect(fan?.kind === 'fan' ? fan.params.promotedChildSlotIndex : null).toBe(1)
  })
})

describe('applyPruneFanToPickedPath', () => {
  it('removes the fan and non-picked variants while preserving the picked subtree', () => {
    const before = mkTree('root', [
      mkRoot('root'),
      mkFan('fan', 'root', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }] }),
      mkSend('s0', 'fan'),
      mkUserTurn('u0', 's0', { text: 'keep me' }),
      mkSend('s0b', 'u0'),
      mkSend('s1', 'fan'),
      mkUserTurn('u1', 's1', { text: 'delete me' }),
    ], {
      edges: [
        { id: 'root->fan', parentId: nodeId('root'), childId: nodeId('fan'), slotIndex: 0 },
        { id: 'fan->s0', parentId: nodeId('fan'), childId: nodeId('s0'), slotIndex: 0 },
        { id: 's0->u0', parentId: nodeId('s0'), childId: nodeId('u0'), slotIndex: 0 },
        { id: 'u0->s0b', parentId: nodeId('u0'), childId: nodeId('s0b'), slotIndex: 0 },
        { id: 'fan->s1', parentId: nodeId('fan'), childId: nodeId('s1'), slotIndex: 1 },
        { id: 's1->u1', parentId: nodeId('s1'), childId: nodeId('u1'), slotIndex: 0 },
      ],
    })

    const after = applyPruneFanToPickedPath(before, nodeId('fan'), 0)

    expect(after.nodes.some((node) => node.id === nodeId('fan'))).toBe(false)
    expect(after.nodes.some((node) => node.id === nodeId('s1'))).toBe(false)
    expect(after.nodes.some((node) => node.id === nodeId('u1'))).toBe(false)
    expect(after.nodes.find((node) => node.id === nodeId('s0'))?.parentId).toBe(nodeId('root'))
    expect(after.nodes.some((node) => node.id === nodeId('u0'))).toBe(true)
    expect(after.nodes.some((node) => node.id === nodeId('s0b'))).toBe(true)
    expect(after.edges.some((edge) => edge.parentId === nodeId('root') && edge.childId === nodeId('s0'))).toBe(true)
  })

  it('returns the original tree when the requested fan slot does not exist', () => {
    const before = mkTree('root', [mkRoot('root'), mkFan('fan', 'root'), mkSend('s0', 'fan')], {
      edges: [
        { id: 'root->fan', parentId: nodeId('root'), childId: nodeId('fan'), slotIndex: 0 },
        { id: 'fan->s0', parentId: nodeId('fan'), childId: nodeId('s0'), slotIndex: 0 },
      ],
    })

    expect(applyPruneFanToPickedPath(before, nodeId('fan'), 99)).toBe(before)
  })
})

describe('applyDeleteSubtree', () => {
  it('deletes the selected node, descendants, and their edges', () => {
    const before = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root'),
      mkSend('s1', 'u1'),
      mkUserTurn('u2', 's1'),
      mkSend('s2', 'u2'),
    ])

    const after = applyDeleteSubtree(before, nodeId('s1'))

    expect(after.nodes.map((node) => node.id)).toEqual([nodeId('root'), nodeId('u1')])
    expect(after.edges).toHaveLength(1)
    expect(after.edges[0]).toMatchObject({ parentId: nodeId('root'), childId: nodeId('u1') })
  })

  it('does not delete the root node', () => {
    const before = tree1()
    const after = applyDeleteSubtree(before, nodeId('root'))

    expect(after).toBe(before)
  })
})

describe('applyCloneTree', () => {
  it('creates a new tree id and records parentConversationTreeId', () => {
    const before = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')], { id: 'source-tree' })

    const after = applyCloneTree(before, () => 'clone-tree')

    expect(after.id).toBe(treeId('clone-tree'))
    expect(after.parentConversationTreeId).toBe(treeId('source-tree'))
    expect(after.nodes.map((node) => node.id)).toEqual(before.nodes.map((node) => node.id))
    expect(after.edges).toEqual(before.edges)
    expect(after.nodes).not.toBe(before.nodes)
  })
})

describe('applyBranchFromNode', () => {
  it('delegates root branching to whole-tree clone semantics', () => {
    const before = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')], { id: 'source-tree' })

    const after = applyBranchFromNode(before, nodeId('root'), () => 'branch-root')

    expect(after.id).toBe(treeId('branch-root'))
    expect(after.parentConversationTreeId).toBe(treeId('source-tree'))
    expect(after.nodes.map((node) => node.id)).toEqual(before.nodes.map((node) => node.id))
  })

  it('branches from a non-root node with only the root path plus selected subtree', () => {
    const before = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root'),
      mkSend('s1', 'u1'),
      mkUserTurn('selected', 's1'),
      mkSend('selected-send', 'selected'),
      mkUserTurn('sibling', 's1'),
      mkSend('sibling-send', 'sibling'),
    ], { id: 'source-tree' })

    const after = applyBranchFromNode(before, nodeId('selected'), () => 'branch-tree')

    expect(after.id).toBe(treeId('branch-tree'))
    expect(after.parentConversationTreeId).toBe(treeId('source-tree'))
    expect(after.nodes.map((node) => node.id)).toEqual([
      nodeId('root'),
      nodeId('u1'),
      nodeId('s1'),
      nodeId('selected'),
      nodeId('selected-send'),
    ])
    expect(after.nodes.some((node) => node.id === nodeId('sibling'))).toBe(false)
    expect(after.edges.every((edge) =>
      after.nodes.some((node) => node.id === edge.parentId) &&
      after.nodes.some((node) => node.id === edge.childId),
    )).toBe(true)
  })
})
