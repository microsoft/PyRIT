// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the readiness / topological-walk layer.
 *
 * Covers (per doc/gui/design/03_runner.md §3.1 + §5.3):
 *   - `findLeafSends`: a SendNode is a leaf iff it has no SendNode descendant
 *     (UserTurn / Fan / Score descendants do NOT make a Send interior).
 *   - `computeReady`: which leaf Sends are dispatchable in this wave per the
 *     §3.1 readiness rule. Interior Sends are never in ready. Failed /
 *     cancelled SEND ancestors block the leaf (no in-wave retry amplification
 *     per rev-15 Finding 4). Edited / stale / running ancestors are in the
 *     allowlist (they'll be regenerated as part of the leaf's dispatch).
 *   - `buildSForTree` / `buildSForSubtree` / `buildSForNode`: S is the set of
 *     nodes in scope whose state is edited / stale / failed / cancelled.
 *   - `demoteRetryFailedNodes`: for a `retry_failed` wave, flip every
 *     S-member failed/cancelled node to stale + clear its execution BEFORE
 *     computeReady runs, so the ancestor allowlist admits them per §5.3.
 */

import type { ConversationTree, ConversationTreeNodeId, NodeState } from './treeTypes'
import {
  buildSForNode,
  buildSForSubtree,
  buildSForTree,
  computeReady,
  demoteRetryFailedNodes,
  findLeafSends,
} from './readiness'
import type { SinkCall } from './testHelpers'
import {
  mkEdge,
  mkExecution,
  mkFan,
  mkMockSink,
  mkRoot,
  mkSend,
  mkScore,
  mkTree,
  mkUserTurn,
  nodeId,
} from './testHelpers'

// ============================================================================
// findLeafSends
// ============================================================================

describe('findLeafSends', () => {
  it('returns the only SendNode in a single-leaf chain', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const leaves = findLeafSends(tree)
    expect(leaves.map((n) => n.id)).toEqual([nodeId('s')])
  })

  it('returns every Send under a Fan (each fan-child Send is a leaf)', () => {
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
      mkSend('s1', 'f'),
      mkSend('s2', 'f'),
      mkSend('s3', 'f'),
    ])
    const leaves = findLeafSends(tree)
    expect(leaves.map((n) => n.id).sort()).toEqual([nodeId('s1'), nodeId('s2'), nodeId('s3')].sort())
  })

  it('excludes an interior Send that has a Send descendant', () => {
    // Crescendo-style chain: Send1 → UserTurn → Send2. Send1 is interior.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r'),
      mkSend('s1', 'u1'),
      mkUserTurn('u2', 's1'),
      mkSend('s2', 'u2'),
    ])
    const leaves = findLeafSends(tree)
    expect(leaves.map((n) => n.id)).toEqual([nodeId('s2')])
  })

  it("treats a Send with a Score-only descendant as a leaf (no Send descendant)", () => {
    // ScoreNode siblings/descendants do not make a Send interior.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
      mkScore('sc', 's'),
    ])
    const leaves = findLeafSends(tree)
    expect(leaves.map((n) => n.id)).toEqual([nodeId('s')])
  })

  it("treats an orphan Send (no children at all) as a leaf", () => {
    // Per 03 §3.2: an orphan Send (Send with no children, e.g., operator
    // added a Send then deleted its UserTurn child) is itself a leaf.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const leaves = findLeafSends(tree)
    expect(leaves.map((n) => n.id)).toEqual([nodeId('s')])
  })

  it('returns empty for a tree with no SendNodes', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    expect(findLeafSends(tree)).toEqual([])
  })

  it('handles a complex tree with multiple leaf Sends at different depths', () => {
    // Two chains of different depths share a root user turn:
    //   r → u
    //       ├── f(attempt) → s_a, s_b   (depth: 2 sends in fan)
    //       └── s_chain → u_chain → s_deep   (depth: 3 chain)
    // Three leaves: s_a, s_b, s_deep. s_chain is interior.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }] }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_chain', 'u'),
      mkUserTurn('u_chain', 's_chain'),
      mkSend('s_deep', 'u_chain'),
    ])
    const leaves = findLeafSends(tree)
    expect(leaves.map((n) => n.id).sort()).toEqual(
      [nodeId('s_a'), nodeId('s_b'), nodeId('s_deep')].sort(),
    )
  })
})

