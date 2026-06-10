// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `resolvePathPartition`. Pure function over a tree + leaf SendNode;
 * walks the root-to-leaf path and partitions the Sends on it into:
 *   - a clean prefix (Sends whose params still match their executions) whose
 *     turns load into `prepended_conversation` as historical context, and
 *   - a fresh suffix (the first stale Send and everything after, ending in
 *     the leaf) whose (UserTurn, FanVariant, Send) triples become the N
 *     `add_message` calls of the leaf's dispatch sequence.
 *
 * The function ALSO returns `treePathSegments` — the (axis, slotIndex) pairs
 * for every FanNode ancestor on the path — used by PR4c's `_build_labels`
 * to populate the `tree_path` label.
 */

import {
  isStaleForResolver,
  resolvePathPartition,
  rootToLeafPath,
} from './partition'
import type { ConversationTreeNodeId } from './treeTypes'
import {
  mkEdge,
  mkExecution,
  mkFan,
  mkRoot,
  mkScore,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
} from './testHelpers'

// ============================================================================
// rootToLeafPath
// ============================================================================

describe('rootToLeafPath', () => {
  it('walks from root to leaf in topo order', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r'),
      mkSend('s1', 'u1'),
      mkUserTurn('u2', 's1'),
      mkSend('s2', 'u2'),
    ])
    const path = rootToLeafPath(tree, nodeId('s2'))
    expect(path.map((n) => n.id)).toEqual([
      nodeId('r'),
      nodeId('u1'),
      nodeId('s1'),
      nodeId('u2'),
      nodeId('s2'),
    ])
  })

  it('includes Fan and Score ancestors on the path', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }] }),
      mkSend('s', 'f'),
      mkScore('sc', 's'),
    ])
    // ScoreNode is not on the leaf's ancestor chain; just confirm the path
    // includes the Fan above the leaf.
    const path = rootToLeafPath(tree, nodeId('s'))
    expect(path.map((n) => n.id)).toEqual([
      nodeId('r'),
      nodeId('u'),
      nodeId('f'),
      nodeId('s'),
    ])
  })

  it('throws when the target node is not in the tree', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    expect(() => rootToLeafPath(tree, nodeId('does-not-exist'))).toThrow(/not in tree/i)
  })
})

// ============================================================================
// isStaleForResolver — the partition's per-Send classification predicate
// ============================================================================

describe('isStaleForResolver', () => {
  it('returns true for edited / stale / failed / cancelled Sends', () => {
    for (const state of ['edited', 'stale', 'failed', 'cancelled'] as const) {
      const send = mkSend('s', 'u', undefined, { state, execution: mkExecution() })
      expect(isStaleForResolver(send)).toBe(true)
    }
  })

  it('returns true for a Send with no execution (regardless of state)', () => {
    // The doc's safety net: failed/cancelled have execution=null per §6.4.1,
    // but the predicate also catches freshly-added Sends in `draft` that have
    // not yet had an execution recorded.
    const send = mkSend('s', 'u', undefined, { state: 'draft', execution: null })
    expect(isStaleForResolver(send)).toBe(true)
  })

  it('returns false for a clean Send with an execution', () => {
    const send = mkSend('s', 'u', undefined, { state: 'clean', execution: mkExecution() })
    expect(isStaleForResolver(send)).toBe(false)
  })

  it('returns true for a running Send with no execution yet (defensive)', () => {
    // `running` should not appear as a path Send during normal dispatch
    // (the runner only walks paths for leaves picked from ready), but if
    // it does, the absence of execution means there's nothing to load
    // into prepended.
    const send = mkSend('s', 'u', undefined, { state: 'running', execution: null })
    expect(isStaleForResolver(send)).toBe(true)
  })
})

// ============================================================================
// resolvePathPartition — the core pure function
// ============================================================================

