// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `estimateRefreshCost` — the pure pre-dispatch cost estimator
 * shared by the runner shim's modal-gate AND the action rail's per-button
 * cost-preview tooltip (per PR5 reviewer rev-18 Finding D.3 + PR6b).
 *
 * Identical semantics to the shim's local `estimateCalls`: sum the leaf
 * dispatch sequences (1 `create_attack` + N `add_message`s where N =
 * count of stale Sends on the leaf's root-to-leaf path). Adds the leaf
 * count to the result so the tooltip can show "≈N calls, M leaves".
 */

import { estimateRefreshCost } from './estimateRefreshCost'
import { buildSForSubtree, buildSForTree } from './readiness'
import {
  mkFan,
  mkRoot,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
} from './testHelpers'
import type { ConversationTreeNode } from './treeTypes'

describe('estimateRefreshCost', () => {
  it('returns 0 calls, 0 leaves on empty S', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u', undefined, { state: 'clean' })])
    const S = new Set<ReturnType<typeof nodeId>>()
    expect(estimateRefreshCost(tree, S)).toEqual({ calls: 0, leaves: 0 })
  })

  it('single stale leaf with a 1-stale-Send path → 2 calls (1 create_attack + 1 add_message), 1 leaf', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u', undefined, { state: 'stale' }),
    ])
    const S = buildSForTree(tree)
    expect(estimateRefreshCost(tree, S)).toEqual({ calls: 2, leaves: 1 })
  })

  it('chain of 3 stale Sends → 4 calls (1 create_attack + 3 add_messages), 1 leaf', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r'),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1'),
      mkSend('s2', 'u2', undefined, { state: 'stale' }),
      mkUserTurn('u3', 's2'),
      mkSend('s3', 'u3', undefined, { state: 'stale' }),
    ])
    const S = buildSForTree(tree)
    expect(estimateRefreshCost(tree, S)).toEqual({ calls: 4, leaves: 1 })
  })

  it('fan of 3 stale Sends, all clean upstream → 6 calls (3 leaves × 2), 3 leaves', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f', undefined, { state: 'stale' }),
      mkSend('s_b', 'f', undefined, { state: 'stale' }),
      mkSend('s_c', 'f', undefined, { state: 'stale' }),
    ])
    const S = buildSForTree(tree)
    expect(estimateRefreshCost(tree, S)).toEqual({ calls: 6, leaves: 3 })
  })

  it('60-leaf attempt-fan with 10-deep shared stale prefix → 720 calls, 60 leaves', () => {
    // Build a 10-deep chain of stale Sends, then a fan with 60 children
    // (also stale). Each leaf's path includes all 10 chain Sends + the
    // leaf itself = 11 stale Sends; each leaf dispatch fires
    // 1 create_attack + 11 add_messages = 12 calls.
    // 60 leaves × 12 = 720 calls. Spec's prose example of "600 calls"
    // (01 §1.2) counted add_messages only; the runner counts the full
    // dispatch sequence including the create_attack starter.
    const nodes: ConversationTreeNode[] = [mkRoot('r'), mkUserTurn('u0', 'r')]
    let parent = 'u0'
    for (let i = 0; i < 10; i++) {
      const sendId = `s${i}`
      const turnId = `t${i}`
      nodes.push(mkSend(sendId, parent, undefined, { state: 'stale' }))
      nodes.push(mkUserTurn(turnId, sendId))
      parent = turnId
    }
    const variants = Array.from({ length: 60 }, () => ({ axis: 'attempt' as const, payload: {} }))
    nodes.push(mkFan('f', parent, { axis: 'attempt', variants }))
    for (let i = 0; i < 60; i++) {
      nodes.push(mkSend(`leaf${i}`, 'f', undefined, { state: 'stale' }))
    }
    const tree = mkTree('r', nodes)
    const S = buildSForTree(tree)
    expect(estimateRefreshCost(tree, S)).toEqual({ calls: 720, leaves: 60 })
  })

  it('subtree-scoped S excludes leaves outside the subtree', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u_left', 'r'),
      mkSend('s_left', 'u_left', undefined, { state: 'stale' }),
      mkUserTurn('u_right', 'r'),
      mkSend('s_right', 'u_right', undefined, { state: 'stale' }),
    ])
    // Refresh-subtree from u_left should only count s_left's dispatch.
    const S = buildSForSubtree(tree, nodeId('u_left'))
    expect(estimateRefreshCost(tree, S)).toEqual({ calls: 2, leaves: 1 })
  })

  it('clean leaves in the tree are excluded — count only what would dispatch', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_clean', 'f', undefined, { state: 'clean' }),
      mkSend('s_stale', 'f', undefined, { state: 'stale' }),
    ])
    const S = buildSForTree(tree)
    // Only s_stale dispatches — 1 create_attack + 1 add_message = 2 calls.
    expect(estimateRefreshCost(tree, S)).toEqual({ calls: 2, leaves: 1 })
  })

  it('result is pure — does not mutate the input tree or S set', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u', undefined, { state: 'stale' })])
    const S = buildSForTree(tree)
    const sBefore = new Set(S)
    const nodesBefore = tree.nodes
    estimateRefreshCost(tree, S)
    expect(tree.nodes).toBe(nodesBefore)
    expect([...S]).toEqual([...sBefore])
  })
})