// ============================================================================
// computeReady — the §3.1 readiness rule
// ============================================================================

describe('computeReady', () => {
  it('returns the leaf when its only ancestor is a clean root', () => {
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const S = new Set([nodeId('s')])
    expect(computeReady(tree, S).map((n) => n.id)).toEqual([nodeId('s')])
  })

  it('admits leaves with stale Send ancestors (they will be regenerated in the same dispatch)', () => {
    // Chain: r → u1 → s1(stale) → u2 → s2(edited). Both Sends are stale-ish;
    // s2 is the leaf. s1 is interior. The leaf is in ready because the §3.1
    // allowlist includes {edited, stale, running, clean} for ancestors.
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u1', 'r', undefined, { state: 'clean' }),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', undefined, { state: 'stale' }),
      mkSend('s2', 'u2', undefined, { state: 'edited' }),
    ])
    const S = new Set([nodeId('s1'), nodeId('s2')])
    // s1 is interior (has s2 as Send descendant), so it never enters ready.
    // s2 is the leaf with stale ancestor s1 (in allowlist) → in ready.
    expect(computeReady(tree, S).map((n) => n.id)).toEqual([nodeId('s2')])
  })

  it('blocks a leaf whose Send ancestor is failed (rev-15 Finding 4 anti-amplification)', () => {
    // s_mid failed in a previous in-wave dispatch. Its sibling-leaf descendants
    // must NOT independently retry s_mid (would amplify a single 5xx into N
    // retries against the same target).
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u1', 'r', undefined, { state: 'clean' }),
      mkSend('s_mid', 'u1', undefined, { state: 'failed' }),
      mkUserTurn('u2', 's_mid', undefined, { state: 'stale' }),
      mkSend('s_leaf', 'u2', undefined, { state: 'edited' }),
    ])
    const S = new Set([nodeId('s_mid'), nodeId('s_leaf')])
    // s_mid is interior; s_leaf is blocked by s_mid's failed state. Empty ready.
    expect(computeReady(tree, S)).toEqual([])
  })

  it('blocks a leaf whose Send ancestor is cancelled', () => {
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u1', 'r', undefined, { state: 'clean' }),
      mkSend('s_mid', 'u1', undefined, { state: 'cancelled' }),
      mkUserTurn('u2', 's_mid', undefined, { state: 'stale' }),
      mkSend('s_leaf', 'u2', undefined, { state: 'edited' }),
    ])
    const S = new Set([nodeId('s_mid'), nodeId('s_leaf')])
    expect(computeReady(tree, S)).toEqual([])
  })

  it('admits a leaf with running Send ancestor (will be added to ready when ancestor completes)', () => {
    // Per the §3.1 allowlist, 'running' ancestors are admitted: the dispatch
    // loop will re-evaluate the leaf when the ancestor completes.
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u1', 'r', undefined, { state: 'clean' }),
      mkSend('s_mid', 'u1', undefined, { state: 'running' }),
      mkUserTurn('u2', 's_mid', undefined, { state: 'stale' }),
      mkSend('s_leaf', 'u2', undefined, { state: 'edited' }),
    ])
    const S = new Set([nodeId('s_leaf')])
    expect(computeReady(tree, S).map((n) => n.id)).toEqual([nodeId('s_leaf')])
  })

  it('does NOT return interior Sends even when they are in S and unblocked', () => {
    // s1 is stale and has clean ancestors — but it's interior (has s2 below
    // it). Only s2 (the leaf) enters ready; s1 will regenerate as part of
    // s2's dispatch sequence per §3.2.
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u1', 'r', undefined, { state: 'clean' }),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', undefined, { state: 'clean' }),
      mkSend('s2', 'u2', undefined, { state: 'stale' }),
    ])
    const S = new Set([nodeId('s1'), nodeId('s2')])
    expect(computeReady(tree, S).map((n) => n.id)).toEqual([nodeId('s2')])
  })

  it('returns all leaves of a Fan when none are blocked', () => {
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkFan('f', 'u', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }] }, { state: 'clean' }),
      mkSend('s1', 'f', undefined, { state: 'edited' }),
      mkSend('s2', 'f', undefined, { state: 'edited' }),
      mkSend('s3', 'f', undefined, { state: 'edited' }),
    ])
    const S = new Set([nodeId('s1'), nodeId('s2'), nodeId('s3')])
    expect(computeReady(tree, S).map((n) => n.id).sort()).toEqual(
      [nodeId('s1'), nodeId('s2'), nodeId('s3')].sort(),
    )
  })

  it('walks transparently through Fan ancestors (Fan state ignored for readiness)', () => {
    // The §3.1 rule says "every SEND ancestor"; Fan is not a Send so its
    // state doesn't gate readiness directly. Even a stale Fan above a leaf
    // is fine as long as no SEND ancestor is failed/cancelled.
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkFan('f', 'u', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }] }, { state: 'stale' }),
      mkSend('s', 'f', undefined, { state: 'edited' }),
    ])
    const S = new Set([nodeId('s')])
    expect(computeReady(tree, S).map((n) => n.id)).toEqual([nodeId('s')])
  })

  it('walks transparently through Score ancestors', () => {
    // Same as Fan: ScoreNode is not a Send so its state doesn't gate.
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkScore('sc', 'u', undefined, { state: 'stale' }),
      mkSend('s', 'sc', undefined, { state: 'edited' }),
    ])
    const S = new Set([nodeId('s')])
    expect(computeReady(tree, S).map((n) => n.id)).toEqual([nodeId('s')])
  })

  it('returns empty when S is empty', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    expect(computeReady(tree, new Set())).toEqual([])
  })

  it('returns only leaves in S (a leaf not in S is not ready)', () => {
    // Even if a leaf is structurally ready, if it's not in S (e.g., it's
    // already `clean` and there's nothing to dispatch), it's not ready.
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkFan('f', 'u', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }] }, { state: 'clean' }),
      mkSend('s1', 'f', undefined, { state: 'clean' }),
      mkSend('s2', 'f', undefined, { state: 'edited' }),
    ])
    // Only s2 is in S; s1 is clean.
    const S = new Set([nodeId('s2')])
    expect(computeReady(tree, S).map((n) => n.id)).toEqual([nodeId('s2')])
  })
})