describe('resolvePathPartition', () => {
  // --------------------------------------------------------------------------
  // Simplest case: a single-Send chain
  // --------------------------------------------------------------------------

  it('single-Send chain (all-stale): root + leaf in fresh suffix, nothing prepended', () => {
    const tree = mkTree('r', [
      mkRoot('r', { text: 'hello', targetRegistryName: 'gpt-4o' }),
      mkUserTurn('u', 'r', { text: 'hi' }),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    const { prepended, freshSuffix, treePathSegments, target } = resolvePathPartition(
      tree,
      nodeId('s'),
    )

    expect(prepended).toEqual([])
    expect(freshSuffix).toHaveLength(1)
    expect(freshSuffix[0].userTurn.id).toBe(nodeId('u'))
    expect(freshSuffix[0].sendNode.id).toBe(nodeId('s'))
    expect(freshSuffix[0].fanVariant).toBeNull()
    expect(treePathSegments).toEqual([])
    expect(target).toBe('gpt-4o')
  })

  it('promotes the RootPrompt to the input UserTurn when no UserTurn sits between', () => {
    // The very-first Send of a fresh tree treats Root's text as the
    // first user turn. There's no operator-authored UserTurn between
    // Root and the first Send in this case.
    const tree = mkTree('r', [
      mkRoot('r', { text: 'how do I bake bread?', targetRegistryName: 'gpt-4o' }),
      mkSend('s', 'r', undefined, { state: 'edited' }),
    ])
    const { freshSuffix } = resolvePathPartition(tree, nodeId('s'))
    expect(freshSuffix).toHaveLength(1)
    // The userTurn is the synthesized root-as-user-turn; carries root's text.
    expect(freshSuffix[0].userTurn.role).toBe('user')
    expect(freshSuffix[0].userTurn.text).toBe('how do I bake bread?')
  })

  it('emits a leading system message when RootPrompt.systemPrompt is set', () => {
    const tree = mkTree('r', [
      mkRoot('r', { text: 'q', systemPrompt: 'You are a helpful assistant.' }),
      mkSend('s', 'r', undefined, { state: 'edited' }),
    ])
    const { prepended } = resolvePathPartition(tree, nodeId('s'))
    expect(prepended).toHaveLength(1)
    expect(prepended[0].role).toBe('system')
    expect(prepended[0].pieces[0].original_value).toBe('You are a helpful assistant.')
  })

  it('does not emit a system message when systemPrompt is absent', () => {
    const tree = mkTree('r', [
      mkRoot('r', { text: 'q' }),
      mkSend('s', 'r', undefined, { state: 'edited' }),
    ])
    const { prepended } = resolvePathPartition(tree, nodeId('s'))
    expect(prepended).toEqual([])
  })

  // --------------------------------------------------------------------------
  // Clean / fresh boundary detection
  // --------------------------------------------------------------------------

  it('all-clean upstream + edited leaf: prepends every upstream turn + assistant; leaf alone in fresh suffix', () => {
    // Chain: r → u1 → s1(clean) → u2 → s2(edited)
    // s1 is clean with a stored execution; its input UserTurn (u1) +
    // assistant response (from s1's execution) both load into prepended.
    // s2 (the leaf) is edited → in fresh suffix.
    const s1Exec = mkExecution({ executionId: 'exec-s1', pieceIds: ['piece-asst-1'] })
    const tree = mkTree('r', [
      mkRoot('r', { text: 'root', targetRegistryName: 'gpt-4o' }),
      mkUserTurn('u1', 'r', { text: 'turn 1' }),
      mkSend('s1', 'u1', undefined, { state: 'clean', execution: s1Exec }),
      mkUserTurn('u2', 's1', { text: 'turn 2' }),
      mkSend('s2', 'u2', undefined, { state: 'edited' }),
    ])
    const { prepended, freshSuffix } = resolvePathPartition(tree, nodeId('s2'))

    // Two prepended turns: user u1 + assistant response of s1.
    expect(prepended).toHaveLength(2)
    expect(prepended[0].role).toBe('user')
    expect(prepended[0].pieces[0].original_value).toBe('turn 1')
    expect(prepended[1].role).toBe('assistant')
    // The assistant message carries a reference to s1's execution pieceIds —
    // the dispatcher resolves piece content via the piece cache (PR4c).
    expect(prepended[1].pieces.map((p) => p.original_prompt_id)).toContain('piece-asst-1')

    // Leaf alone in fresh suffix.
    expect(freshSuffix).toHaveLength(1)
    expect(freshSuffix[0].userTurn.id).toBe(nodeId('u2'))
    expect(freshSuffix[0].sendNode.id).toBe(nodeId('s2'))
  })

  it('stale interior Send: prefix ends at the stale Send; fresh suffix is the interior + leaf', () => {
    // Chain: r → u1 → s1(stale) → u2 → s2(edited)
    // s1 is the first stale Send. Prepended is empty (nothing was clean
    // before s1). Fresh suffix is [s1, s2] with their respective input UTs.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r', { text: 'turn 1' }),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', { text: 'turn 2' }),
      mkSend('s2', 'u2', undefined, { state: 'edited' }),
    ])
    const { prepended, freshSuffix } = resolvePathPartition(tree, nodeId('s2'))
    expect(prepended).toEqual([])
    expect(freshSuffix.map((p) => p.sendNode.id)).toEqual([nodeId('s1'), nodeId('s2')])
    expect(freshSuffix.map((p) => p.userTurn.id)).toEqual([nodeId('u1'), nodeId('u2')])
  })

  it('clean prefix + stale interior + leaf: prefix loaded, both stales in fresh suffix', () => {
    // r → u1 → s1(clean) → u2 → s2(stale) → u3 → s3(edited)
    const s1Exec = mkExecution({ executionId: 'exec-s1', pieceIds: ['p1'] })
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r', { text: 't1' }),
      mkSend('s1', 'u1', undefined, { state: 'clean', execution: s1Exec }),
      mkUserTurn('u2', 's1', { text: 't2' }),
      mkSend('s2', 'u2', undefined, { state: 'stale' }),
      mkUserTurn('u3', 's2', { text: 't3' }),
      mkSend('s3', 'u3', undefined, { state: 'edited' }),
    ])
    const { prepended, freshSuffix } = resolvePathPartition(tree, nodeId('s3'))

    expect(prepended).toHaveLength(2) // u1 + s1 assistant response
    expect(freshSuffix.map((p) => p.sendNode.id)).toEqual([nodeId('s2'), nodeId('s3')])
  })

  it('a clean Send with state=failed (defensive: per §6.4.1, failed has execution=null) goes to fresh suffix', () => {
    // Defensive: even if some buggy path left execution set on a failed Send,
    // the resolver treats failed as fresh (the predicate's state-set check).
    // This guarantees retries always re-dispatch failed nodes.
    const stale = mkExecution({ executionId: 'old' })
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 't' }),
      mkSend('s', 'u', undefined, { state: 'failed', execution: stale }),
    ])
    const { prepended, freshSuffix } = resolvePathPartition(tree, nodeId('s'))
    expect(prepended).toEqual([])
    expect(freshSuffix).toHaveLength(1)
  })

  // --------------------------------------------------------------------------
  // Fan / Score transparency — the §5.1 #5 invariant on the path-walk side
  // --------------------------------------------------------------------------

  it('Fan(attempt) above a Send: variant carries (axis, slot); UserTurn is taken from ABOVE the Fan', () => {
    // r → u → f(attempt, n=3) → s_a (slot 0), s_b (slot 1), s_c (slot 2)
    // Walking the path to s_b: [r, u, f, s_b]. The Send's input UserTurn is
    // u (above the Fan); the variant carries ('attempt', 1) per s_b's slot.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 'shared input' }),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f', undefined, { state: 'edited' }),
      mkSend('s_b', 'f', undefined, { state: 'edited' }),
      mkSend('s_c', 'f', undefined, { state: 'edited' }),
    ])

    const { freshSuffix, treePathSegments } = resolvePathPartition(tree, nodeId('s_b'))
    expect(freshSuffix).toHaveLength(1)
    expect(freshSuffix[0].userTurn.id).toBe(nodeId('u')) // shared UT from above the Fan
    expect(freshSuffix[0].sendNode.id).toBe(nodeId('s_b'))
    expect(freshSuffix[0].fanVariant).toEqual({ axis: 'attempt', slotIndex: 1 })
    expect(treePathSegments).toEqual([['attempt', 1]])
  })

  it('Fan(converter) above a per-child UserTurn: tree_path captures the fan; variant rests on the child UT (not on freshSuffix.fanVariant)', () => {
    // Path: r → u_above → f(converter) → u_child_1 → s_1
    // The operator-authored fan-child UserTurn (u_child_1) carries the
    // variant's converter pipeline in its params (materialized at Fan-
    // creation time per 01 §4.4 "Fan children are materialized in the
    // conversation tree"). The Send's input UserTurn IS u_child_1, and
    // freshSuffix.fanVariant is null because the variant data lives on the
    // child UT — there's no shared input from above the fan to vary by slot.
    //
    // What persists is `tree_path`: the Fan ancestor is recorded so the
    // wave's leaf AR can round-trip the fan structure for reconstruction.
    const tree = mkTree(
      'r',
      [
        mkRoot('r'),
        mkUserTurn('u_above', 'r', { text: 'q' }),
        mkFan('f', 'u_above', {
          axis: 'converter',
          variants: [
            { axis: 'converter', payload: { converters: [{ converterId: 'base64' }] } },
            { axis: 'converter', payload: { converters: [{ converterId: 'rot13' }] } },
          ],
        }),
        mkUserTurn('u_child_0', 'f', { text: 'q', converterPipeline: [{ converterId: 'base64' }] }),
        mkSend('s_0', 'u_child_0', undefined, { state: 'edited' }),
        mkUserTurn('u_child_1', 'f', { text: 'q', converterPipeline: [{ converterId: 'rot13' }] }),
        mkSend('s_1', 'u_child_1', undefined, { state: 'edited' }),
      ],
      {
        edges: [
          mkEdge('r', 'u_above', 0),
          mkEdge('u_above', 'f', 0),
          mkEdge('f', 'u_child_0', 0),
          mkEdge('f', 'u_child_1', 1),
          mkEdge('u_child_0', 's_0', 0),
          mkEdge('u_child_1', 's_1', 0),
        ],
      },
    )

    const { freshSuffix, treePathSegments } = resolvePathPartition(tree, nodeId('s_1'))
    expect(freshSuffix).toHaveLength(1)
    // Input UT is the per-child fan UserTurn.
    expect(freshSuffix[0].userTurn.id).toBe(nodeId('u_child_1'))
    // No fan_variant on the FreshSuffixEntry: variant data lives on the
    // child UT's converterPipeline (which the dispatcher reads directly).
    expect(freshSuffix[0].fanVariant).toBeNull()
    // tree_path still captures the fan ancestor for label round-trip.
    expect(treePathSegments).toEqual([['converter', 1]])
    // Sanity: the child UT carries the variant's converter pipeline.
    if (freshSuffix[0].userTurn.role !== undefined && 'params' in (freshSuffix[0].userTurn as object)) {
      // narrow to real UserTurnNode (not synthetic)
      const ut = freshSuffix[0].userTurn as Extract<typeof freshSuffix[0]['userTurn'], { params: unknown }>
      expect(ut.params.converterPipeline).toEqual([{ converterId: 'rot13' }])
    }
  })

  it('Score ancestor on the path is transparent (does not consume pending UserTurn or break variant)', () => {
    // r → u → sc → s
    // The Score node passes through; the Send's input is u with no variant.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { text: 'q' }),
      mkScore('sc', 'u'),
      mkSend('s', 'sc', undefined, { state: 'edited' }),
    ])
    const { freshSuffix, treePathSegments } = resolvePathPartition(tree, nodeId('s'))
    expect(freshSuffix).toHaveLength(1)
    expect(freshSuffix[0].userTurn.id).toBe(nodeId('u'))
    expect(freshSuffix[0].fanVariant).toBeNull()
    expect(treePathSegments).toEqual([])
  })

  it('nested fans accumulate tree_path segments in topo order', () => {
    // r → u → f_outer(prompt, n=2 [a, b]) → u_mid_a → f_inner(attempt, n=2) → s
    // Walking to s in (outer=a slot 0, inner=slot 1):
    // tree_path = [(prompt, 0), (attempt, 1)]
    const tree = mkTree(
      'r',
      [
        mkRoot('r'),
        mkUserTurn('u', 'r', { text: 'q' }),
        mkFan('f_outer', 'u', {
          axis: 'prompt',
          variants: [
            { axis: 'prompt', payload: { text: 'alt-a' } },
            { axis: 'prompt', payload: { text: 'alt-b' } },
          ],
        }),
        mkUserTurn('u_mid_a', 'f_outer', { text: 'alt-a' }),
        mkUserTurn('u_mid_b', 'f_outer', { text: 'alt-b' }),
        mkFan('f_inner_a', 'u_mid_a', {
          axis: 'attempt',
          variants: [
            { axis: 'attempt', payload: {} },
            { axis: 'attempt', payload: {} },
          ],
        }),
        mkSend('s_0', 'f_inner_a', undefined, { state: 'edited' }),
        mkSend('s_1', 'f_inner_a', undefined, { state: 'edited' }),
      ],
      {
        edges: [
          mkEdge('r', 'u', 0),
          mkEdge('u', 'f_outer', 0),
          mkEdge('f_outer', 'u_mid_a', 0),
          mkEdge('f_outer', 'u_mid_b', 1),
          mkEdge('u_mid_a', 'f_inner_a', 0),
          mkEdge('f_inner_a', 's_0', 0),
          mkEdge('f_inner_a', 's_1', 1),
        ],
      },
    )

    const { treePathSegments } = resolvePathPartition(tree, nodeId('s_1'))
    expect(treePathSegments).toEqual([
      ['prompt', 0],
      ['attempt', 1],
    ])
  })

  // --------------------------------------------------------------------------
  // Target resolution: inherited from RootPrompt unless overridden
  // --------------------------------------------------------------------------

  it('SendNode target override wins over the root target', () => {
    const tree = mkTree('r', [
      mkRoot('r', { targetRegistryName: 'gpt-4o' }),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u', { targetRegistryName: 'claude-3.5-sonnet' }, { state: 'edited' }),
    ])
    expect(resolvePathPartition(tree, nodeId('s')).target).toBe('claude-3.5-sonnet')
  })

  it('SendNode without target override inherits from root', () => {
    const tree = mkTree('r', [
      mkRoot('r', { targetRegistryName: 'llama-3-70b' }),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u', undefined, { state: 'edited' }),
    ])
    expect(resolvePathPartition(tree, nodeId('s')).target).toBe('llama-3-70b')
  })

  // --------------------------------------------------------------------------
  // Preconditions / error handling
  // --------------------------------------------------------------------------

  it('throws when the target node is not a SendNode', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    expect(() => resolvePathPartition(tree, nodeId('u'))).toThrow(/leaf send/i)
  })

  it('throws when the target SendNode is not actually a leaf', () => {
    // s1 has s2 as a Send descendant → not a leaf. The runner's dispatch
    // loop never calls the resolver for interior Sends, but enforce the
    // precondition so a buggy caller fails loudly.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r'),
      mkSend('s1', 'u1'),
      mkUserTurn('u2', 's1'),
      mkSend('s2', 'u2'),
    ])
    expect(() => resolvePathPartition(tree, nodeId('s1'))).toThrow(/leaf send/i)
  })
})

