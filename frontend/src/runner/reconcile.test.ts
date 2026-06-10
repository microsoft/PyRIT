// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `reconcileAllTransforms` — the wave-end pass per 03 §3.1 step 6
 * + §3.3a. Walks every transform-class node (UserTurn / Fan / Score) once
 * and flips `stale → clean` when every parent is `clean`. Catches the
 * operator-typical case of ScoreNodes attached as SIBLINGS of a Send (not
 * on a dispatched leaf's path), which the per-dispatch
 * `reconcileTransformStates` cannot reach.
 *
 * Pure tree-walker with one sink mutation surface (`setNodeState`); tests
 * mock the sink and assert the exact transitions fired.
 */

import { reconcileAllTransforms } from './reconcile'
import {
  mkFan,
  mkMockSink,
  mkRoot,
  mkScore,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
  treeId,
} from './testHelpers'

describe('reconcileAllTransforms', () => {
  it('flips a stale UserTurn whose only parent is clean to clean', () => {
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'stale' }),
      ],
      { id: 't-1' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-1'), sink)

    const calls = callsOf('setNodeState')
    expect(calls).toHaveLength(1)
    expect(calls[0].nodeId).toBe(nodeId('u'))
    expect(calls[0].state).toBe('clean')
    expect(calls[0].treeId).toBe(treeId('t-1'))
  })

  it('flips a stale ScoreNode sibling of a Send when its parent is clean', () => {
    // The operator-typical placement: r → u → s_send AND r → u → score(stale).
    // The per-dispatch reconcileTransformStates only walks the leaf's path,
    // missing the sibling ScoreNode. The wave-end pass catches it.
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkSend('s_send', 'u', undefined, { state: 'clean' }),
        mkScore('score', 'u', undefined, { state: 'stale' }),
      ],
      { id: 't-2' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-2'), sink)

    const calls = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('score'))
    expect(calls).toHaveLength(1)
    expect(calls[0].state).toBe('clean')
  })

  it('flips a stale FanNode whose only parent is clean to clean', () => {
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkFan('f', 'u', undefined, { state: 'stale' }),
      ],
      { id: 't-3' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-3'), sink)

    const calls = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('f'))
    expect(calls).toHaveLength(1)
    expect(calls[0].state).toBe('clean')
  })

  it('does NOT flip a SendNode (only transforms reconcile here)', () => {
    // Send-state transitions are owned by the dispatcher; the reconciler
    // must not interfere or it would race recordExecution/setNodeState.
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkSend('s', 'u', undefined, { state: 'stale' }),
      ],
      { id: 't-4' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-4'), sink)
    expect(callsOf('setNodeState')).toHaveLength(0)
  })

  it('does NOT flip a transform whose parent is still stale', () => {
    // r(clean) → u(stale) → score(stale). u is stale, so score's parent is
    // not all-clean; score stays stale.
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'stale' }),
        mkScore('score', 'u', undefined, { state: 'stale' }),
      ],
      { id: 't-5' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-5'), sink)

    // u CAN flip (its parent r is clean). score CANNOT flip in this single
    // pass (its parent u is still stale at scan time). A second pass would
    // catch it; the wave-end caller fires only once per wave per spec.
    const calls = callsOf('setNodeState')
    expect(calls.map((c) => c.nodeId)).toEqual([nodeId('u')])
  })

  it('does NOT flip a transform whose parent is failed', () => {
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkSend('s_failed', 'u', undefined, { state: 'failed' }),
        mkScore('score', 's_failed', undefined, { state: 'stale' }),
      ],
      { id: 't-6' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-6'), sink)
    expect(callsOf('setNodeState')).toHaveLength(0)
  })

  it('idempotent: clean transforms generate no sink calls', () => {
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkScore('score', 'u', undefined, { state: 'clean' }),
      ],
      { id: 't-7' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-7'), sink)
    expect(callsOf('setNodeState')).toHaveLength(0)
  })

  it('handles a transform with multiple parents (only flips when ALL are clean)', () => {
    // Per the spec, parents (plural) — a node's `parentId` is singular in our
    // model so the spec's "all parents clean" reduces to "the one parent is
    // clean." Cover the existing single-parent case for completeness; cross-
    // tree DAG support is V2.
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkScore('score', 'u', undefined, { state: 'stale' }),
      ],
      { id: 't-8' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-8'), sink)
    expect(callsOf('setNodeState')).toHaveLength(1)
  })

  it('ignores nodes with edited state (operator just edited; not the reconciler\'s concern)', () => {
    // `edited` is a transient pre-wave state — the §6.3 propagation rules
    // re-stale descendants but the edited node itself is operator-intent.
    // The reconciler only flips `stale → clean`; it must not touch `edited`.
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'edited' }),
      ],
      { id: 't-9' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-9'), sink)
    expect(callsOf('setNodeState')).toHaveLength(0)
  })

  it('walks the whole tree (not just one path) — catches sibling transforms', () => {
    // A wide tree where the wave dispatched only s_a. Without whole-tree
    // walk, s_b's score sibling (stale, all-ancestors-clean) would stay
    // stuck stale after the wave settles.
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkFan('f', 'u', undefined, { state: 'clean' }),
        mkSend('s_a', 'f', undefined, { state: 'clean' }),
        mkSend('s_b', 'f', undefined, { state: 'clean' }),
        mkScore('score_b', 's_b', undefined, { state: 'stale' }),
      ],
      { id: 't-10' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-10'), sink)
    const calls = callsOf('setNodeState').filter((c) => c.nodeId === nodeId('score_b'))
    expect(calls).toHaveLength(1)
    expect(calls[0].state).toBe('clean')
  })

  it('handles a transform that is the tree root with no parents (vacuously all-clean → flips)', () => {
    // Edge case: a UserTurn at the root (no actual parent) is "all-parents-
    // clean" vacuously. Flip it to clean.
    const tree = mkTree(
      'u',
      [mkUserTurn('u', null as unknown as string, undefined, { state: 'stale' })],
      { id: 't-11' },
    )
    // Replace the parentId with a true null (mkUserTurn requires a string;
    // we need a root-positioned transform).
    const patched = {
      ...tree,
      nodes: tree.nodes.map((n) => (n.id === nodeId('u') ? { ...n, parentId: null } : n)),
    }
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(patched, treeId('t-11'), sink)
    expect(callsOf('setNodeState')).toHaveLength(1)
    expect(callsOf('setNodeState')[0].state).toBe('clean')
  })

  it('does not touch other sink methods (only setNodeState)', () => {
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'stale' }),
      ],
      { id: 't-12' },
    )
    const { sink, callsOf } = mkMockSink()
    reconcileAllTransforms(tree, treeId('t-12'), sink)
    expect(callsOf('clearExecution')).toHaveLength(0)
    expect(callsOf('recordExecution')).toHaveLength(0)
    expect(callsOf('emitWaveEvent')).toHaveLength(0)
    expect(callsOf('setReflogPinned')).toHaveLength(0)
  })
})