// ============================================================================
// buildS — S construction for the three refresh scopes
// ============================================================================

describe('buildSForTree', () => {
  it('includes every node whose state is in {edited, stale, failed, cancelled}', () => {
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u1', 'r', undefined, { state: 'edited' }),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', undefined, { state: 'failed' }),
      mkSend('s2', 'u2', undefined, { state: 'cancelled' }),
      mkUserTurn('u3', 's1', undefined, { state: 'clean' }),
      mkSend('s3', 'u3', undefined, { state: 'running' }),
      mkScore('sc', 's3', undefined, { state: 'draft' }),
    ])
    const S = buildSForTree(tree)
    // clean / running / draft are excluded.
    expect([...S].sort()).toEqual(
      [nodeId('u1'), nodeId('s1'), nodeId('u2'), nodeId('s2')].sort(),
    )
  })

  it('returns empty when every node is clean', () => {
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkSend('s', 'u', undefined, { state: 'clean' }),
    ])
    expect(buildSForTree(tree).size).toBe(0)
  })
})

describe('buildSForSubtree', () => {
  it('scopes S to the subtree rooted at the given node (subtree root included)', () => {
    // r(clean) → u_a(edited) → s_a(stale)
    //         → u_b(edited) → s_b(stale)
    // refreshSubtree(u_b) → S = {u_b, s_b}; u_a/s_a excluded.
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u_a', 'r', undefined, { state: 'edited' }),
      mkSend('s_a', 'u_a', undefined, { state: 'stale' }),
      mkUserTurn('u_b', 'r', undefined, { state: 'edited' }),
      mkSend('s_b', 'u_b', undefined, { state: 'stale' }),
    ])
    const S = buildSForSubtree(tree, nodeId('u_b'))
    expect([...S].sort()).toEqual([nodeId('u_b'), nodeId('s_b')].sort())
  })

  it('returns empty for a subtree with no in-need-of-dispatch nodes', () => {
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkSend('s', 'u', undefined, { state: 'clean' }),
    ])
    expect(buildSForSubtree(tree, nodeId('u')).size).toBe(0)
  })
})