// ============================================================================
// tree_path segment shape (used by PR4c's _build_labels)
// ============================================================================

describe('tree_path segments', () => {
  it('produces JSON-serializable array of [axis, slotIndex] pairs', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }],
      }),
      mkSend('s_0', 'f', undefined, { state: 'edited' }),
      mkSend('s_1', 'f', undefined, { state: 'edited' }),
    ])
    const { treePathSegments } = resolvePathPartition(tree, nodeId('s_1'))
    const json = JSON.stringify(treePathSegments)
    expect(json).toBe('[["attempt",1]]')
    // Round-trip equality.
    expect(JSON.parse(json)).toEqual([['attempt', 1]])
  })

  it('is `[]` for a leaf with no fan ancestors', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u', undefined, { state: 'edited' })])
    const { treePathSegments } = resolvePathPartition(tree, nodeId('s'))
    expect(JSON.stringify(treePathSegments)).toBe('[]')
  })
})

// ============================================================================
// Helper: typed access to FreshSuffixEntry so tests stay terse
// ============================================================================

// Re-declared inline here as a sanity check the public API exposes the right
// shape (would fail to compile if the partition module changes the names).
type _AssertNodeIdShape = ConversationTreeNodeId extends string ? true : never
const _shape: _AssertNodeIdShape = true
void _shape