describe('buildSForNode', () => {
  it('returns {nodeId} if the node is in scope and in {edited, stale, failed, cancelled}', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    expect([...buildSForNode(tree, nodeId('s'))]).toEqual([nodeId('s')])
  })

  it('returns empty if the node is clean', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u', undefined, { state: 'clean' }),
    ])
    expect(buildSForNode(tree, nodeId('s')).size).toBe(0)
  })
})

// ============================================================================
// demoteRetryFailedNodes — §3.1 step 2b
// ============================================================================

describe('demoteRetryFailedNodes', () => {
  it('flips every S-member failed node to stale and clears its execution', () => {
    const failedExec = mkExecution({ executionId: 'old-1', outcome: 'failure' })
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkSend('s', 'u', undefined, { state: 'failed', execution: failedExec }),
    ])
    const S = new Set([nodeId('s')])
    const { sink, callsOf } = mkMockSink()

    demoteRetryFailedNodes(tree, S, sink)

    const stateCalls = callsOf('setNodeState')
    expect(stateCalls).toHaveLength(1)
    expect(stateCalls[0].nodeId).toBe(nodeId('s'))
    expect(stateCalls[0].state).toBe<NodeState>('stale')
    // Reason must be the null sentinel (per 03 §2.2: clears lastError); omitting
    // would leave a stale error from the prior failure visible.
    expect(stateCalls[0].reason).toBeNull()

    const clearCalls = callsOf('clearExecution')
    expect(clearCalls).toHaveLength(1)
    expect(clearCalls[0].nodeId).toBe(nodeId('s'))
  })

  it('flips cancelled nodes too', () => {
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'clean' }),
      mkSend('s', 'u', undefined, { state: 'cancelled' }),
    ])
    const S = new Set([nodeId('s')])
    const { sink, callsOf } = mkMockSink()

    demoteRetryFailedNodes(tree, S, sink)

    expect(callsOf('setNodeState')[0].state).toBe<NodeState>('stale')
    expect(callsOf('clearExecution')).toHaveLength(1)
  })

  it('ignores S-member nodes that are not in {failed, cancelled}', () => {
    // S can contain edited/stale nodes too; demotion is only for failed/cancelled.
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u', 'r', undefined, { state: 'edited' }),
      mkSend('s', 'u', undefined, { state: 'stale' }),
    ])
    const S = new Set([nodeId('u'), nodeId('s')])
    const { sink, callsOf } = mkMockSink()

    demoteRetryFailedNodes(tree, S, sink)
    expect(callsOf('setNodeState')).toHaveLength(0)
    expect(callsOf('clearExecution')).toHaveLength(0)
  })

  it('ignores nodes outside S', () => {
    const failedNotInS = mkSend('s_other', 'u', undefined, { state: 'failed' })
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u', undefined, { state: 'failed' }),
      failedNotInS,
    ])
    const S = new Set([nodeId('s')])
    const { sink, callsOf } = mkMockSink()

    demoteRetryFailedNodes(tree, S, sink)

    // Only s is demoted; s_other is failed but not in S.
    const stateCalls = callsOf('setNodeState')
    expect(stateCalls.map((c) => c.nodeId)).toEqual([nodeId('s')])
  })

  it('composes with computeReady: leaves blocked by failed ancestors become ready after demotion', () => {
    // Honest composition test: build one tree, run demoteRetryFailedNodes,
    // then project the sink's setNodeState calls back onto a fresh tree copy
    // and run computeReady on the result. Without the projection step, this
    // would just be testing computeReady against a hand-rolled tree — the
    // demoter could write nonsense and the test would still pass.
    const tree = mkTree('r', [
      mkRoot('r', undefined, { state: 'clean' }),
      mkUserTurn('u1', 'r', undefined, { state: 'clean' }),
      mkSend('s_mid', 'u1', undefined, { state: 'failed' }),
      mkUserTurn('u2', 's_mid', undefined, { state: 'stale' }),
      mkSend('s_leaf', 'u2', undefined, { state: 'failed' }),
    ])
    const S = new Set([nodeId('s_mid'), nodeId('s_leaf')])

    // Pre-condition: s_leaf is blocked by s_mid's failed state.
    expect(computeReady(tree, S)).toEqual([])

    // Run the demoter against a recording sink, then project its state changes
    // back onto a copy of the tree.
    const { sink, callsOf } = mkMockSink()
    demoteRetryFailedNodes(tree, S, sink)

    const projected = projectStateChanges(tree, callsOf('setNodeState'))

    // Post-condition: composing the two surfaces the V1.0 contract — after
    // demotion runs, computeReady admits s_leaf. If the demoter wrote anything
    // other than 'stale' (typo'd state, wrong nodes), s_leaf would still be
    // blocked and the assertion would fail.
    expect(computeReady(projected, S).map((n) => n.id)).toEqual([nodeId('s_leaf')])
  })
})

/**
 * Apply a sequence of recorded setNodeState calls to a copy of the tree,
 * producing the tree the runner would see after the demoter's writes. Only
 * touches `state` — sufficient for readiness composition tests; not a general
 * projection (it doesn't clone execution or other fields touched by the sink).
 */
function projectStateChanges(
  tree: ConversationTree,
  calls: Extract<SinkCall, { method: 'setNodeState' }>[],
): ConversationTree {
  const overrides = new Map<string, NodeState>()
  for (const c of calls) {
    overrides.set(c.nodeId as string, c.state)
  }
  return {
    ...tree,
    nodes: tree.nodes.map((n) => {
      const o = overrides.get(n.id as string)
      return o === undefined ? n : { ...n, state: o }
    }),
  }
}

// ============================================================================
// Defensive cases: a tree with explicit fan slotIndex edges
// ============================================================================

describe('fan-slot-aware traversal', () => {
  it('handles a fan with explicit slotIndex edges (siblings still each appear once as leaves)', () => {
    // Smoke test: explicit edges (varying slotIndex) shouldn't double-count
    // children. findLeafSends + computeReady are edge-agnostic; they walk via
    // parentId on nodes.
    const tree = mkTree(
      'r',
      [
        mkRoot('r', undefined, { state: 'clean' }),
        mkUserTurn('u', 'r', undefined, { state: 'clean' }),
        mkFan('f', 'u', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }] }, { state: 'clean' }),
        mkSend('s0', 'f', undefined, { state: 'edited' }),
        mkSend('s1', 'f', undefined, { state: 'edited' }),
      ],
      {
        edges: [
          mkEdge('r', 'u', 0),
          mkEdge('u', 'f', 0),
          mkEdge('f', 's0', 0),
          mkEdge('f', 's1', 1),
        ],
      },
    )
    const leaves = findLeafSends(tree).map((n): ConversationTreeNodeId => n.id)
    expect(leaves.sort()).toEqual([nodeId('s0'), nodeId('s1')].sort())

    const S = new Set(leaves)
    expect(computeReady(tree, S).map((n) => n.id).sort()).toEqual(
      [nodeId('s0'), nodeId('s1')].sort(),
    )
  })
})
